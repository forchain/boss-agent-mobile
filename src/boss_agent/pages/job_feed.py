"""
pages.job_feed
==============
Job discovery screens: the recommendation list, search entry, and the two filter dialogs
(spec #395, ticket #398).

Everything a job-search run touches lives here, so importing `JobListPage` no longer drags in the
communication list or the job-detail commute probe. `LocatedJobCard` is the feed's hand-off type: a
parsed `JobCardBrief` plus the opaque device element it was read from.
"""

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from droid_agent_core.gestures import Point
from droid_agent_core.locators import wait_until

from ..card_parser import CardFacets, ParsedCard, needs_text_fallback, parse_card
from ..job_entities import JobCardBrief
from ..search_entities import FilterConfig
from .base import BACK_INTERVAL_SEC, BaseBossPage, logger


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
            text_nodes = (
                self._read_card_text_nodes(card_elem) if needs_text_fallback(facets) else []
            )
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
