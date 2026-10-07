"""
pages.system
============
Startup and authentication readiness (spec #395, ticket #398).

`StartupDialogPage` clears the privacy and permission dialogs the app shows before any screen is
usable; `LoginPage` reports whether a run is authenticated, logged out, or facing a challenge. The
`check_login` handler needs both, and neither knows anything about job feeds or chats.
"""

from ..enums import AuthStatus
from .base import BaseBossPage


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
