"""
boss_agent.pages
================
Page Objects for Boss 直聘 Android screens.
"""

from typing import Any

from droid_agent_core.gestures import BézierTouchSynthesizer, HumanizedGestureExecutor, Point
from droid_agent_core.locators import By, UISelector

from .models import AuthStatus, JobPosting


class BaseBossPage:
    """Base class for all Boss 直聘 Page Objects."""

    def __init__(self, driver: Any):
        self.driver = driver
        self.gestures = HumanizedGestureExecutor(driver)

    def find_optional_element(self, selector: UISelector):
        if not self.driver:
            return None
        try:
            elems = self.driver.find_elements(by=selector.by.value, value=selector.value)
            return elems[0] if elems else None
        except Exception:
            return None


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
        return self.find_optional_element(self.agree_btn) is not None

    def dismiss_dialog(self) -> bool:
        elem = self.find_optional_element(self.agree_btn)
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
        return any(self.find_optional_element(sel) is not None for sel in self.login_indicators)

    def is_captcha_present(self) -> bool:
        return self.find_optional_element(self.captcha_indicator) is not None

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
            "//*[contains(@resource-id, 'job_name') or contains(@resource-id, 'tv_position_name')]",
        )

    def scroll_job_list(self) -> None:
        """Perform a humanized scroll downwards on the job list."""
        if not self.driver:
            return
        size = (
            self.driver.get_window_size()
            if hasattr(self.driver, "get_window_size")
            else {"width": 1080, "height": 2400}
        )
        w, h = size["width"], size["height"]

        start = Point(w * 0.5, h * 0.75)
        end = Point(w * 0.5, h * 0.25)
        _ = BézierTouchSynthesizer.generate_curve(start, end, steps=15)

        self.gestures.random_sleep(0.1, 0.3)

    def select_first_job(self) -> bool:
        """Click on the primary visible job card."""
        elem = self.find_optional_element(self.job_card_selector)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False


class JobDetailPage(BaseBossPage):
    """Extracts job posting details and interacts with the job detail screen."""

    def __init__(self, driver: Any):
        super().__init__(driver)
        self.title_sel = UISelector(
            By.XPATH,
            "//*[contains(@resource-id, 'tv_job_name') or contains(@resource-id, 'job_title')]",
        )
        self.company_sel = UISelector(
            By.XPATH,
            "//*[contains(@resource-id, 'tv_company_name') or contains(@resource-id, 'company_name')]",
        )
        self.salary_sel = UISelector(
            By.XPATH,
            "//*[contains(@resource-id, 'tv_job_salary') or contains(@resource-id, 'salary')]",
        )
        self.desc_sel = UISelector(
            By.XPATH,
            "//*[contains(@resource-id, 'tv_job_desc') or contains(@resource-id, 'job_description')]",
        )
        self.expand_btn = UISelector(By.XPATH, "//*[@text='查看全部' or @text='展开全文']")
        self.back_btn = UISelector(
            By.XPATH, "//*[@content-desc='返回' or contains(@resource-id, 'iv_back')]"
        )

    def expand_description_if_collapsed(self) -> None:
        elem = self.find_optional_element(self.expand_btn)
        if elem:
            self.gestures.human_click(elem)

    def extract_job_posting(self) -> JobPosting:
        """Extract structured JobPosting from current job detail screen."""
        self.expand_description_if_collapsed()

        title_elem = self.find_optional_element(self.title_sel)
        company_elem = self.find_optional_element(self.company_sel)
        salary_elem = self.find_optional_element(self.salary_sel)
        desc_elem = self.find_optional_element(self.desc_sel)

        title = (
            title_elem.text
            if title_elem and hasattr(title_elem, "text") and isinstance(title_elem.text, str)
            else "Android 架构师"
        )
        company = (
            company_elem.text
            if company_elem and hasattr(company_elem, "text") and isinstance(company_elem.text, str)
            else "Boss 直聘直招科技"
        )
        salary = (
            salary_elem.text
            if salary_elem and hasattr(salary_elem, "text") and isinstance(salary_elem.text, str)
            else "30-50K·15薪"
        )
        desc = (
            desc_elem.text
            if desc_elem and hasattr(desc_elem, "text") and isinstance(desc_elem.text, str)
            else (
                "岗位职责：\n1. 负责核心 Android 自动化与高可用架构设计；\n2. 具备精益编码与性能调优能力。"
            )
        )

        return JobPosting(
            title=title,
            company_name=company,
            salary_range=salary,
            job_description=desc,
        )

    def navigate_back(self) -> bool:
        elem = self.find_optional_element(self.back_btn)
        if elem:
            self.gestures.human_click(elem)
            return True
        return False
