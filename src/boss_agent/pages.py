import contextlib
import hashlib
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from rich.console import Console

from droid_agent_core.gestures import (
    HumanizedGestureExecutor,
    Point,
    calculate_probe_coordinate,
)
from droid_agent_core.locators import (
    LocatorRegistry,
    UISelector,
    get_global_locator_registry,
    wait_until,
)

from .card_parser import CardFacets, ParsedCard, needs_text_fallback, parse_card
from .models import (
    PLATFORM_BADGE_MARKERS,
    AuthStatus,
    ChatButtonState,
    FilterConfig,
    JobCardBrief,
    JobPosting,
    classify_chat_button,
)
from .rejection import DISINTEREST_REASON

logger = logging.getLogger("boss_agent.pages")
ui_logger = logging.getLogger("droid_agent_core.ui")
console = Console()

# Jittered hardware-Back cadence for search/home recovery. Deliberately a range,
# not a fixed interval: a perfectly regular Back rhythm is exactly the timing
# signature the humanized-interaction policy (ADR-0005) exists to avoid.
BACK_INTERVAL_SEC: tuple[float, float] = (0.35, 0.65)

# Commute distance widget on the job detail page (spec #209): "距离家庭住址19.5千米",
# "距住址12km", "距离家庭住址800米". Sub-kilometre units (米/m) convert to km.
COMMUTE_DISTANCE_PATTERN = re.compile(
    r"(?:距离|距)[^0-9]*([0-9]+(?:\.[0-9]+)?)\s*(千米|公里|米|km|m)",
    re.IGNORECASE,
)

#: Swipes the commute probe may spend before failing open (spec #209, ticket #263). An
#: expanded JD is often 3,000-6,000+ characters and pushes `home_tip_vf` several screens
#: below the fold, so the original three-swipe budget ran out on exactly the
#: comprehensive postings the distance ceiling exists to screen.
COMMUTE_PROBE_MAX_SCROLLS = 6

#: Fraction of the viewport covered per probe swipe. 55% covers noticeably more ground per
#: gesture than the original 40% while staying within safe bounds: ``_scroll_page_up``
#: clamps the gesture to end no higher than 15% of the screen height, so this stride still
#: trails off well below the status bar and the detail page's own header.
COMMUTE_PROBE_SCROLL_STRIDE_RATIO = 0.55

#: Consecutive swipes that must leave the anchor exactly where it was before the probe
#: accepts that it has reached the scroll container's end. One is not enough: an anchor that
#: has scrolled just above the viewport can report a clamped, unchanging position for a
#: swipe while the page is still moving, and calling "bottom" there would end the search
#: early on precisely the long expanded JDs this probe exists for.
COMMUTE_PROBE_STALLED_SWIPES = 2

#: Separator in a communication card's `[Company] | [Position]` descriptor.
CARD_DESCRIPTOR_SEPARATOR: str = "|"

#: Fullwidth vertical bar (U+FF5C). The divider is a rendered glyph rather than a
#: protocol value, so both forms are accepted: a build or font that emits the
#: fullwidth bar would otherwise yield no employer on any card, and the blacklist
#: would silently never be populated.
FULLWIDTH_CARD_DESCRIPTOR_SEPARATOR: str = "｜"

#: Locator key of the on-screen back affordance tried before the hardware Back key.
BACK_BUTTON_KEY: str = "communication_list.back_btn"

#: XPath to a card's text nodes, the fallback for fields the platform leaves unlabelled.
CARD_CHILD_TEXT_XPATH: str = ".//android.widget.TextView"

#: Bounded recovery steps the 仅沟通 list navigation may spend unwinding the app.
LIST_RECOVERY_MAX_STEPS: int = 6


def _normalize_card_descriptors(text: str) -> str:
    """Collapse fullwidth vertical bars onto the ASCII card-descriptor separator."""
    return (text or "").replace(FULLWIDTH_CARD_DESCRIPTOR_SEPARATOR, CARD_DESCRIPTOR_SEPARATOR)


#: Badge texts the platform renders in `iv_msg_status` for a thread whose last
#: message is the candidate's own or an unsent draft (spec #205). Matched explicitly
#: rather than keyed on the node's mere presence: a future badge with different wording
#: would otherwise silently stop every rejection from being detected.
OUTBOUND_STATUS_MARKERS: tuple[str, ...] = ("送达", "已读", "草稿")


def _log_selector_lookup(selector: UISelector, outcome: str, started_at: float) -> None:
    """Emit one UI telemetry line for a single selector query."""
    ui_logger.debug(
        "[UI] find key=%s by=%s selector=%.160s -> %s in %.2fs",
        selector.description or "-",
        selector.by.value,
        selector.value,
        outcome,
        time.monotonic() - started_at,
    )


def _log_info(msg: str) -> None:
    logger.info(msg)
    console.print(f"[cyan][JobDetailPage][/cyan] {msg}")


def _log_warn(msg: str) -> None:
    logger.warning(msg)
    console.print(f"[yellow][JobDetailPage ⚠️][/yellow] {msg}")


def _log_error(msg: str) -> None:
    logger.error(msg)
    console.print(f"[bold red][JobDetailPage ❌][/bold red] {msg}")


@dataclass
class LocatedJobCard:
    """A parsed card brief together with the element it was read from.

    The element is an opaque device handle; it crosses to the feed pipeline only for
    viewport geometry and the detail-page tap. Everything else consumes the brief,
    which is pure domain data — the same shape Chat Triage proved with
    ``CommunicationCard``.
    """

    card: JobCardBrief
    element: Any = None


def _whitespace_digest(text: str) -> str:
    normalized = re.sub(r"\s+", "", text or "")
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]


def compute_card_key(
    sender_name: str,
    message_text: str,
    row_text: str = "",
    descriptor: str = "",
) -> str:
    """Signature identifying one communication card for visited-set deduplication.

    Sender, the card's employer descriptor, and a whitespace-insensitive hash of the
    message text: stable across re-reads of the same card, and distinct for two
    recruiters sending identical text.

    The descriptor earns its place because platform rejection templates are canned
    and generic sender names ('李女士', 'HR', '招聘负责人') repeat across employers:
    sender plus text alone collapses two different companies onto one key, and the
    later card is skipped as already-visited -- silently costing it its blacklist
    entry, which is the one durable action this path takes.

    When the sender node cannot be read, the row's own rendered text stands in for
    it. Falling back to a constant would collapse identical rejection templates
    from different recruiters onto one key, silently skipping the later ones.
    """
    sender = re.sub(r"\s+", "", sender_name or "").strip()
    if not sender:
        source = re.sub(r"\s+", "", row_text or "") or re.sub(r"\s+", "", message_text or "")
        sender = f"row-{hashlib.sha1(source.encode('utf-8')).hexdigest()[:8]}"
    employer = _normalize_card_descriptors(descriptor)
    return f"{sender}:{_whitespace_digest(employer)}:{_whitespace_digest(message_text)}"


def parse_company_from_descriptor(descriptor: str, sender_name: str = "") -> str:
    """Extract the employer from a card's `[Company] | [Position]` descriptor.

    Examples:
        "传音控股 | 算法工程师"        -> "传音控股"
        "严胜 传音控股 | 算法工程师"   -> "传音控股"   (sender_name="严胜")
        "小米集团 | 算法工程师"        -> "小米集团"   (sender_name="小米")
        "我们感谢您的投递"            -> ""            (no separator)

    Anything after the first separator is the position and is discarded: only the
    employer is ever blacklisted.

    A leading recruiter name is stripped because the row-text fallback can glue it
    onto the descriptor -- but only when a delimiter actually separates the two.
    This value feeds a substring-matched *global* blacklist, so a sender whose
    nickname merely prefixes the employer must leave it intact: stripping "小米"
    off "小米集团" leaves the generic token "集团", which would then reject every
    集团 employer in the country. An undelimited prefix is left alone deliberately:
    the worst case is an employer name that matches nothing, against a worst case of
    blacklisting an entire class of employers.
    """
    text = _normalize_card_descriptors(descriptor).strip()
    if CARD_DESCRIPTOR_SEPARATOR not in text:
        return ""

    company = text.split(CARD_DESCRIPTOR_SEPARATOR, 1)[0].strip()
    sender = (sender_name or "").strip()
    if sender and company != sender:
        glued = re.match(rf"^{re.escape(sender)}[\s·•・:：\-—－]+(?P<employer>.+)$", company)
        if glued:
            company = glued.group("employer").strip()
    return company


@dataclass
class CommunicationCard:
    """One conversation card read from the 仅沟通 communication list.

    Everything the triage needs is on the card itself: the outbound indicator
    says whether the thread is waiting on the recruiter, and the descriptor
    carries the employer.
    """

    sender_name: str
    message_text: str
    element: Any = None
    row_text: str = ""
    key: str = ""
    #: Raw Outbound Message Indicator text (`iv_msg_status`), e.g. "[送达]".
    outbound_status: str = ""
    #: The card's `[Company] | [Position]` descriptor, e.g. "传音控股 | 算法工程师".
    company_position: str = ""

    def __post_init__(self) -> None:
        if not self.key:
            self.key = compute_card_key(
                self.sender_name,
                self.message_text,
                self.row_text,
                descriptor=self.company_position,
            )

    @property
    def has_outbound_indicator(self) -> bool:
        """Whether the candidate sent the last message and the recruiter has not replied.

        True only for a badge carrying a known marker (``[送达]`` / ``[已读]``). An
        unrecognised badge is treated as an inbound message instead of being
        skipped: a missed skip costs one LLM call, while a wrong skip would stop
        every rejection from ever being blacklisted, silently and invisibly.
        """
        status = (self.outbound_status or "").strip()
        return any(marker in status for marker in OUTBOUND_STATUS_MARKERS)

    @property
    def company_name(self) -> str:
        """Employer parsed from the card descriptor, or "" when unavailable."""
        return parse_company_from_descriptor(self.company_position, self.sender_name)


class BaseBossPage:
    """Base class for all Boss 直聘 Page Objects using key-based locator resolution."""

    BOSS_PACKAGE_NAME: str = "com.hpbr.bosszhipin"

    def __init__(self, driver: Any, locator_registry: LocatorRegistry | None = None):
        self.driver = driver
        self.gestures = HumanizedGestureExecutor(driver)
        self.locators = locator_registry or get_global_locator_registry()

    def activate_app(self, package_name: str = BOSS_PACKAGE_NAME) -> bool:
        """Ensure the Boss application is active and in foreground."""
        if hasattr(self.driver, "activate_app"):
            try:
                self.driver.activate_app(package_name)
                return True
            except Exception:
                pass
        return False

    def _get_window_size(self) -> dict[str, int]:
        """Get the current screen window dimensions with safe default fallback."""
        if hasattr(self.driver, "get_window_size"):
            try:
                size = self.driver.get_window_size()
                if isinstance(size, dict) and "width" in size and "height" in size:
                    w = int(size["width"])
                    h = int(size["height"])
                    if w > 100 and h > 100:
                        return {"width": w, "height": h}
            except Exception:
                pass
        return {"width": 1080, "height": 2400}

    def _find_by_selectors(self, selectors: list[UISelector]):
        if not self.driver or not selectors:
            return None
        for sel in selectors:
            started_at = time.monotonic()
            try:
                elems = self.driver.find_elements(by=sel.by.value, value=sel.value)
            except Exception as exc:
                _log_selector_lookup(sel, f"error:{exc}", started_at)
                continue
            _log_selector_lookup(sel, "match" if elems else "none", started_at)
            if elems:
                return elems[0]
        return None

    def _find_elements_by_key(
        self, key: str, format_args: dict[str, Any] | None = None
    ) -> list[Any]:
        """Return every element matched by the first locator for `key` that hits anything."""
        if not self.driver:
            return []
        for sel in self.locators.get_selectors(key, format_args=format_args):
            started_at = time.monotonic()
            try:
                elems = self.driver.find_elements(by=sel.by.value, value=sel.value)
            except Exception as exc:
                _log_selector_lookup(sel, f"error:{exc}", started_at)
                continue
            _log_selector_lookup(sel, "match" if elems else "none", started_at)
            if elems:
                return list(elems)
        return []

    def _extract_card_field_text(self, card_elem: Any, key: str) -> str:
        """Extract text from a sub-element inside a list card using configured selectors."""
        selectors = self.locators.get_selectors(key)
        for sel in selectors:
            try:
                if sel.by.value == "id":
                    elems = card_elem.find_elements(by="id", value=sel.value)
                elif sel.by.value == "xpath":
                    val = sel.value
                    if not val.startswith("."):
                        val = "." + val
                    elems = card_elem.find_elements(by="xpath", value=val)
                else:
                    elems = card_elem.find_elements(by=sel.by.value, value=sel.value)
                if elems:
                    for el in elems:
                        raw_t = getattr(el, "text", None)
                        if raw_t is not None:
                            txt = str(raw_t).strip()
                            if txt and txt not in PLATFORM_BADGE_MARKERS:
                                return txt
            except Exception:
                continue
        return ""

    def find_by_key(
        self,
        key: str,
        timeout_sec: float = 0.0,
        format_args: dict[str, Any] | None = None,
        default: str | list[str] | None = None,
    ):
        """Find an element using its configured key with automatic strategy detection."""
        ui_logger.debug("[UI] find_by_key '%s' timeout=%.1fs", key, timeout_sec)
        selectors = self.locators.get_selectors(key, format_args=format_args, default=default)
        if not selectors:
            ui_logger.debug("[UI] find_by_key '%s' -> no selectors configured", key)
            return None

        if timeout_sec > 0:
            try:
                return wait_until(
                    lambda: self._find_by_selectors(selectors),
                    timeout_sec=timeout_sec,
                    error_message=f"Element not found for key '{key}'",
                )
            except TimeoutError:
                ui_logger.debug("[UI] find_by_key '%s' -> timed out after %.1fs", key, timeout_sec)
                return None
        return self._find_by_selectors(selectors)

    def find_now(
        self,
        key: str,
        format_args: dict[str, Any] | None = None,
        default: str | list[str] | None = None,
    ):
        """Single fast-fail lookup: exactly one query, no polling.

        State checks that callers run inside their own retry loop use this, so a
        miss costs one selector query instead of a wait budget nobody honours.
        """
        return self.find_by_key(key, timeout_sec=0.0, format_args=format_args, default=default)

    def wait_for_key(
        self,
        key: str,
        timeout_sec: float = 10.0,
        format_args: dict[str, Any] | None = None,
        default: str | list[str] | None = None,
    ):
        """Wait until an element for the given key is found on screen."""
        ui_logger.debug("[UI] wait_for_key '%s' timeout=%.1fs", key, timeout_sec)
        if not self.driver:
            raise RuntimeError("Driver session is not initialized")
        selectors = self.locators.get_selectors(key, format_args=format_args, default=default)
        if not selectors:
            raise ValueError(f"No selector defined in configuration for key '{key}'")
        return wait_until(
            lambda: self._find_by_selectors(selectors),
            timeout_sec=timeout_sec,
            error_message=f"Timed out waiting for element key '{key}'",
        )

    def find_optional_element(self, selector: UISelector, timeout_sec: float = 0.0):
        """Backward compatible selector lookup."""
        if not self.driver:
            return None
        if timeout_sec > 0:
            try:
                return wait_until(
                    lambda: self._find_by_selectors([selector]),
                    timeout_sec=timeout_sec,
                    error_message=f"Element not found: {selector.description or selector.value}",
                )
            except TimeoutError:
                return None
        return self._find_by_selectors([selector])

    def wait_for_element(self, selector: UISelector, timeout_sec: float = 10.0):
        """Backward compatible selector wait."""
        if not self.driver:
            raise RuntimeError("Driver session is not initialized")
        return wait_until(
            lambda: self._find_by_selectors([selector]),
            timeout_sec=timeout_sec,
            error_message=f"Timed out waiting for element: {selector.description or selector.value}",
        )

    def press_back(self) -> None:
        """Send Android KEYCODE_BACK (keyevent 4) or driver.back() to press the hardware back button."""
        if not self.driver:
            return
        if hasattr(self.driver, "press_keycode"):
            try:
                ui_logger.debug("[UI] press KEYCODE_BACK (4) via=driver.press_keycode")
                self.driver.press_keycode(4)  # Android KEYCODE_BACK
                return
            except Exception:
                pass
        if hasattr(self.driver, "back"):
            ui_logger.debug("[UI] press back via=driver.back")
            with contextlib.suppress(Exception):
                self.driver.back()

    def _ensure_foreground(self) -> None:
        """Re-activate the Boss app if a Back press escaped it (e.g. to the launcher).

        The `current_package` property is itself a driver round-trip that can raise
        on a stale session, so a failure here must never abort the caller's loop.
        """
        if not self.driver:
            return
        try:
            package = getattr(self.driver, "current_package", None)
        except Exception:
            return
        if isinstance(package, str) and package and package != self.BOSS_PACKAGE_NAME:
            logger.warning("Foreground package is '%s' instead of Boss; re-activating app", package)
            self.activate_app()


class StartupDialogPage(BaseBossPage):
    """Handles startup privacy policy and permission dialogs."""

    def is_dialog_present(self) -> bool:
        return self.find_by_key("startup.agree_btn", timeout_sec=2.0) is not None

    def dismiss_dialog(self) -> bool:
        elem = self.find_by_key("startup.agree_btn", timeout_sec=2.0)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False


class LoginPage(BaseBossPage):
    """Detects login state and authentication challenges."""

    def is_login_screen(self) -> bool:
        return self.find_by_key("login.login_indicators", timeout_sec=1.0) is not None

    def is_captcha_present(self) -> bool:
        return self.find_by_key("login.captcha_indicator", timeout_sec=1.0) is not None

    def get_auth_status(self) -> AuthStatus:
        if self.is_captcha_present():
            return AuthStatus.CHALLENGE_REQUIRED
        if self.is_login_screen():
            return AuthStatus.UNAUTHENTICATED
        return AuthStatus.AUTHENTICATED


class JobListPage(BaseBossPage):
    """Interacts with the main job recommendation/search list."""

    def is_on_home_page(self) -> bool:
        """Check if currently on the main job recommendation home page."""
        return self.find_by_key("job_list.search_icon", timeout_sec=0.5) is not None

    def navigate_to_home(self, max_attempts: int = 6) -> bool:
        """Return to the Job Recommendation home page by pressing Back only.

        The home page state is defined by a single anchor: the search entry icon.
        Each attempt therefore does exactly one fast selector query — no probing of
        dialog close buttons, chat/detail back buttons or bottom tabs, which used to
        cost 20-40 seconds per call.
        """
        return self._press_back_until(self.is_on_home_page, max_attempts)

    def _press_back_until(self, is_done: Callable[[], bool], max_attempts: int) -> bool:
        """Recover toward ``is_done()`` with Back presses, bounded by ``max_attempts``.

        Per attempt: the 职位 bottom tab anchor (one fast query — measured on device,
        a bottom tab such as 消息 ignores Back entirely, so the tab click is the only
        way home from there), then a hardware Back press on a humanized cadence.
        """
        for _ in range(max_attempts):
            if is_done():
                return True

            job_tab = self.find_now("job_list.job_tab")
            if job_tab:
                self.gestures.human_click(job_tab)
                if is_done():
                    return True

            self._ensure_foreground()
            self.press_back()
            self.gestures.random_sleep(*BACK_INTERVAL_SEC)
        return is_done()

    def ensure_job_tab(self) -> bool:
        """Ensure the user is on the primary '职位' (Job) navigation tab."""
        elem = self.find_by_key("job_list.job_tab", timeout_sec=2.0)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False

    def open_search(self, timeout_sec: float = 10.0, max_back_attempts: int = 10) -> bool:
        """Enter the search input screen using exactly two anchors.

        1. Search input box already on screen -> we are done (no navigation at all).
        2. Home search entry icon on screen   -> click it, then wait for the input box.
        3. Neither                            -> press hardware Back and re-evaluate,
           up to ``max_back_attempts`` times, so a deep subpage unwinds quickly
           instead of scanning every dialog/detail/chat close button in the tree.
        """
        search_page = SearchPage(self.driver)

        def try_enter() -> bool:
            if search_page.is_search_page():
                return True
            entry = self.find_now("job_list.search_icon")
            if not entry:
                return False
            self.gestures.human_click(entry)
            return search_page.wait_for_search_page(timeout_sec=timeout_sec)

        return self._press_back_until(try_enter, max_back_attempts)

    def wait_for_jobs_loaded(self, timeout_sec: float = 15.0) -> bool:
        """Wait until at least one job card is present on the screen."""
        if not self.driver:
            return False
        try:
            self.wait_for_key("job_list.job_card", timeout_sec=timeout_sec)
            return True
        except TimeoutError:
            return False

    def get_feed_bottom_boundary(self, timeout_sec: float = 0.5) -> Any | None:
        """Find and return the feed bottom boundary element ('暂无其他符合职位，为你推荐') if visible."""
        if not self.driver:
            return None
        # 1. Try finding bottom_tips via configured locator registry
        try:
            elem = self.find_by_key("job_list.bottom_tips", timeout_sec=timeout_sec)
            if elem:
                txt = getattr(elem, "text", "") or ""
                if not txt or any(
                    kw in txt
                    for kw in (
                        "为你推荐",
                        "暂无其他符合职位",
                        "暂无符合职位",
                        "其他符合职位",
                        "没有更多",
                        "暂无更多",
                    )
                ):
                    return elem
        except Exception:
            pass
        # 2. Heuristic xpath fallback for text
        try:
            xpath_expr = (
                "//*[contains(@text, '为你推荐') or "
                "contains(@text, '暂无其他符合职位') or "
                "contains(@text, '暂无符合职位') or "
                "contains(@text, '没有更多') or "
                "contains(@text, '暂无更多')]"
            )
            elems = self.driver.find_elements(by="xpath", value=xpath_expr)
            if elems:
                return elems[0]
        except Exception:
            pass
        return None

    def is_feed_bottom_reached(self, timeout_sec: float = 0.5) -> bool:
        """Check if the search feed bottom boundary banner ('暂无其他符合职位，为你推荐') is visible."""
        return self.get_feed_bottom_boundary(timeout_sec=timeout_sec) is not None

    def scroll_job_list(self) -> None:
        """Perform a humanized scroll downwards on the job list."""
        if not self.driver:
            return
        size = self._get_window_size()
        w, h = size["width"], size["height"]

        start = Point(w * 0.5, h * 0.75)
        end = Point(w * 0.5, h * 0.25)
        self.gestures.human_swipe(start, end, duration_ms=500)
        self.gestures.random_sleep(0.3, 0.6)

    def select_first_job(self, timeout_sec: float = 10.0) -> bool:
        """Click on the primary visible job card."""
        elem = self.find_by_key("job_list.job_card", timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False

    def _read_card_facets(self, card_elem: Any) -> CardFacets:
        """Priority-1 reads: whatever the configured locators can address on this card.

        A read is a device concern, so it stays here; interpreting what was read is
        :mod:`boss_agent.card_parser`'s job.
        """
        return CardFacets(
            title=self._extract_card_field_text(card_elem, "job_list.card_title"),
            company=self._extract_card_field_text(card_elem, "job_list.card_company"),
            scale=self._extract_card_field_text(card_elem, "job_list.card_scale"),
            industry=self._extract_card_field_text(card_elem, "job_list.card_industry"),
            salary=self._extract_card_field_text(card_elem, "job_list.card_salary"),
            recruiter=self._extract_card_field_text(card_elem, "job_list.card_recruiter"),
            location=self._extract_card_field_text(card_elem, "job_list.card_location"),
            snippet=self._extract_card_field_text(card_elem, "job_list.card_snippet"),
            tags=tuple(self._read_card_tags(card_elem)),
        )

    def _read_card_tags(self, card_elem: Any) -> list[str]:
        """Tag texts from the card's tags container, or ``[]`` when there is none."""
        tags: list[str] = []
        with contextlib.suppress(Exception):
            for t_sel in self.locators.get_selectors("job_list.card_tags_container"):
                if t_sel.by.value == "id":
                    tag_boxes = card_elem.find_elements(by="id", value=t_sel.value)
                else:
                    tag_boxes = card_elem.find_elements(by=t_sel.by.value, value=t_sel.value)
                if tag_boxes:
                    tags = [
                        e.text.strip()
                        for e in tag_boxes[0].find_elements(by="xpath", value=".//*[@text]")
                        if getattr(e, "text", None) and e.text.strip()
                    ]
                    if tags:
                        break
        return tags

    def _read_card_text_nodes(self, card_elem: Any) -> list[str]:
        """The card's ordered text nodes, for the classifier's fallback pass."""
        with contextlib.suppress(Exception):
            nodes = [
                e.text.strip()
                for e in card_elem.find_elements(by="xpath", value=".//*[@text]")
                if getattr(e, "text", None) and e.text.strip()
            ]
            if nodes:
                return nodes
        raw_text = getattr(card_elem, "text", "") or ""
        return [line.strip() for line in raw_text.splitlines() if line.strip()]

    def _is_bottom_cutoff(self, card_elem: Any, parsed: ParsedCard) -> bool:
        """Whether a card lacking recruiter *and* city is merely cut off by the viewport.

        Device geometry, so it stays here: when a card is cut off at the bottom of the
        screen its lower sub-elements are not in the accessibility hierarchy yet, and
        the next scroll will bring a complete one into view.
        """
        if parsed.recruiter_name or parsed.location:
            return False
        try:
            elem_loc = getattr(card_elem, "location", None) or {}
            elem_size = getattr(card_elem, "size", None) or {}
            card_bottom = elem_loc.get("y", 0) + elem_size.get("height", 0)
            win_height = self._get_window_size()["height"]
            if card_bottom >= win_height * 0.85:
                logger.info(
                    "Skipping bottom-cutoff job card '%s - %s' (card_bottom=%d, win_h=%d) to wait for next scroll",
                    parsed.company_name,
                    parsed.title,
                    card_bottom,
                    win_height,
                )
                return True
        except Exception:
            pass
        return False

    def extract_visible_job_cards(self, max_cards: int = 10) -> list[LocatedJobCard]:
        """Extract visible job cards as data plus the element each was read from."""
        if not self.driver:
            return []
        cards: list[Any] = []
        for sel in self.locators.get_selectors("job_list.job_card"):
            try:
                elems = self.driver.find_elements(by=sel.by.value, value=sel.value)
                if elems:
                    cards = elems
                    break
            except Exception:
                continue

        located: list[LocatedJobCard] = []
        for card_elem in cards[:max_cards]:
            facets = self._read_card_facets(card_elem)
            # Only pay for the accessibility-tree walk when the locator reads were
            # incomplete — the parser decides that, so the rule lives in one place.
            text_nodes = self._read_card_text_nodes(card_elem) if needs_text_fallback(facets) else []
            parsed = parse_card(facets, text_nodes)
            if parsed is None:
                continue
            if self._is_bottom_cutoff(card_elem, parsed):
                continue
            located.append(
                LocatedJobCard(
                    card=JobCardBrief(
                        title=parsed.title,
                        company_name=parsed.company_name,
                        recruiter_name=parsed.recruiter_name or "招聘者",
                        salary_range=parsed.salary_range,
                        location=parsed.location,
                        tags=list(parsed.tags),
                        digest=parsed.snippet,
                        snippet=parsed.snippet,
                        company_scale=parsed.company_scale,
                        industry=parsed.industry,
                        recruiter_title=parsed.recruiter_title,
                        is_headhunter=parsed.is_headhunter,
                    ),
                    element=card_elem,
                )
            )
        return located


class SearchPage(BaseBossPage):
    """Page Object for the Boss 直聘 job search screen."""

    def is_search_page(self) -> bool:
        """Check if currently on the search input screen.

        Single fast-fail query (no wait loop): the caller polls this in its own
        retry loop, and a slow "wait" here both lied about its budget and made
        every miss cost several seconds.
        """
        return self.find_by_key("search.search_input", timeout_sec=0.0) is not None

    def wait_for_search_page(self, timeout_sec: float = 10.0) -> bool:
        """Wait until search input box is present on screen."""
        try:
            self.wait_for_key("search.search_input", timeout_sec=timeout_sec)
            return True
        except TimeoutError:
            return False

    def clear_input(self) -> bool:
        """Clear search input via clear icon or direct element clear."""
        clear_elem = self.find_by_key("search.clear_btn", timeout_sec=1.0)
        if clear_elem:
            self.gestures.human_click(clear_elem)
            return True
        input_elem = self.find_by_key("search.search_input", timeout_sec=1.0)
        if input_elem and hasattr(input_elem, "clear"):
            try:
                input_elem.clear()
                return True
            except Exception:
                pass
        return False

    def enter_keyword(self, keyword: str, timeout_sec: float = 10.0) -> bool:
        """Type search keyword into search input."""
        elem = self.find_by_key("search.search_input", timeout_sec=timeout_sec)
        if not elem:
            return False
        self.clear_input()
        self.gestures.human_click(elem)
        self.gestures.human_type(elem, keyword, clear_first=False)
        return True

    def submit_search(self, timeout_sec: float = 10.0) -> bool:
        """Submit the search by clicking the '搜索' button."""
        elem = self.find_by_key("search.search_btn", timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_click(elem)
            return True
        if self.driver and hasattr(self.driver, "press_keycode"):
            try:
                self.driver.press_keycode(66)  # KEYCODE_ENTER
                return True
            except Exception:
                pass
        return False

    def search(self, keyword: str, timeout_sec: float = 15.0) -> bool:
        """Convenience method to enter keyword and submit search."""
        if not self.wait_for_search_page(timeout_sec=timeout_sec):
            return False
        if not self.enter_keyword(keyword, timeout_sec=timeout_sec):
            return False
        return self.submit_search(timeout_sec=timeout_sec)

    def navigate_back(self) -> bool:
        """Navigate back to the previous screen."""
        elem = self.find_by_key("search.back_btn", timeout_sec=2.0)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False


FILTER_OPTION_SYNONYMS: dict[str, list[str]] = {
    "3k以下": ["3000元以下", "3K以下", "3k以下"],
    "3000元以下": ["3000元以下", "3K以下", "3k以下"],
    "3-5k": ["3000-5000元", "3-5K", "3-5k"],
    "3000-5000元": ["3000-5000元", "3-5K", "3-5k"],
    "5-10k": ["5000-10000元", "5-10K", "5-10k"],
    "5000-10000元": ["5000-10000元", "5-10K", "5-10k"],
    "10-20k": ["1-2万元", "1-2万", "10-20K", "10-20k"],
    "1-2万": ["1-2万元", "1-2万", "10-20K", "10-20k"],
    "1-2万元": ["1-2万元", "1-2万", "10-20K", "10-20k"],
    "20-50k": ["2-5万元", "2-5万", "20-50K", "20-50k"],
    "2-5万": ["2-5万元", "2-5万", "20-50K", "20-50k"],
    "2-5万元": ["2-5万元", "2-5万", "20-50K", "20-50k"],
    "50k以上": ["5万元以上", "5万以上", "50K以上", "50k以上"],
    "5万以上": ["5万元以上", "5万以上", "50K以上", "50k以上"],
    "5万元以上": ["5万元以上", "5万以上", "50K以上", "50k以上"],
    "在校/应届": ["应届生", "在校生", "在校/应届"],
    "在校生": ["在校生", "在校/应届"],
    "应届生": ["应届生", "在校/应届"],
    "1年以内": ["1年以内", "1年以下"],
}


class FilterDialogPage(BaseBossPage):
    """Page Object for the Boss 直聘 Job Filter Dialog (筛选)."""

    def is_dialog_open(self) -> bool:
        """Check if filter dialog is currently open."""
        return self.find_by_key("filter.confirm_btn", timeout_sec=1.0) is not None

    def open_filter(self, timeout_sec: float = 10.0) -> bool:
        """Click the '筛选' entry button to open the filter dialog and wait for it."""
        if self.is_dialog_open():
            return True
        elem = self.find_by_key("filter.filter_entry", timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_click(elem)
            try:
                wait_until(
                    self.is_dialog_open,
                    timeout_sec=5.0,
                    error_message="Filter dialog did not open after clicking filter button",
                )
                return True
            except TimeoutError:
                return False
        return False

    def scroll_dialog_down(self) -> None:
        """Scroll down within the filter dialog to reveal lower sections (e.g. BOSS活跃, 公司规模)."""
        if not self.driver:
            return
        size = self._get_window_size()
        w, h = size["width"], size["height"]
        start = Point(w * 0.5, h * 0.70)
        end = Point(w * 0.5, h * 0.30)
        self.gestures.human_swipe(start, end, duration_ms=400)

    def select_option(self, option_text: str, auto_scroll: bool = True) -> bool:
        """Find and click a filter option tag with optional auto-scroll and synonym fallback."""
        if not option_text or not option_text.strip():
            return False

        trimmed = option_text.strip()
        if trimmed == "不限":
            return True

        normalized_key = trimmed.lower()
        synonyms = FILTER_OPTION_SYNONYMS.get(normalized_key, [])
        candidates: list[str] = []
        for s in synonyms:
            if s not in candidates:
                candidates.append(s)
        if trimmed not in candidates:
            candidates.insert(0, trimmed)

        def _try_click_option() -> bool:
            for cand in candidates:
                elem = self.find_by_key(
                    "filter.option_item",
                    timeout_sec=1.5,
                    format_args={"text": cand},
                )
                if elem:
                    self.gestures.human_click(elem)
                    logger.info("Selected filter option: '%s' (matched tag '%s')", trimmed, cand)
                    return True
            return False

        if _try_click_option():
            return True

        if auto_scroll:
            self.scroll_dialog_down()
            return _try_click_option()

        logger.warning(
            "Filter option '%s' (candidates=%s) not found in dialog", trimmed, candidates
        )
        return False

    def confirm_filter(self, timeout_sec: float = 5.0) -> bool:
        """Click the '确定' button to apply chosen filters."""
        elem = self.find_by_key("filter.confirm_btn", timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_click(elem)
            with contextlib.suppress(TimeoutError):
                wait_until(
                    lambda: not self.is_dialog_open(),
                    timeout_sec=5.0,
                    error_message="Filter dialog failed to close",
                )
            return True
        return False

    def reset_filter(self) -> bool:
        """Click the '清除' button to reset filters to default."""
        elem = self.find_by_key("filter.reset_btn", timeout_sec=2.0)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False

    def clear_filters(self, timeout_sec: float = 10.0) -> bool:
        """Open filter dialog, click reset button, and confirm to clear all filters."""
        if not self.is_dialog_open():
            opened = self.open_filter(timeout_sec=timeout_sec)
            if not opened:
                return False
        self.reset_filter()
        time.sleep(0.3)
        return self.confirm_filter(timeout_sec=timeout_sec)

    def close_dialog(self) -> bool:
        """Close the filter dialog without applying changes."""
        elem = self.find_by_key("filter.close_btn", timeout_sec=2.0)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False

    def apply_filters(self, config: FilterConfig | None, timeout_sec: float = 10.0) -> bool:
        """Apply all specified filter dimensions in order, clearing previous conditions first."""
        if not config or not config.has_filters:
            return self.clear_filters(timeout_sec=timeout_sec)

        if not self.is_dialog_open():
            opened = self.open_filter(timeout_sec=timeout_sec)
            if not opened:
                return False

        # Always reset first to prevent previous search conditions from persisting
        self.reset_filter()
        time.sleep(0.3)

        def _is_effective(val: str | None) -> bool:
            return bool(val and val.strip() and val.strip() != "不限")

        # 1. Top visible filters: Education, Salary, Experience
        if _is_effective(config.education):
            self.select_option(config.education, auto_scroll=False)
        if _is_effective(config.salary):
            self.select_option(config.salary, auto_scroll=False)
        if _is_effective(config.experience):
            self.select_option(config.experience, auto_scroll=False)

        # 2. Scroll down for bottom sections: Activity and Company Scales
        needs_scroll = _is_effective(config.activity) or any(
            _is_effective(s) for s in config.company_scales
        )
        if needs_scroll:
            self.scroll_dialog_down()

            if _is_effective(config.activity):
                self.select_option(config.activity, auto_scroll=True)

            for scale in config.company_scales:
                if _is_effective(scale):
                    self.select_option(scale, auto_scroll=True)

        # 3. Confirm
        return self.confirm_filter()


class IndustryFilterDialogPage(BaseBossPage):
    """Page Object for the Boss 直聘 Industry Filter Dialog (行业筛选)."""

    def is_dialog_open(self) -> bool:
        """Check if industry filter dialog is currently open."""
        return (
            self.find_by_key("industry.confirm_btn", timeout_sec=1.0) is not None
            or self.find_by_key("industry.cancel_btn", timeout_sec=1.0) is not None
        )

    def open_industry_filter(self, timeout_sec: float = 10.0) -> bool:
        """Click the '行业' filter entry button to open the industry selection dialog and wait for it."""
        if self.is_dialog_open():
            return True
        elem = self.find_by_key("industry.filter_entry", timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_click(elem)
            try:
                wait_until(
                    self.is_dialog_open,
                    timeout_sec=5.0,
                    error_message="Industry filter dialog did not open after clicking industry entry button",
                )
                return True
            except TimeoutError:
                return False
        return False

    def scroll_dialog_down(self) -> None:
        """Scroll down within the industry filter dialog to reveal lower industry categories."""
        if not self.driver:
            return
        size = self._get_window_size()
        w, h = size["width"], size["height"]
        start = Point(w * 0.5, h * 0.70)
        end = Point(w * 0.5, h * 0.30)
        self.gestures.human_swipe(start, end, duration_ms=400)

    def scroll_dialog_up(self) -> None:
        """Scroll up within the industry filter dialog."""
        if not self.driver:
            return
        size = self._get_window_size()
        w, h = size["width"], size["height"]
        start = Point(w * 0.5, h * 0.30)
        end = Point(w * 0.5, h * 0.70)
        self.gestures.human_swipe(start, end, duration_ms=400)

    def select_industry_option(
        self, option_text: str, auto_scroll: bool = True, max_scroll_attempts: int = 4
    ) -> bool:
        """Find and click a specific industry tag option, auto-scrolling if needed."""
        if not option_text:
            return False

        def _try_click_option() -> bool:
            elem = self.find_by_key(
                "industry.option_item",
                timeout_sec=1.5,
                format_args={"text": option_text},
            )
            if elem:
                self.gestures.human_click(elem)
                return True
            return False

        if _try_click_option():
            return True

        if auto_scroll:
            for _ in range(max_scroll_attempts):
                self.scroll_dialog_down()
                if _try_click_option():
                    return True

        return False

    def select_industries(self, industries: list[str], auto_scroll: bool = True) -> list[str]:
        """Select multiple industry tag options (multi-select). Returns list of successfully selected industries."""
        selected: list[str] = []
        for ind in industries:
            if self.select_industry_option(ind, auto_scroll=auto_scroll):
                selected.append(ind)
        return selected

    def confirm_filter(self, timeout_sec: float = 5.0) -> bool:
        """Click the '确定' button to apply chosen industry filters."""
        elem = self.find_by_key("industry.confirm_btn", timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_click(elem)
            with contextlib.suppress(TimeoutError):
                wait_until(
                    lambda: not self.is_dialog_open(),
                    timeout_sec=5.0,
                    error_message="Industry filter dialog failed to close after confirmation",
                )
            return True
        return False

    def cancel_filter(self, timeout_sec: float = 5.0) -> bool:
        """Click the '取消' button to dismiss industry filters without applying."""
        elem = self.find_by_key("industry.cancel_btn", timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_click(elem)
            with contextlib.suppress(TimeoutError):
                wait_until(
                    lambda: not self.is_dialog_open(),
                    timeout_sec=5.0,
                    error_message="Industry filter dialog failed to close after cancellation",
                )
            return True
        return False

    def apply_industry_filters(
        self, industries: list[str] | None, timeout_sec: float = 10.0
    ) -> bool:
        """Complete workflow to open industry filter dialog, select multiple industries, and confirm."""
        if not industries:
            return False

        if not self.is_dialog_open():
            opened = self.open_industry_filter(timeout_sec=timeout_sec)
            if not opened:
                return False

        # Select all specified industry options (multi-select)
        selected = self.select_industries(industries, auto_scroll=True)
        if not selected:
            self.cancel_filter()
            return False

        return self.confirm_filter()


class JobDetailPage(BaseBossPage):
    """Extracts job posting details and interacts with the job detail screen."""

    def __init__(self, driver: Any, locator_registry: LocatorRegistry | None = None):
        super().__init__(driver, locator_registry)
        self._current_description: str = ""

    def extract_commute_distance(
        self, max_scrolls: int = COMMUTE_PROBE_MAX_SCROLLS
    ) -> tuple[float | None, str]:
        """Probe toward the bottom of the detail page for the commute distance widget.

        The Boss platform offers no native distance filter, but the detail page bottom
        renders ``home_tip_vf`` containing e.g. "距离家庭住址19.5千米". Returns
        ``(distance_km, raw_text)``; ``(None, "")`` when the widget is absent within the
        scroll budget (no home address configured, remote job, or timeout) so callers
        fail open instead of rejecting an unknown distance.

        The budget defaults to :data:`COMMUTE_PROBE_MAX_SCROLLS` because an expanded JD
        pushes the widget far below the fold; callers may override it. Probing ends as soon
        as the widget appears or the scroll container refuses to move any further.
        """
        elem = self.find_by_key("job_detail.distance_tip", timeout_sec=1.0)

        scrolls = 0
        stalled_swipes = 0
        while elem is None and scrolls < max_scrolls:
            scrolls += 1
            win_size = self._get_window_size()
            offset_before = self._detail_scroll_offset()
            _log_info(
                f"📜 [Commute Probe {scrolls}/{max_scrolls}] Distance widget not in viewport; "
                f"scrolling toward page bottom to reveal 'home_tip_vf'..."
            )
            self._scroll_page_up(
                int(win_size.get("height", 2400) * COMMUTE_PROBE_SCROLL_STRIDE_RATIO)
            )
            elem = self.find_by_key("job_detail.distance_tip", timeout_sec=1.0)
            if elem is not None:
                break

            stalled_swipes = stalled_swipes + 1 if self._swipe_moved_nothing(offset_before) else 0
            if stalled_swipes >= COMMUTE_PROBE_STALLED_SWIPES:
                _log_info(
                    f"⏹️ [Commute Probe] Detail page has not moved for {stalled_swipes} "
                    f"swipes ({scrolls} total); it is at its bottom and 'home_tip_vf' "
                    f"cannot appear below it."
                )
                break

        if elem is None:
            _log_info(
                f"ℹ️ [Commute Probe] 'home_tip_vf' distance widget not found after {scrolls} "
                f"scroll(s); failing open (distance screening passes)."
            )
            return None, ""

        raw = getattr(elem, "text", None)
        if not isinstance(raw, str):
            # The locator matched a node without readable text (wrong node, or a
            # widget that has not been populated yet). Treat it as absent.
            _log_warn(
                f"⚠️ [Commute Probe] Distance widget carried no readable text "
                f"({type(raw).__name__}); failing open."
            )
            return None, ""

        raw_text = raw.strip()
        distance_km = self._parse_commute_distance(raw_text)
        if distance_km is None:
            _log_warn(
                f"⚠️ [Commute Probe] Distance widget found but text was unparseable: "
                f"'{raw_text}'. Failing open."
            )
        else:
            _log_info(f"📍 [Commute Probe] Parsed commute distance: {distance_km} km ('{raw_text}')")
        return distance_km, raw_text

    def _detail_scroll_offset(self) -> float | None:
        """Screen-space top of a stable detail-page anchor, or None when unreadable.

        Comparing the anchor before and after a swipe is how the commute probe tells that
        the scroll container has bottomed out. Unreadable bounds return None, which
        disables the check entirely: an unreadable page must spend its full scroll budget
        rather than end the search on a guess.
        """
        for key in ("job_detail.desc", "job_detail.title"):
            rect = getattr(self.find_now(key), "rect", None)
            if isinstance(rect, dict) and isinstance(rect.get("y"), int | float):
                return float(rect["y"])
        return None

    def _swipe_moved_nothing(self, offset_before: float | None) -> bool:
        """Whether a swipe left the page exactly where it was, i.e. this swipe moved nothing."""
        if offset_before is None:
            return False
        offset_after = self._detail_scroll_offset()
        return offset_after is not None and abs(offset_after - offset_before) < 1.0

    @staticmethod
    def _parse_commute_distance(raw_text: str) -> float | None:
        """Normalize a distance widget string to kilometres, or None if unparseable."""
        match = COMMUTE_DISTANCE_PATTERN.search(raw_text or "")
        if not match:
            return None
        value = float(match.group(1))
        if match.group(2).lower() in ("米", "m"):
            value /= 1000.0
        return round(value, 3)

    def _scroll_page_up(self, scroll_px: int) -> None:
        """Swipe up on screen to scroll the detail page content downwards."""
        win_size = self._get_window_size()
        screen_width = win_size.get("width", 1080)
        screen_height = win_size.get("height", 2400)

        mid_x = screen_width // 2
        start_y = int(screen_height * 0.75)
        end_y = max(int(screen_height * 0.15), start_y - scroll_px)

        _log_info(
            f"📜 [Scroll Page Up] Swiping from ({mid_x}, {start_y}) to ({mid_x}, {end_y}) "
            f"[distance: {start_y - end_y}px] to reveal lower content..."
        )
        self.gestures.human_swipe(
            Point(mid_x, start_y),
            Point(mid_x, end_y),
            duration_ms=450,
        )
        time.sleep(0.4)

    def expand_description_if_collapsed(self, max_scroll_attempts: int = 4) -> bool:
        """Expand truncated job description by scrolling to reveal its bottom and tapping the '查看更多' hotspot.

        Returns True if expanded or not truncated; False if expansion failed.
        """
        _log_info("🔍 Checking job description expansion status...")

        # 1. First attempt: standard explicit expand button if visible
        elem = self.find_by_key("job_detail.expand_btn", timeout_sec=0.5)
        if elem:
            _log_info(
                "👆 Found standard explicit expand button ('查看全部' / '展开全文'), clicking it..."
            )
            self.gestures.human_click(elem)
            time.sleep(0.3)
            desc_elem = self.find_by_key("job_detail.desc", timeout_sec=1.0)
            if desc_elem and getattr(desc_elem, "text", None):
                self._current_description = desc_elem.text.strip()
            return True

        # 2. Locate the job description TextView (com.hpbr.bosszhipin:id/tv_description)
        desc_elem = self.find_by_key("job_detail.desc", timeout_sec=1.5)
        if not desc_elem:
            win_size = self._get_window_size()
            _log_info(
                "📜 Job description element not visible in initial viewport; scrolling down once to locate it..."
            )
            self._scroll_page_up(int(win_size.get("height", 2400) * 0.4))
            desc_elem = self.find_by_key("job_detail.desc", timeout_sec=2.0)

        if not desc_elem:
            _log_error(
                "Failed to locate job description element ('com.hpbr.bosszhipin:id/tv_description') on detail page!"
            )
            return False

        initial_text = getattr(desc_elem, "text", "") or ""
        self._current_description = initial_text.strip()

        is_truncated = (
            "查看更多" in initial_text or "展开" in initial_text or initial_text.endswith("...")
        )
        if not is_truncated:
            _log_info(
                f"✅ Job description is already fully expanded (length: {len(initial_text)} chars, no '查看更多' found)."
            )
            return True

        _log_info(
            f"📑 Truncated job description detected (length: {len(initial_text)} chars). "
            f"Snippet: '...{initial_text[-40:].replace(chr(10), ' ')}'. Preparing to scroll and expand..."
        )

        win_size = self._get_window_size()
        screen_height = win_size.get("height", 2400)
        # The floating '立即沟通' bar sits at the bottom ~220-250px. Keep bottom of JD well above it.
        safe_bottom_threshold = screen_height - 260
        target_view_y = int(screen_height * 0.60)

        # Iteratively scroll until bottom of tv_description is in the safe visible area
        for attempt in range(1, max_scroll_attempts + 1):
            rect = getattr(desc_elem, "rect", None)
            if not (
                isinstance(rect, dict)
                and all(
                    k in rect and isinstance(rect[k], int | float)
                    for k in ("x", "y", "width", "height")
                )
            ):
                _log_warn(
                    f"Cannot retrieve valid element bounds on attempt {attempt}; scrolling page..."
                )
                self._scroll_page_up(int(screen_height * 0.35))
                desc_elem = self.find_by_key("job_detail.desc", timeout_sec=1.0)
                continue

            elem_top = float(rect["y"])
            elem_height = float(rect["height"])
            elem_bottom = elem_top + elem_height

            _log_info(
                f"📏 [JD Bounds Check {attempt}/{max_scroll_attempts}] "
                f"Top: {elem_top:.1f}, Height: {elem_height:.1f}, Bottom: {elem_bottom:.1f} "
                f"(Safe threshold: < {safe_bottom_threshold:.1f})"
            )

            if elem_bottom > safe_bottom_threshold:
                scroll_needed = int(elem_bottom - target_view_y)
                scroll_distance = max(150, min(int(screen_height * 0.45), scroll_needed))
                _log_info(
                    f"📜 JD bottom ({elem_bottom:.1f}) is obstructed/below threshold ({safe_bottom_threshold:.1f}). "
                    f"Scrolling page up by {scroll_distance} px (attempt {attempt}/{max_scroll_attempts})..."
                )
                self._scroll_page_up(scroll_distance)
                desc_elem = self.find_by_key("job_detail.desc", timeout_sec=1.0)
                if not desc_elem:
                    _log_warn("Lost job description element reference after swipe; re-locating...")
                    desc_elem = self.find_by_key("job_detail.desc", timeout_sec=2.0)
                    if not desc_elem:
                        _log_error("Could not find job description element after scroll!")
                        return False
            else:
                _log_info(
                    f"🎯 JD bottom ({elem_bottom:.1f}) is now safely in view (safe threshold: {safe_bottom_threshold:.1f})."
                )
                break

        # Re-fetch bounds after scroll settling
        rect = getattr(desc_elem, "rect", None)
        if not (
            isinstance(rect, dict)
            and all(
                k in rect and isinstance(rect[k], int | float)
                for k in ("x", "y", "width", "height")
            )
        ):
            _log_error("Cannot calculate tap coordinates: element rect is invalid after scrolling!")
            return False

        # Tapping the '查看更多' hotspot in bottom-right corner of tv_description
        tap_offsets = [(0.90, 25.0), (0.80, 25.0)]
        expanded = False

        for idx, (ratio_x, offset_y) in enumerate(tap_offsets, 1):
            target_x, target_y = calculate_probe_coordinate(
                rect, [ratio_x, offset_y], origin="bottom-left"
            )
            _log_info(
                f"👆 [Tap Hotspot {idx}/{len(tap_offsets)}] Tapping '查看更多' at screen coordinate "
                f"({target_x:.1f}, {target_y:.1f}) [ratio_x={ratio_x}, offset_y={offset_y}px from bottom]..."
            )
            self.gestures.human_click_at_point(target_x, target_y, jitter_px=3.0)

            # Wait for layout update and inspect fresh element
            time.sleep(0.5)
            refreshed = self.find_by_key("job_detail.desc", timeout_sec=1.0)
            if refreshed:
                desc_elem = refreshed
            curr_text = getattr(desc_elem, "text", "") or ""

            if "查看更多" not in curr_text or len(curr_text) >= len(initial_text) + 10:
                _log_info(
                    f"✨ [Expansion Success] Job description expanded successfully! "
                    f"Length: {len(initial_text)} -> {len(curr_text)} chars."
                )
                self._current_description = curr_text.strip()
                expanded = True
                break
            else:
                _log_warn(
                    f"⚠️ Tap attempt {idx} at ({target_x:.1f}, {target_y:.1f}) did not trigger expansion. "
                    f"(Text still contains '查看更多', current length: {len(curr_text)})"
                )

        if not expanded:
            curr_text = getattr(desc_elem, "text", "") or ""
            self._current_description = curr_text.strip() or initial_text.strip()
            if "查看更多" in curr_text:
                _log_error(
                    f"❌ FAILED TO EXPAND JOB DESCRIPTION: '查看更多' is STILL present after scrolling and {len(tap_offsets)} tap attempts! "
                    f"Tail text: '...{curr_text[-60:].replace(chr(10), ' ')}'"
                )
            return False

        return True

    def extract_job_posting(
        self,
        timeout_sec: float = 10.0,
        fallback_company: str = "",
        fallback_title: str = "",
        probe_commute_distance: bool = False,
        is_headhunter: bool | None = None,
        commute_max_scrolls: int = COMMUTE_PROBE_MAX_SCROLLS,
    ) -> JobPosting:
        """Extract structured JobPosting from current job detail screen.

        ``probe_commute_distance`` triggers the bottom-widget scroll probe, so it is
        only enabled while the commute ceiling is active — otherwise every detail
        inspection would pay swipe latency for a filter that cannot reject anything.

        ``is_headhunter`` suppresses that probe for headhunter postings: the platform
        conceals the hiring enterprise and its address there, so the distance tip is
        never rendered and the scroll could only burn gesture budget and
        element-discovery timeouts. An unknown channel (``None``) still probes, so an
        unrecognised direct hire is never silently spared distance screening.

        ``commute_max_scrolls`` is the probe's swipe budget, relaxed by default for the
        long expanded JDs that push the widget several screens below the fold.

        Raises RuntimeError if job details are not found on the screen.
        """
        # Explicit wait for title or salary element on detail page
        try:
            self.wait_for_key("job_detail.title", timeout_sec=timeout_sec)
        except TimeoutError:
            if not self.find_by_key("job_detail.salary", timeout_sec=2.0):
                raise RuntimeError(
                    "Failed to extract job posting: Job detail screen did not load within timeout. "
                    "Ensure job card was clicked and navigation to detail screen completed."
                ) from None

        # 1. Capture header elements FIRST while at top of viewport before scrolling down.
        # Scrolling down to expand the description can recycle/remove off-screen headers from accessibility node tree.
        title_elem = self.find_by_key("job_detail.title")
        company_elem = self.find_by_key("job_detail.company")
        salary_elem = self.find_by_key("job_detail.salary")

        title = title_elem.text.strip() if title_elem and getattr(title_elem, "text", None) else ""
        company = (
            company_elem.text.strip()
            if company_elem and getattr(company_elem, "text", None)
            else ""
        )
        salary = (
            salary_elem.text.strip() if salary_elem and getattr(salary_elem, "text", None) else ""
        )

        # 2. Expand job description if truncated (may scroll the page down)
        self.expand_description_if_collapsed()

        # 3. Retrieve full job description from cached property or current element
        desc = (self._current_description or "").strip()
        if not desc:
            desc_elem = self.find_by_key("job_detail.desc")
            desc = desc_elem.text.strip() if desc_elem and getattr(desc_elem, "text", None) else ""

        # 4. Fallback lookups in case header elements were somehow missed before scroll
        if not title:
            title_elem = self.find_by_key("job_detail.title")
            title = (
                title_elem.text.strip() if title_elem and getattr(title_elem, "text", None) else ""
            )
        if not company:
            company_elem = self.find_by_key("job_detail.company")
            company = (
                company_elem.text.strip()
                if company_elem and getattr(company_elem, "text", None)
                else ""
            )
        if not salary:
            salary_elem = self.find_by_key("job_detail.salary")
            salary = (
                salary_elem.text.strip()
                if salary_elem and getattr(salary_elem, "text", None)
                else ""
            )

        if "查看更多" in desc:
            _log_error(
                f"❌ [JobDetailPage] Incomplete Job Description extracted! "
                f"'查看更多' still present in final text for '{title}'. Length: {len(desc)}"
            )

        eff_title = title or fallback_title or "未注明职位"
        eff_company = company or fallback_company or "未注明公司"

        if not eff_title and not desc:
            raise RuntimeError(
                "Failed to extract job posting: Both job title and description were missing or empty. "
                "The current screen is not a valid job detail page."
            )

        # Headhunter postings conceal the hiring enterprise, so the platform never draws
        # the distance tip for them: the probe cannot succeed and would only spend gesture
        # budget. Guarded here as well as in the caller's policy so that no caller can buy
        # a scroll for a widget the platform cannot render.
        commute_distance_km: float | None = None
        commute_distance_text = ""
        if probe_commute_distance and is_headhunter is not True:
            commute_distance_km, commute_distance_text = self.extract_commute_distance(
                max_scrolls=commute_max_scrolls
            )

        return JobPosting(
            title=eff_title,
            company_name=eff_company,
            salary_range=salary or "面议",
            job_description=desc or "无详细岗位描述",
            commute_distance_km=commute_distance_km,
            commute_distance_text=commute_distance_text,
        )

    def get_chat_button_state(self, timeout_sec: float = 2.0) -> ChatButtonState:
        """Read the engagement state of the detail page call-to-action button (`btn_chat`).

        Returns UNKNOWN when the button is absent or its text is unrecognised, so callers keep
        their normal extraction flow instead of skipping a possibly live posting.
        """
        # Two-stage timing, one selector source. The registry's `job_detail.chat_btn`
        # key already leads with the same resource id this used to hardcode, so the
        # registry lookup stays the only path to a selector: a Boss UI change is
        # absorbed by editing the locator config, not by hunting inline UISelectors.
        # The zero-timeout first pass is what keeps this probe cheap on the happy path.
        elem = self.find_by_key("job_detail.chat_btn", timeout_sec=0.0) or self.find_by_key(
            "job_detail.chat_btn", timeout_sec=timeout_sec
        )
        if not elem:
            return ChatButtonState.UNKNOWN

        text = getattr(elem, "text", "") or ""
        enabled = True
        try:
            raw_enabled = elem.get_attribute("enabled")
        except Exception:
            raw_enabled = None
        if isinstance(raw_enabled, str):
            enabled = raw_enabled.strip().lower() not in ("false", "0")
        elif raw_enabled is not None:
            enabled = bool(raw_enabled)

        return classify_chat_button(text, enabled=enabled)

    def open_chat(self, timeout_sec: float = 5.0) -> bool:
        """Click '立即沟通' / chat entry button to open chat dialog from job detail screen."""
        elem = self.find_by_key("chat.chat_entry_btn", timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False

    def navigate_back(self) -> bool:
        elem = self.find_by_key("job_detail.back_btn", timeout_sec=2.0)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False


class ChatPage(BaseBossPage):
    """Interacts with the Boss 直聘 chat/greeting communication page."""

    def is_chat_page(self, timeout_sec: float = 3.0) -> bool:
        """Check if currently inside chat dialog/page."""
        return bool(self.find_by_key("chat.message_input", timeout_sec=timeout_sec))

    def open_chat(self, timeout_sec: float = 5.0) -> bool:
        """Click '立即沟通' / chat entry button to open chat dialogue."""
        elem = self.find_by_key("chat.chat_entry_btn", timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False

    def type_greeting_message(
        self, message: str, timeout_sec: float = 5.0, clear_first: bool = False
    ) -> bool:
        """Type greeting message into the chat message input box.

        IMPORTANT SAFETY GUARANTEE: Does NOT click the send button.
        Allows user to review, edit, or manually send during testing.
        """
        elem = self.find_by_key("chat.message_input", timeout_sec=timeout_sec)
        if elem:
            if clear_first:
                self.gestures.human_type(elem, message, clear_first=True)
            else:
                self.gestures.human_type(elem, message)
            return True
        return False

    def clear_message_input(self, timeout_sec: float = 3.0) -> bool:
        """Clear any text from the chat message input box to avoid leaving drafts."""
        elem = self.find_by_key("chat.message_input", timeout_sec=timeout_sec)
        if elem and hasattr(elem, "clear"):
            with contextlib.suppress(Exception):
                elem.clear()
                return True
        return False

    def click_send(self, timeout_sec: float = 3.0) -> bool:
        """Click the send button on the chat page."""
        elem = self.find_by_key("chat.send_btn", timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False

    def send_message(self, message: str, timeout_sec: float = 5.0) -> bool:
        """Type and send a chat message in one step.

        Only ever reached for messages already judged safe to send (e.g. a
        rejection acknowledgment). Blank text is refused so an empty input box
        can never be submitted.
        """
        if not (message or "").strip():
            return False
        if not self.type_greeting_message(message, timeout_sec=timeout_sec, clear_first=True):
            return False
        return self.click_send(timeout_sec=timeout_sec)

    def navigate_back(self, timeout_sec: float = 3.0) -> bool:
        """Click back button from chat dialog."""
        elem = self.find_by_key("chat.back_btn", timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_click(elem)
            return True
        # Fallback to driver back if element not found
        try:
            self.driver.back()
            return True
        except Exception:
            return False


class CommunicationListPage(BaseBossPage):
    """Page Object for the 仅沟通 communication list under the 消息 tab.

    Cards carry everything the triage needs — the Outbound Message Indicator, the
    `[Company] | [Position]` descriptor and the last message text — so neither
    classification nor employer extraction ever requires opening a chat (spec #205).
    """

    def is_on_list(self, timeout_sec: float = 2.0) -> bool:
        """Check whether the 仅沟通 communication list is currently displayed."""
        return (
            self.find_by_key("communication_list.communication_tab", timeout_sec=timeout_sec)
            is not None
        )

    def wait_for_list_return(self, timeout_sec: float = 5.0) -> bool:
        """Wait for the platform to drop the conversation and land back on the list."""
        return self.is_on_list(timeout_sec=timeout_sec)

    def open_list(self, timeout_sec: float = 5.0, max_steps: int = LIST_RECOVERY_MAX_STEPS) -> bool:
        """Navigate into the 仅沟通 list from whatever screen the app is currently on.

        The first check means an app already showing the list is never clicked at all.
        From anywhere else the loop spends at most ``max_steps`` screens trying to
        reach the message column, then clicks 消息 -> 仅沟通 and confirms the landing.

        A CHECK_CHAT dispatch can arrive while the app sits on a job detail, an open
        chat, a filter sheet or the launcher, so "the 消息 tab is right there" cannot
        be assumed: unwinding first, and clicking the column the moment it appears,
        is what makes the navigation self-healing instead of an instant task failure.

        The per-step probes fast-fail; only the landing confirmation inside
        `_open_message_column` spends the caller's ``timeout_sec``, because polling
        out the full element budget on each of ``max_steps`` screens turns one
        unreachable list into a multi-minute stall.
        """
        for step in range(max_steps + 1):
            if self.is_on_list(timeout_sec=0.0):
                return True
            if self._open_message_column(timeout_sec=timeout_sec):
                return True
            if step == max_steps:
                break
            ui_logger.debug("[UI] 仅沟通 recovery step %d/%d", step + 1, max_steps)
            self._recover_one_step()
        return False

    def _open_message_column(self, timeout_sec: float) -> bool:
        """Click the bottom 消息 tab then the 仅沟通 sub-tab; True once landed on the list."""
        tab = self.find_now("communication_list.entry_tab")
        if not tab:
            return False
        self.gestures.human_click(tab)

        sub_tab = self.find_by_key("communication_list.communication_tab", timeout_sec=timeout_sec)
        if not sub_tab:
            return False
        self.gestures.human_click(sub_tab)
        return self.is_on_list(timeout_sec=timeout_sec)

    def _recover_one_step(self) -> None:
        """Unwind exactly one screen: on-screen back button first, hardware key second.

        The on-screen affordance is preferred because it keeps the app inside Boss,
        whereas a hardware Back from a chat room or the message column can leave the
        app entirely. The probe stays a single short locator list on purpose (ADR
        0011 measured each missed candidate at ~1s), the foreground guard re-activates
        Boss if a Back press did escape, and the settle pause after either action is
        what stops the loop degenerating into a runaway burst of clicks.
        """
        self._ensure_foreground()
        back = self.find_now(BACK_BUTTON_KEY)
        if back:
            self.gestures.human_click(back)
        else:
            self.press_back()
        self.gestures.random_sleep(*BACK_INTERVAL_SEC)

    def _find_message_cards(self) -> list[Any]:
        """Locate communication rows, falling back to the message nodes' parents."""
        cards = self._find_elements_by_key("communication_list.message_card")
        if cards:
            return cards
        text_nodes = self._find_elements_by_key("communication_list.message_text")
        if not text_nodes:
            return []
        resolved: list[Any] = []
        for node in text_nodes:
            try:
                parent = node.find_element(by="xpath", value="..")
                resolved.append(parent if parent else node)
            except Exception:
                resolved.append(node)
        return resolved

    def extract_visible_messages(self, max_items: int = 10) -> list[CommunicationCard]:
        """Extract every visible card's sender, outbound badge, descriptor and text."""
        if not self.driver:
            return []

        cards: list[CommunicationCard] = []
        for card in self._find_message_cards()[:max_items]:
            row_text = (getattr(card, "text", "") or "").strip()
            sender = self._extract_card_field_text(card, "communication_list.sender_name")
            text = self._extract_card_field_text(card, "communication_list.message_text")
            if not text:
                # A card may itself be the message node (fallback locator path).
                text = row_text
            if not text:
                continue
            cards.append(
                CommunicationCard(
                    sender_name=sender,
                    message_text=text,
                    element=card,
                    row_text=row_text,
                    outbound_status=self._extract_card_field_text(
                        card, "communication_list.outbound_status"
                    ),
                    company_position=self._extract_company_position(card, sender, text),
                )
            )
        return cards

    def _scan_card_text_nodes(self, card: Any) -> list[str]:
        """The text of a card's child TextViews, in document order.

        The fallback behind the fields the platform leaves unlabelled: the descriptor
        is rendered into a text node with no resource-id measured on hardware, and a
        node walk is what reads it. A card whose layer cannot be read at all yields
        nothing, which the caller reads as "this field is not on the card" rather
        than as an error.
        """
        try:
            children = card.find_elements(by="xpath", value=CARD_CHILD_TEXT_XPATH)
        except Exception:
            return []
        return [(getattr(child, "text", "") or "").strip() for child in children]

    def _extract_company_position(self, card: Any, sender: str, message_text: str) -> str:
        """Read the `[Company] | [Position]` descriptor off a card.

        Falls back to the card's own child nodes when the configured resource-ids
        miss, because the descriptor node has no measured id on a live device.

        The sender, the message and the rendered row are all excluded: a rejection
        message may itself contain a `|`, and mistaking the message for an employer
        would blacklist a company that was never named on the card.

        Fullwidth bars are folded onto the ASCII separator before every comparison --
        the separator is a rendered glyph, and normalising only the configured field
        would leave the node scan blind on a device that draws `｜`.
        """
        descriptor = _normalize_card_descriptors(
            self._extract_card_field_text(card, "communication_list.company_position")
        )
        if CARD_DESCRIPTOR_SEPARATOR in descriptor:
            return descriptor

        message = _normalize_card_descriptors((message_text or "").strip())
        sender = (sender or "").strip()
        for candidate in self._scan_card_text_nodes(card):
            candidate = _normalize_card_descriptors(candidate)
            if not candidate or candidate == sender:
                continue
            if message and (candidate in message or message in candidate):
                continue
            if CARD_DESCRIPTOR_SEPARATOR in candidate:
                return candidate
        return ""

    def open_message(self, message: CommunicationCard) -> bool:
        """Click a communication card to enter its chat dialog."""
        if message.element is None:
            return False
        self.gestures.human_click(message.element)
        return True

    def mark_disinterest(self, timeout_sec: float = 3.0) -> bool:
        """Submit disinterest feedback with the standardized 重复推荐 reason.

        Fails fast rather than guessing: if either the 不感兴趣 button or the
        reason option does not appear, the sequence aborts without a second click
        so the caller can report the unexpected UI. The reason is deliberately not
        a parameter: the platform feedback category is standardized and never
        branched on.
        """
        button = self.find_by_key("chat.disinterest_btn", timeout_sec=timeout_sec)
        if not button:
            return False
        self.gestures.human_click(button)

        option = self.find_by_key(
            "chat.disinterest_reason_option",
            timeout_sec=timeout_sec,
            format_args={"reason": DISINTEREST_REASON},
        )
        if not option:
            return False
        self.gestures.human_click(option)
        return True
