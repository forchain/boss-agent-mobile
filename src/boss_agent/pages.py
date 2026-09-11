import contextlib
import re
import time
from dataclasses import dataclass, field
from typing import Any

from droid_agent_core.gestures import (
    BézierTouchSynthesizer,
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

from .models import AuthStatus, FilterConfig, JobPosting, compute_job_fingerprint


@dataclass
class JobCardBrief:
    """Lightweight extraction from a job card in search/list view for deduplication."""

    title: str
    company_name: str
    recruiter_name: str
    element: Any = None
    fingerprint: str = ""
    salary_range: str = ""
    location: str = ""
    tags: list[str] = field(default_factory=list)
    digest: str = ""
    snippet: str = ""
    company_scale: str = ""
    industry: str = ""
    recruiter_title: str = ""
    is_headhunter: bool = False

    def __post_init__(self) -> None:
        if self.title:
            self.title = clean_job_title(self.title)
        if self.recruiter_name and any(sep in self.recruiter_name for sep in ("·", "•", "・")):
            parts = [p.strip() for p in re.split(r"[·•・]", self.recruiter_name, maxsplit=1)]
            self.recruiter_name = parts[0].rstrip("·•・").strip()
            if not self.recruiter_title and len(parts) > 1 and parts[1]:
                self.recruiter_title = parts[1].strip()
        elif self.recruiter_name:
            self.recruiter_name = self.recruiter_name.rstrip("·•・").strip()

        if not self.digest and self.snippet:
            self.digest = self.snippet
        elif self.digest and not self.snippet:
            self.snippet = self.digest

        if not self.is_headhunter and (
            "猎头" in (self.recruiter_title or "") or "猎头" in (self.recruiter_name or "")
        ):
            self.is_headhunter = True
        if not self.fingerprint:
            self.fingerprint = compute_job_fingerprint(
                company_name=self.company_name,
                title=self.title,
                recruiter_name=self.recruiter_name,
            )


KNOWN_CITIES = (
    "上海", "北京", "深圳", "广州", "杭州", "成都", "武汉", "南京", "苏州", "西安",
    "重庆", "天津", "长沙", "厦门", "合肥", "青岛", "郑州", "大连", "海外", "远程",
)

RECRUITER_TITLE_KEYWORDS = (
    "猎头", "顾问", "专员", "专家", "HR", "招聘", "经理", "主管", "总监",
    "助理", "VP", "合伙人", "Recruiter", "Leader", "HRBP", "负责人",
)


def clean_job_title(raw_title: str) -> str:
    """Clean job title by stripping trailing status badges, tag placeholders like '&@', '@%', and excess punctuation.

    Examples:
        "技术负责人-CTO级别｜pre-ipo公司｜医疗AI &@" -> "技术负责人-CTO级别｜pre-ipo公司｜医疗AI"
        "CTO，外企AI Startup，可远程办公 &@"        -> "CTO，外企AI Startup，可远程办公"
        "算法高级工程师-DataAgent &@  &@"             -> "算法高级工程师-DataAgent"
        "外企-全栈开发工程师-不加班1075 @%"         -> "外企-全栈开发工程师-不加班1075"
    """
    if not raw_title:
        return ""
    if not isinstance(raw_title, str):
        raw_title = str(raw_title)
    t = raw_title.strip()
    while True:
        cleaned = re.sub(r"[\s&@%]+$", "", t).strip()
        if cleaned == t:
            break
        t = cleaned
    return t


def is_likely_location(s: str) -> bool:
    """Check if a string is likely a geographic location rather than a recruiter title or company info."""
    if not s:
        return False
    t = s.strip()
    if any(kw in t for kw in RECRUITER_TITLE_KEYWORDS):
        return False
    return (
        t in KNOWN_CITIES
        or t.endswith("市")
        or t.endswith("区")
        or t.endswith("县")
        or any(c in t for c in KNOWN_CITIES)
    )


def parse_recruiter_info(raw_text: str) -> tuple[str, str, bool]:
    """Parse raw recruiter text into (name, title, is_headhunter).

    Strictly determines is_headhunter based on whether '猎头' appears
    in the recruiter's title or name.

    Examples:
        "钟先生 · 猎头顾问" -> ("钟先生", "猎头顾问", True)
        "钟先生·猎头顾问"   -> ("钟先生", "猎头顾问", True)
        "钟先生 ·"          -> ("钟先生", "", False)
        "农女士 · 高级招聘专员" -> ("农女士", "高级招聘专员", False)
        "冯女士 · 总经理助理 上海" -> ("冯女士", "总经理助理", False)
        "张先生"             -> ("张先生", "", False)
    """
    raw = (raw_text or "").strip()
    if not raw:
        return "", "", False

    # Strip trailing city if attached like "冯女士 · 总经理助理 上海"
    trailing_city = False
    parts_by_space = raw.split()
    if len(parts_by_space) > 2:
        last_tok = parts_by_space[-1]
        if is_likely_location(last_tok):
            raw = " ".join(parts_by_space[:-1]).strip()
            trailing_city = True

    name = raw
    title = ""

    if any(sep in raw for sep in ("·", "•", "・")):
        tokens = [t.strip() for t in re.split(r"[·•・]", raw, maxsplit=1)]
        name = tokens[0].strip().rstrip("·•・").strip()
        title = tokens[1].strip() if len(tokens) > 1 else ""
    elif " " in raw:
        tokens = [t.strip() for t in raw.split(None, 1)]
        name = tokens[0].strip().rstrip("·•・").strip()
        title = tokens[1].strip() if len(tokens) > 1 else ""
    else:
        name = raw.rstrip("·•・").strip()

    # Clean any trailing city from title if not already stripped
    if not trailing_city and title and " " in title:
        sub_toks = title.rsplit(" ", 1)
        if is_likely_location(sub_toks[1]):
            title = sub_toks[0].strip()

    is_headhunter = "猎头" in title or "猎头" in name
    return name, title, is_headhunter


def parse_company_scale_industry(
    company_text: str,
    explicit_scale: str = "",
    explicit_industry: str = "",
) -> tuple[str, str, str]:
    """Parse company name, company scale, and industry from raw text or explicit parameters.

    Examples:
        "某中型人工智能公司 100-499人 人工智能" -> ("某中型人工智能公司", "100-499人", "人工智能")
        "深至科技 100-499人 人工智能" -> ("深至科技", "100-499人", "人工智能")
        "深至科技", "100-499人", "人工智能" -> ("深至科技", "100-499人", "人工智能")
    """
    raw = (company_text or "").strip()
    scale = (explicit_scale or "").strip()
    industry = (explicit_industry or "").strip()

    if not raw:
        return "", scale, industry

    # Pattern matching scale like "100-499人", "10000人以上", "少于50人", "20-99人"
    scale_pattern = r"(\d+[-~至]\d+人|\d+人以上|少于\d+人|\d+人以下|\d+人)"
    m = re.search(scale_pattern, raw)
    if m:
        matched_scale = m.group(1)
        if not scale:
            scale = matched_scale
        # Split into before scale and after scale
        parts = raw.split(matched_scale, 1)
        comp_name = parts[0].strip()
        rem = parts[1].strip() if len(parts) > 1 else ""
        if rem and not industry:
            industry = rem
        return comp_name or raw, scale, industry

    # If scale was already provided explicitly and exists in raw company string
    if scale and scale in raw:
        parts = raw.split(scale, 1)
        comp_name = parts[0].strip()
        rem = parts[1].strip() if len(parts) > 1 else ""
        if rem and not industry:
            industry = rem
        return comp_name or raw, scale, industry

    # If industry was provided explicitly and exists at end of company string
    if industry and raw.endswith(industry) and len(raw) > len(industry) + 2:
        comp_name = raw[: -len(industry)].strip()
        return comp_name, scale, industry

    return raw, scale, industry


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

    def _extract_card_field_text(self, card_elem: Any, key: str) -> str:
        """Extract text from a sub-element inside a job card using configured selectors."""
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
                            if txt and txt not in ("猎", "新", "急", "热", "置顶"):
                                return txt
            except Exception:
                continue
        return ""

    def extract_visible_job_cards(self, max_cards: int = 10) -> list[JobCardBrief]:
        """Extract visible job card briefs (title, company, recruiter, salary, location, tags, snippet)."""
        if not self.driver:
            return []
        card_selectors = self.locators.get_selectors("job_list.job_card")
        cards: list[Any] = []
        for sel in card_selectors:
            try:
                elems = self.driver.find_elements(by=sel.by.value, value=sel.value)
                if elems:
                    cards = elems
                    break
            except Exception:
                continue

        briefs: list[JobCardBrief] = []
        for card_elem in cards[:max_cards]:
            # Priority 1: Direct sub-element extraction by configured locator keys
            title = clean_job_title(self._extract_card_field_text(card_elem, "job_list.card_title"))
            raw_company = self._extract_card_field_text(card_elem, "job_list.card_company")
            raw_scale = self._extract_card_field_text(card_elem, "job_list.card_scale")
            raw_industry = self._extract_card_field_text(card_elem, "job_list.card_industry")
            salary = self._extract_card_field_text(card_elem, "job_list.card_salary")
            raw_recruiter = self._extract_card_field_text(card_elem, "job_list.card_recruiter")
            location = self._extract_card_field_text(card_elem, "job_list.card_location")
            snippet = self._extract_card_field_text(card_elem, "job_list.card_snippet")

            company, scale, industry = parse_company_scale_industry(
                raw_company, explicit_scale=raw_scale, explicit_industry=raw_industry
            )
            recruiter_name, recruiter_title, is_headhunter = parse_recruiter_info(raw_recruiter)

            # Safeguard: if location element was extracted but contains recruiter title words
            if location and not is_likely_location(location):
                if any(kw in location for kw in RECRUITER_TITLE_KEYWORDS):
                    if not recruiter_title:
                        recruiter_title = location
                    if "猎头" in location:
                        is_headhunter = True
                location = ""

            tags: list[str] = []
            with contextlib.suppress(Exception):
                tag_containers = self.locators.get_selectors("job_list.card_tags_container")
                for t_sel in tag_containers:
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

            # Priority 2: Fallback to text heuristic parsing IF title is still missing
            if not title:
                sub_texts: list[str] = []
                with contextlib.suppress(Exception):
                    sub_texts = [
                        e.text.strip()
                        for e in card_elem.find_elements(by="xpath", value=".//*[@text]")
                        if getattr(e, "text", None) and e.text.strip()
                    ]

                if not sub_texts:
                    raw_text = getattr(card_elem, "text", "") or ""
                    sub_texts = [line.strip() for line in raw_text.splitlines() if line.strip()]

                clean_texts = [t for t in sub_texts if t not in ("猎", "新", "急", "热", "置顶")]

                for t in clean_texts:
                    # 1. Salary detection
                    if not salary and (
                        re.search(r"\d+[-~至]\d+.*[万千Kk元薪]", t)
                        or re.search(r"^\d+.*[万千Kk元薪]", t)
                        or ("元" in t and any(c.isdigit() for c in t))
                        or ("K" in t and any(c.isdigit() for c in t))
                    ):
                        salary = t
                        continue

                    # 2. Title detection (first non-salary substantive text is always job title)
                    if not title:
                        title = clean_job_title(t)
                        continue

                    # 3. Company line detection (includes scale / industry heuristic)
                    if not company:
                        c_name, c_scale, c_ind = parse_company_scale_industry(t)
                        company = c_name
                        if c_scale and not scale:
                            scale = c_scale
                        if c_ind and not industry:
                            industry = c_ind
                        continue

                    # 4. Recruiter & attached location detection (e.g. "钟先生 · 猎头顾问" or "冯女士·总经理助理 上海")
                    if not recruiter_name and (
                        any(sep in t for sep in ("·", "•", "・"))
                        or any(kw in t for kw in RECRUITER_TITLE_KEYWORDS)
                    ):
                        r_name, r_title, r_hh = parse_recruiter_info(t)
                        recruiter_name = r_name
                        if r_title:
                            recruiter_title = r_title
                        if r_hh:
                            is_headhunter = True
                        parts = t.rsplit(" ", 1)
                        if (
                            not location
                            and len(parts) == 2
                            and is_likely_location(parts[1])
                        ):
                            location = parts[1].strip()
                        continue

                    # 5. Location detection if standalone
                    if not location and (
                        t in ("上海", "北京", "深圳", "广州", "杭州", "成都", "武汉", "南京", "苏州", "西安", "海外")
                        or t.endswith("市")
                        or t.endswith("区")
                    ):
                        location = t
                        continue

                    # 6. Standalone scale or industry detection
                    if re.search(r"(\d+[-~至]\d+人|\d+人以上|少于\d+人|\d+人以下)", t) and not scale:
                        scale = t
                        continue

                    # 7. Tags vs Snippet
                    if any(kw in t for kw in ("年", "应届", "经验", "本科", "大专", "硕士", "博士", "学历")):
                        tags.append(t)
                    elif len(t) > 10 and not snippet:
                        snippet = t
                    elif len(t) <= 12:
                        tags.append(t)
                    elif not snippet:
                        snippet = t

            # Skip incomplete or partially visible cards without genuine company name
            if title and company and company.strip() not in ("", "未知公司"):
                briefs.append(
                    JobCardBrief(
                        title=title,
                        company_name=company.strip(),
                        recruiter_name=recruiter_name or "招聘者",
                        salary_range=salary,
                        location=location,
                        tags=tags,
                        digest=snippet,
                        snippet=snippet,
                        company_scale=scale,
                        industry=industry,
                        recruiter_title=recruiter_title,
                        is_headhunter=is_headhunter,
                        element=card_elem,
                    )
                )
        return briefs



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
        # 1. First attempt: standard explicit expand button if visible
        elem = self.find_by_key("job_detail.expand_btn", timeout_sec=0.5)
        if elem:
            self.gestures.human_click(elem)
            return

        # 2. Second attempt: inline ClickableSpan probe within tv_description
        desc_elem = self.find_by_key("job_detail.desc", timeout_sec=1.0)
        if not desc_elem:
            return

        initial_text = getattr(desc_elem, "text", "") or ""
        # Only probe if text indicates it is truncated/collapsed
        if not ("查看更多" in initial_text or "展开" in initial_text or initial_text.endswith("...")):
            return

        rect = getattr(desc_elem, "rect", None)
        if not (
            isinstance(rect, dict)
            and all(
                k in rect and isinstance(rect[k], int | float)
                for k in ("x", "y", "width", "height")
            )
        ):
            return

        # Viewport safety check: ensure bottom edge of tv_description is not offscreen
        win_size = self._get_window_size()
        screen_height = win_size.get("height", 2400)
        screen_width = win_size.get("width", 1080)

        elem_bottom = float(rect["y"]) + float(rect["height"])
        if elem_bottom > screen_height - 150:
            scroll_dist = min(int(elem_bottom - (screen_height * 0.7)), int(screen_height * 0.3))
            if scroll_dist > 50:
                mid_x = screen_width // 2
                start_y = int(screen_height * 0.7)
                end_y = max(int(screen_height * 0.3), start_y - scroll_dist)
                self.gestures.human_swipe(
                    Point(mid_x, start_y),
                    Point(mid_x, end_y),
                    duration_ms=400,
                )
                desc_elem = self.find_by_key("job_detail.desc", timeout_sec=1.0)
                if not desc_elem:
                    return
                rect = getattr(desc_elem, "rect", None)
                if not (
                    isinstance(rect, dict)
                    and all(
                        k in rect and isinstance(rect[k], int | float)
                        for k in ("x", "y", "width", "height")
                    )
                ):
                    return

        # Load probe configurations from locator registry
        probe_cfg = self.locators.get_raw("job_detail.inline_expand_probes") or {}
        origin = probe_cfg.get("origin", "bottom-left") if isinstance(probe_cfg, dict) else "bottom-left"
        default_points = [
            [0.90, 20],
            [0.90, 60],
            [0.70, 20],
            [0.25, 20],
            [0.50, 60],
        ]
        points = (
            probe_cfg.get("points", default_points)
            if isinstance(probe_cfg, dict)
            else default_points
        )

        for pt in points:
            target_x, target_y = calculate_probe_coordinate(rect, pt, origin=origin)
            self.gestures.human_click_at_point(target_x, target_y, jitter_px=3.0)

            # Re-read text to verify early stopping condition
            time.sleep(0.3)
            try:
                curr_text = getattr(desc_elem, "text", "") or ""
            except Exception:
                refreshed = self.find_by_key("job_detail.desc", timeout_sec=0.5)
                curr_text = getattr(refreshed, "text", "") if refreshed else ""
                if refreshed:
                    desc_elem = refreshed

            if "查看更多" not in curr_text or len(curr_text) >= len(initial_text) + 10:
                break

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
