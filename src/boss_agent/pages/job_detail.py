"""
pages.job_detail
================
The job detail page and its commute-distance probe (spec #395, ticket #398).

`JobDetailPage` owns the expanded-JD read, the inline description hotspot probes, and the scroll
probe behind the commute ceiling (spec #209). The constants tuning that probe live beside the code
they bound, instead of at the top of a file where four unrelated features also lived.
"""

import re
import time
from collections.abc import Callable
from typing import Any

from droid_agent_core.gestures import Point, calculate_probe_coordinate
from droid_agent_core.locators import LocatorRegistry

from ..enums import ChatButtonState
from ..identifier_helpers import classify_chat_button, jd_is_truncated
from ..job_entities import JobLocationLine, JobPosting
from .base import BaseBossPage, _log_error, _log_info, _log_warn

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
            _log_info(
                f"📍 [Commute Probe] Parsed commute distance: {distance_km} km ('{raw_text}')"
            )
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
        desc_elem = None
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
            if not jd_is_truncated(self._current_description):
                return True
            _log_warn(
                f"⚠️ Explicit expand button did not fully expand description "
                f"('查看更多' still present, length {len(self._current_description)}); "
                f"falling back to bottom-right hotspot probing..."
            )

        # 2. Locate the job description TextView (com.hpbr.bosszhipin:id/tv_description)
        if not desc_elem:
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

        # One rule, owned by the domain model, and read by the stored-JD check too (#301).
        is_truncated = jd_is_truncated(initial_text)
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
        commute_probe_upgrade: Callable[[str], bool] | None = None,
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

        ``commute_probe_upgrade`` is consulted with the full location line once the header
        has been read, and may only turn the probe on (issue #333). The card's location
        facet names a district but no station, so a posting whose 商圈 nobody listed may
        still be one whose 地铁站 somebody did; asking here costs nothing, because the line
        is captured alongside the title and the swipe budget it can authorise is the
        expensive part.

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
        # The location line shares the header row, so it is read here for the same reason
        # the other three are: expanding the description can recycle them out of the tree.
        location_elem = self.find_now("job_detail.location_line")
        location_line = JobLocationLine.parse(self._read_location_line(location_elem))

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
        if not location_line.raw:
            # The zero-timeout read above covers the normal path; this one pays a real
            # lookup only for a posting whose line the tree had not built yet.
            location_elem = self.find_by_key("job_detail.location_line", timeout_sec=0.5)
            location_line = JobLocationLine.parse(self._read_location_line(location_elem))

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
        probe = probe_commute_distance
        if not probe and commute_probe_upgrade is not None and is_headhunter is not True:
            # Only ever an upgrade: the card stage may decline a district it could not
            # resolve to a station, but it must not be overruled by a line that lists none.
            probe = bool(commute_probe_upgrade(location_line.match_text))
            if probe:
                _log_info(
                    f"📍 [Commute Probe] '{location_line.match_text or title}' "
                    f"商圈未列入考察列表，但地铁站 '{location_line.metro_station or '未标注'}' "
                    f"命中，升级为需要底部探测"
                )
        if probe and is_headhunter is not True:
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
            location_line=location_line.raw,
            metro_lines=location_line.metro_lines,
            metro_station=location_line.metro_station,
        )

    @staticmethod
    def _read_location_line(elem: Any) -> str:
        """The rendered location line, or "" when there is nothing readable to parse.

        The node can be absent, or present without text — a container matched by the
        fallback XPath, or a row the platform has not populated yet. Both are ordinary
        states rather than failures, and both must read as "no station", so the location
        line fails open exactly as the commute widget does rather than raising on a node
        whose ``text`` is not a string.
        """
        raw = getattr(elem, "text", None) if elem is not None else None
        return raw.strip() if isinstance(raw, str) else ""

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
