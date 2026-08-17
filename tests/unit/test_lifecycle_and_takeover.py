"""Unit tests for Boss App lifecycle, Page Objects, and TakeoverHandler."""

from unittest.mock import MagicMock

from boss_agent.models import AuthStatus, JobPosting
from boss_agent.pages import (
    LoginPage,
    StartupDialogPage,
)
from boss_agent.workflows import TakeoverHandler


def test_job_posting_dataclass():
    job = JobPosting(
        title="Python 后端架构师",
        company_name="某知名互联网科技公司",
        salary_range="35-50K·16薪",
        job_description="负责核心移动端自动化架构设计与高并发微服务开发。",
        location="北京·朝阳区",
    )
    assert job.title == "Python 后端架构师"
    assert "35-50K" in job.salary_range
    assert len(job.job_description) >= 20


def test_startup_dialog_page_detection_and_dismissal():
    mock_driver = MagicMock()
    mock_elem = MagicMock()
    mock_elem.rect = {"x": 100, "y": 200, "width": 50, "height": 30}
    mock_driver.find_elements.return_value = [mock_elem]

    page = StartupDialogPage(mock_driver)
    assert page.is_dialog_present() is True

    dismissed = page.dismiss_dialog()
    assert dismissed is True


def test_login_page_detection():
    mock_driver = MagicMock()
    mock_login_elem = MagicMock()

    def mock_find_elements(by, value):
        if "手机号登录" in value:
            return [mock_login_elem]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements

    login_page = LoginPage(mock_driver)
    assert login_page.is_login_screen() is True
    assert login_page.is_captcha_present() is False
    assert login_page.get_auth_status() == AuthStatus.UNAUTHENTICATED


def test_takeover_handler_triggers_on_unauthenticated():
    mock_driver = MagicMock()
    mock_login_elem = MagicMock()

    def mock_find_elements(by, value):
        if "手机号登录" in value:
            return [mock_login_elem]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements

    handler = TakeoverHandler(mock_driver, auto_confirm_for_test=True)
    status = handler.check_and_handle_takeover()
    assert status == AuthStatus.AUTHENTICATED
