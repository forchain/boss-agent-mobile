import contextlib
import time
from typing import Any

from droid_agent_core.gestures import BézierTouchSynthesizer, HumanizedGestureExecutor, Point
from droid_agent_core.locators import (
    LocatorRegistry,
    UISelector,
    get_global_locator_registry,
    wait_until,
)

from .models import AuthStatus, FilterConfig, JobPosting


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
                return {"width": int(size["width"]), "height": int(size["height"])}
            except Exception:
                pass
        return {"width": 1080, "height": 2400}

    def _find_by_selectors(self, selectors: list[UISelector]):
        if not self.driver or not selectors:
            return None
        for sel in selectors:
            try:
                elems = self.driver.find_elements(by=sel.by.value, value=sel.value)
                if elems:
                    return elems[0]
            except Exception:
                continue
        return None

    def find_by_key(
        self,
        key: str,
        timeout_sec: float = 0.0,
        format_args: dict[str, Any] | None = None,
        default: str | list[str] | None = None,
    ):
        """Find an element using its configured key with automatic strategy detection."""
        selectors = self.locators.get_selectors(key, format_args=format_args, default=default)
        if not selectors:
            return None

        if timeout_sec > 0:
            try:
                return wait_until(
                    lambda: self._find_by_selectors(selectors),
                    timeout_sec=timeout_sec,
                    error_message=f"Element not found for key '{key}'",
                )
            except TimeoutError:
                return None
        return self._find_by_selectors(selectors)

    def wait_for_key(
        self,
        key: str,
        timeout_sec: float = 10.0,
        format_args: dict[str, Any] | None = None,
        default: str | list[str] | None = None,
    ):
        """Wait until an element for the given key is found on screen."""
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
        """Ensure the app navigates back to the primary Job Recommendation Home page.

        Handles:
        1. Dismissing open chat screens, job details, or dialogs if present.
        2. Clicking back buttons or driver back from subpages.
        3. Switching to the primary '职位' tab.
        """
        for _ in range(max_attempts):
            if self.is_on_home_page():
                self.ensure_job_tab()
                return True

            # Dismiss open filter / industry dialogs if present
            close_dialog_btn = self.find_by_key("filter.close_btn", timeout_sec=0.3)
            if close_dialog_btn:
                self.gestures.human_click(close_dialog_btn)
                time.sleep(0.3)
                continue

            cancel_industry_btn = self.find_by_key("industry.cancel_btn", timeout_sec=0.3)
            if cancel_industry_btn:
                self.gestures.human_click(cancel_industry_btn)
                time.sleep(0.3)
                continue

            # Look for chat/search/navigation/job_detail back button
            back_elem = self.find_by_key("chat.back_btn", timeout_sec=0.5)
            if not back_elem:
                back_elem = self.find_by_key("navigation.back_btn", timeout_sec=0.3)
            if not back_elem:
                back_elem = self.find_by_key("search.back_btn", timeout_sec=0.3)
            if not back_elem:
                back_elem = self.find_by_key("job_detail.back_btn", timeout_sec=0.3)

            if back_elem:
                self.gestures.human_click(back_elem)
                time.sleep(0.8)
            elif hasattr(self.driver, "back"):
                with contextlib.suppress(Exception):
                    self.driver.back()
                    time.sleep(0.8)

            # Try clicking job tab
            job_tab_elem = self.find_by_key("job_list.job_tab", timeout_sec=0.5)
            if job_tab_elem:
                self.gestures.human_click(job_tab_elem)
                time.sleep(0.5)

        self.ensure_job_tab()
        return self.is_on_home_page()

    def ensure_job_tab(self) -> bool:
        """Ensure the user is on the primary '职位' (Job) navigation tab."""
        elem = self.find_by_key("job_list.job_tab", timeout_sec=2.0)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False

    def open_search(self, timeout_sec: float = 10.0) -> bool:
        """Click the search icon in the top header to enter the search page."""
        elem = self.find_by_key("job_list.search_icon", timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False

    def wait_for_jobs_loaded(self, timeout_sec: float = 15.0) -> bool:
        """Wait until at least one job card is present on the screen."""
        if not self.driver:
            return False
        try:
            self.wait_for_key("job_list.job_card", timeout_sec=timeout_sec)
            return True
        except TimeoutError:
            return False

    def scroll_job_list(self) -> None:
        """Perform a humanized scroll downwards on the job list."""
        if not self.driver:
            return
        size = self._get_window_size()
        w, h = size["width"], size["height"]

        start = Point(w * 0.5, h * 0.75)
        end = Point(w * 0.5, h * 0.25)
        _ = BézierTouchSynthesizer.generate_curve(start, end, steps=15)

        self.gestures.random_sleep(0.1, 0.3)

    def select_first_job(self, timeout_sec: float = 10.0) -> bool:
        """Click on the primary visible job card."""
        elem = self.find_by_key("job_list.job_card", timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False


class SearchPage(BaseBossPage):
    """Page Object for the Boss 直聘 job search screen."""

    def is_search_page(self) -> bool:
        """Check if currently on the search input screen."""
        return self.find_by_key("search.search_input", timeout_sec=0.5) is not None

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
        """Find and click a filter option tag with optional auto-scroll."""
        if not option_text:
            return False

        def _try_click_option() -> bool:
            elem = self.find_by_key(
                "filter.option_item",
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
            self.scroll_dialog_down()
            return _try_click_option()

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

    def close_dialog(self) -> bool:
        """Close the filter dialog without applying changes."""
        elem = self.find_by_key("filter.close_btn", timeout_sec=2.0)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False

    def apply_filters(self, config: FilterConfig | None, timeout_sec: float = 10.0) -> bool:
        """Apply all specified filter dimensions in order."""
        if not config or not config.has_filters:
            return False

        if not self.is_dialog_open():
            opened = self.open_filter(timeout_sec=timeout_sec)
            if not opened:
                return False

        # 1. Top visible filters: Education, Salary, Experience
        if config.education:
            self.select_option(config.education, auto_scroll=False)
        if config.salary:
            self.select_option(config.salary, auto_scroll=False)
        if config.experience:
            self.select_option(config.experience, auto_scroll=False)

        # 2. Scroll down for bottom sections: Activity and Company Scales
        self.scroll_dialog_down()

        if config.activity:
            self.select_option(config.activity, auto_scroll=True)

        for scale in config.company_scales:
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

    def expand_description_if_collapsed(self) -> None:
        elem = self.find_by_key("job_detail.expand_btn", timeout_sec=1.0)
        if elem:
            self.gestures.human_click(elem)

    def extract_job_posting(self, timeout_sec: float = 10.0) -> JobPosting:
        """Extract structured JobPosting from current job detail screen.

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

        self.expand_description_if_collapsed()

        title_elem = self.find_by_key("job_detail.title")
        company_elem = self.find_by_key("job_detail.company")
        salary_elem = self.find_by_key("job_detail.salary")
        desc_elem = self.find_by_key("job_detail.desc")

        title = title_elem.text.strip() if title_elem and getattr(title_elem, "text", None) else ""
        company = (
            company_elem.text.strip()
            if company_elem and getattr(company_elem, "text", None)
            else ""
        )
        salary = (
            salary_elem.text.strip() if salary_elem and getattr(salary_elem, "text", None) else ""
        )
        desc = desc_elem.text.strip() if desc_elem and getattr(desc_elem, "text", None) else ""

        if not title and not salary:
            raise RuntimeError(
                "Failed to extract job posting: Both title and salary elements were empty or missing on the screen."
            )

        return JobPosting(
            title=title or "未注明职位",
            company_name=company or "未注明公司",
            salary_range=salary or "面议",
            job_description=desc or "无详细岗位描述",
        )

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

    def type_greeting_message(self, message: str, timeout_sec: float = 5.0) -> bool:
        """Type greeting message into the chat message input box.

        IMPORTANT SAFETY GUARANTEE: Does NOT click the send button.
        Allows user to review, edit, or manually send during testing.
        """
        elem = self.find_by_key("chat.message_input", timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_type(elem, message)
            return True
        return False

    def click_send(self, timeout_sec: float = 3.0) -> bool:
        """Click the send button on the chat page."""
        elem = self.find_by_key("chat.send_btn", timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False

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


