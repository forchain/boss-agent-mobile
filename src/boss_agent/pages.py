"""
boss_agent.pages
================
Page Objects for Boss 直聘 Android screens with robust wait_until synchronization.
"""

from typing import Any

from droid_agent_core.gestures import BézierTouchSynthesizer, HumanizedGestureExecutor, Point
from droid_agent_core.locators import By, UISelector, wait_until

from .models import AuthStatus, FilterConfig, JobPosting


class BaseBossPage:
    """Base class for all Boss 直聘 Page Objects."""

    def __init__(self, driver: Any):
        self.driver = driver
        self.gestures = HumanizedGestureExecutor(driver)

    def _get_window_size(self) -> dict[str, int]:
        """Get the current screen window dimensions with safe default fallback."""
        if hasattr(self.driver, "get_window_size"):
            try:
                size = self.driver.get_window_size()
                return {"width": int(size["width"]), "height": int(size["height"])}
            except Exception:
                pass
        return {"width": 1080, "height": 2400}

    def _find_single(self, selector: UISelector):
        try:
            elems = self.driver.find_elements(by=selector.by.value, value=selector.value)
            return elems[0] if elems else None
        except Exception:
            return None

    def find_optional_element(self, selector: UISelector, timeout_sec: float = 0.0):
        if not self.driver:
            return None
        if timeout_sec > 0:
            try:
                return wait_until(
                    lambda: self._find_single(selector),
                    timeout_sec=timeout_sec,
                    error_message=f"Element not found: {selector.description or selector.value}",
                )
            except TimeoutError:
                return None
        return self._find_single(selector)

    def wait_for_element(self, selector: UISelector, timeout_sec: float = 10.0):
        """Wait until an element matching the selector is found on the current screen."""
        if not self.driver:
            raise RuntimeError("Driver session is not initialized")
        return wait_until(
            lambda: self._find_single(selector),
            timeout_sec=timeout_sec,
            error_message=f"Timed out waiting for element: {selector.description or selector.value}",
        )


class StartupDialogPage(BaseBossPage):
    """Handles startup privacy policy and permission dialogs."""

    def __init__(self, driver: Any):
        super().__init__(driver)
        self.agree_btn = UISelector(
            By.XPATH,
            "//*[@text='同意' or @text='同意并继续' or @text='好的']",
            "Agree and continue button",
        )

    def is_dialog_present(self) -> bool:
        return self.find_optional_element(self.agree_btn, timeout_sec=2.0) is not None

    def dismiss_dialog(self) -> bool:
        elem = self.find_optional_element(self.agree_btn, timeout_sec=2.0)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False


class LoginPage(BaseBossPage):
    """Detects login state and authentication challenges."""

    def __init__(self, driver: Any):
        super().__init__(driver)
        self.login_indicators = [
            UISelector(
                By.XPATH, "//*[@text='手机号登录' or @text='验证码登录' or @text='密码登录']"
            ),
            UISelector(By.XPATH, "//*[@text='微信登录' or @text='其他登录方式']"),
        ]
        self.captcha_indicator = UISelector(
            By.XPATH, "//*[@text='拖动滑块完成拼图' or @text='安全验证' or @text='向右滑动']"
        )

    def is_login_screen(self) -> bool:
        return any(
            self.find_optional_element(sel, timeout_sec=1.0) is not None
            for sel in self.login_indicators
        )

    def is_captcha_present(self) -> bool:
        return self.find_optional_element(self.captcha_indicator, timeout_sec=1.0) is not None

    def get_auth_status(self) -> AuthStatus:
        if self.is_captcha_present():
            return AuthStatus.CHALLENGE_REQUIRED
        if self.is_login_screen():
            return AuthStatus.UNAUTHENTICATED
        return AuthStatus.AUTHENTICATED


class JobListPage(BaseBossPage):
    """Interacts with the main job recommendation/search list."""

    def __init__(self, driver: Any):
        super().__init__(driver)
        self.job_card_selector = UISelector(
            By.XPATH,
            "//*[contains(@resource-id, 'job_name') or contains(@resource-id, 'tv_position_name') or contains(@resource-id, 'cl_card_container') or contains(@resource-id, 'view_job_card')]",
            "Job card item",
        )
        self.job_tab_selector = UISelector(
            By.XPATH,
            "//*[@resource-id='com.hpbr.bosszhipin:id/tv_tab_1' or @text='职位']",
            "Bottom Job Tab",
        )
        self.search_icon_selectors = [
            UISelector(
                By.XPATH,
                "//android.widget.LinearLayout[@resource-id='com.hpbr.bosszhipin:id/ly_menu']/*[last()]",
                "Top search icon in header menu",
            ),
            UISelector(
                By.XPATH,
                "//*[@resource-id='com.hpbr.bosszhipin:id/ly_menu']/android.widget.ImageView[2]",
                "Top search icon",
            ),
            UISelector(
                By.XPATH,
                "//*[@content-desc='搜索' or contains(@resource-id, 'search')]",
                "Generic search button",
            ),
        ]

    def ensure_job_tab(self) -> bool:
        """Ensure the user is on the primary '职位' (Job) navigation tab."""
        elem = self.find_optional_element(self.job_tab_selector, timeout_sec=2.0)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False

    def open_search(self, timeout_sec: float = 10.0) -> bool:
        """Click the search icon in the top header to enter the search page."""
        for sel in self.search_icon_selectors:
            elem = self.find_optional_element(sel, timeout_sec=timeout_sec)
            if elem:
                self.gestures.human_click(elem)
                return True
        return False

    def wait_for_jobs_loaded(self, timeout_sec: float = 15.0) -> bool:
        """Wait until at least one job card is present on the screen."""
        if not self.driver:
            return False
        try:
            self.wait_for_element(self.job_card_selector, timeout_sec=timeout_sec)
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
        elem = self.find_optional_element(self.job_card_selector, timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False


class SearchPage(BaseBossPage):
    """Page Object for the Boss 直聘 job search screen."""

    def __init__(self, driver: Any):
        super().__init__(driver)
        self.search_input_sel = UISelector(
            By.XPATH,
            "//*[@resource-id='com.hpbr.bosszhipin:id/et_search' or @class='android.widget.EditText']",
            "Search input box",
        )
        self.search_btn_sel = UISelector(
            By.XPATH,
            "//*[@resource-id='com.hpbr.bosszhipin:id/tv_search' or @text='搜索']",
            "Search submit button",
        )
        self.clear_btn_sel = UISelector(
            By.XPATH,
            "//*[@resource-id='com.hpbr.bosszhipin:id/iv_clear']",
            "Clear text button",
        )
        self.back_btn_sel = UISelector(
            By.XPATH,
            "//*[@resource-id='com.hpbr.bosszhipin:id/iv_back_ai' or @content-desc='返回']",
            "Back button",
        )

    def is_search_page(self) -> bool:
        """Check if currently on the search input screen."""
        return self.find_optional_element(self.search_input_sel, timeout_sec=1.0) is not None

    def wait_for_search_page(self, timeout_sec: float = 10.0) -> bool:
        """Wait until search input box is present on screen."""
        try:
            self.wait_for_element(self.search_input_sel, timeout_sec=timeout_sec)
            return True
        except TimeoutError:
            return False

    def clear_input(self) -> bool:
        """Clear search input via clear icon or direct element clear."""
        clear_elem = self.find_optional_element(self.clear_btn_sel, timeout_sec=1.0)
        if clear_elem:
            self.gestures.human_click(clear_elem)
            return True
        input_elem = self.find_optional_element(self.search_input_sel, timeout_sec=1.0)
        if input_elem and hasattr(input_elem, "clear"):
            try:
                input_elem.clear()
                return True
            except Exception:
                pass
        return False

    def enter_keyword(self, keyword: str, timeout_sec: float = 10.0) -> bool:
        """Type search keyword into search input."""
        elem = self.find_optional_element(self.search_input_sel, timeout_sec=timeout_sec)
        if not elem:
            return False
        self.clear_input()
        self.gestures.human_click(elem)
        self.gestures.human_type(elem, keyword, clear_first=False)
        return True

    def submit_search(self, timeout_sec: float = 10.0) -> bool:
        """Submit the search by clicking the '搜索' button."""
        elem = self.find_optional_element(self.search_btn_sel, timeout_sec=timeout_sec)
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
        elem = self.find_optional_element(self.back_btn_sel, timeout_sec=2.0)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False


class FilterDialogPage(BaseBossPage):
    """Page Object for the Boss 直聘 Job Filter Dialog (筛选)."""

    def __init__(self, driver: Any):
        super().__init__(driver)
        self.filter_entry_sel = UISelector(
            By.XPATH,
            "//*[@resource-id='com.hpbr.bosszhipin:id/tv_title' and @text='筛选'] | //*[@text='筛选']",
            "Filter entry button",
        )
        self.confirm_btn_sel = UISelector(
            By.XPATH,
            "//*[@resource-id='com.hpbr.bosszhipin:id/btn_confirm' or @text='确定']",
            "Confirm filter button",
        )
        self.reset_btn_sel = UISelector(
            By.XPATH,
            "//*[@resource-id='com.hpbr.bosszhipin:id/btn_reset' or @text='清除']",
            "Reset filter button",
        )
        self.close_btn_sel = UISelector(
            By.XPATH,
            "//*[@resource-id='com.hpbr.bosszhipin:id/iv_close' or @content-desc='关闭']",
            "Close filter dialog",
        )

    def is_dialog_open(self) -> bool:
        """Check if filter dialog is currently open."""
        return self.find_optional_element(self.confirm_btn_sel, timeout_sec=1.0) is not None

    def open_filter(self, timeout_sec: float = 10.0) -> bool:
        """Click the '筛选' entry button to open the filter dialog and wait for it."""
        if self.is_dialog_open():
            return True
        elem = self.find_optional_element(self.filter_entry_sel, timeout_sec=timeout_sec)
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

        sel = UISelector(
            By.XPATH,
            f"//*[@resource-id='com.hpbr.bosszhipin:id/keywords_view_text' and (@text='{option_text}' or contains(@text, '{option_text}'))] | //*[@text='{option_text}']",
            f"Filter option {option_text}",
        )

        elem = self.find_optional_element(sel, timeout_sec=2.0)
        if elem:
            self.gestures.human_click(elem)
            return True

        if auto_scroll:
            self.scroll_dialog_down()
            elem = self.find_optional_element(sel, timeout_sec=2.0)
            if elem:
                self.gestures.human_click(elem)
                return True

        return False

    def confirm_filter(self, timeout_sec: float = 5.0) -> bool:
        """Click the '确定' button to apply chosen filters."""
        elem = self.find_optional_element(self.confirm_btn_sel, timeout_sec=timeout_sec)
        if elem:
            self.gestures.human_click(elem)
            try:
                wait_until(
                    lambda: not self.is_dialog_open(),
                    timeout_sec=5.0,
                    error_message="Filter dialog failed to close",
                )
            except TimeoutError:
                pass
            return True
        return False

    def reset_filter(self) -> bool:
        """Click the '清除' button to reset filters to default."""
        elem = self.find_optional_element(self.reset_btn_sel, timeout_sec=2.0)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False

    def close_dialog(self) -> bool:
        """Close the filter dialog without applying changes."""
        elem = self.find_optional_element(self.close_btn_sel, timeout_sec=2.0)
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


class JobDetailPage(BaseBossPage):
    """Extracts job posting details and interacts with the job detail screen."""

    def __init__(self, driver: Any):
        super().__init__(driver)
        self.title_sel = UISelector(
            By.XPATH,
            "//*[contains(@resource-id, 'tv_job_name') or contains(@resource-id, 'job_title')]",
            "Job Title",
        )
        self.company_sel = UISelector(
            By.XPATH,
            "//*[contains(@resource-id, 'tv_company_name') or contains(@resource-id, 'company_name')]",
            "Company Name",
        )
        self.salary_sel = UISelector(
            By.XPATH,
            "//*[contains(@resource-id, 'tv_job_salary') or contains(@resource-id, 'salary')]",
            "Salary Range",
        )
        self.desc_sel = UISelector(
            By.XPATH,
            "//*[contains(@resource-id, 'tv_job_desc') or contains(@resource-id, 'job_description')]",
            "Job Description",
        )
        self.expand_btn = UISelector(
            By.XPATH,
            "//*[@text='查看全部' or @text='展开全文']",
            "Expand Description Button",
        )
        self.back_btn = UISelector(
            By.XPATH,
            "//*[@content-desc='返回' or contains(@resource-id, 'iv_back')]",
            "Back Button",
        )

    def expand_description_if_collapsed(self) -> None:
        elem = self.find_optional_element(self.expand_btn, timeout_sec=1.0)
        if elem:
            self.gestures.human_click(elem)

    def extract_job_posting(self, timeout_sec: float = 10.0) -> JobPosting:
        """Extract structured JobPosting from current job detail screen.

        Raises RuntimeError if job details are not found on the screen.
        """
        # Explicit wait for title or salary element on detail page
        try:
            self.wait_for_element(self.title_sel, timeout_sec=timeout_sec)
        except TimeoutError:
            if not self.find_optional_element(self.salary_sel, timeout_sec=2.0):
                raise RuntimeError(
                    "Failed to extract job posting: Job detail screen did not load within timeout. "
                    "Ensure job card was clicked and navigation to detail screen completed."
                )

        self.expand_description_if_collapsed()

        title_elem = self.find_optional_element(self.title_sel)
        company_elem = self.find_optional_element(self.company_sel)
        salary_elem = self.find_optional_element(self.salary_sel)
        desc_elem = self.find_optional_element(self.desc_sel)

        title = title_elem.text.strip() if title_elem and getattr(title_elem, "text", None) else ""
        company = (
            company_elem.text.strip() if company_elem and getattr(company_elem, "text", None) else ""
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

    def navigate_back(self) -> bool:
        elem = self.find_optional_element(self.back_btn, timeout_sec=2.0)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False
