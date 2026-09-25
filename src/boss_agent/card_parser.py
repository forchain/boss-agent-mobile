"""
boss_agent.card_parser
======================
Pure interpretation of a Boss 直聘 job card — no driver, no device, no page object.

Job-card extraction used to be one ~228-line method on the page object that fused
three unrelated things: locator reads, an eight-branch text-heuristic classifier, and
viewport-cutoff geometry. Only the last of those needs a device. Everything else is a
decision about text, and a decision about text should be table-testable without
fabricating an accessibility tree — which is what this module makes it.

The split is:

* :class:`CardFacets` — the priority-1 reads a page object can address with locators.
* :func:`parse_card` — ordered rules over those facets plus, when they are incomplete,
  the card's ordered text nodes.
* :func:`needs_text_fallback` — the one place that says when the text pass runs, so a
  caller cannot accidentally reorder the two stages.

Viewport-cutoff detection deliberately stays with the page object: it needs
``card_bottom`` against the window height, which is geometry rather than text.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .models import (
    KNOWN_CITIES,
    PLATFORM_BADGE_MARKERS,
    RECRUITER_SEPARATORS,
    RECRUITER_TITLE_KEYWORDS,
    clean_job_title,
    is_invalid_company_name,
    is_likely_location,
    sanitize_tags,
    split_recruiter_name,
)

#: Company-scale strings: "100-499人", "10000人以上", "少于50人", "20-99人", "50人".
SCALE_PATTERN = r"(\d+[-~至]\d+人|\d+人以上|少于\d+人|\d+人以下|\d+人)"
_SCALE_RE = re.compile(SCALE_PATTERN)

_SALARY_RE = (
    re.compile(r"\d+[-~至]\d+.*[万千Kk元薪]"),
    re.compile(r"^\d+.*[万千Kk元薪]"),
)

#: Words that mark a text node as an education/experience requirement rather than a tag.
REQUIREMENT_KEYWORDS: tuple[str, ...] = (
    "年",
    "应届",
    "经验",
    "本科",
    "大专",
    "硕士",
    "博士",
    "学历",
)

#: Longest text node still classified as a tag; longer ones are a digest. The two bounds
#: deliberately overlap with a gap — anything between them falls through to the snippet
#: branch below, which is the pre-existing rule and not an off-by-one to tidy up.
TAG_MAX_CHARS = 12
SNIPPET_MIN_CHARS = 10


@dataclass(frozen=True)
class CardFacets:
    """The priority-1 reads for one card: whatever the configured locators could address.

    Every field is the raw extracted string. Interpretation is :func:`parse_card`'s job,
    so a change to the rules never requires a change to how the card was read.
    """

    title: str = ""
    company: str = ""
    scale: str = ""
    industry: str = ""
    salary: str = ""
    recruiter: str = ""
    location: str = ""
    snippet: str = ""
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedCard:
    """A card's interpreted facets — the input every downstream decision is made from."""

    title: str
    company_name: str
    recruiter_name: str
    recruiter_title: str
    is_headhunter: bool
    salary_range: str
    location: str
    tags: tuple[str, ...]
    snippet: str
    company_scale: str
    industry: str


def needs_text_fallback(facets: CardFacets) -> bool:
    """Whether a card's locator reads were incomplete enough to need its text nodes.

    The fallback branch only fires when title *or* company is missing from the
    priority-1 reads. That ordering is load-bearing: a card whose locators resolved
    both fields is never re-interpreted from raw text, so a layout change cannot
    silently override an explicit read.

    The decision is made on the *interpreted* reads, not the raw ones: Boss's
    recommendation cards render the job title inside the company row, so a raw read
    can look complete while interpreting to no company at all. Answering from the raw
    read there would skip the text pass that exists to recover the real employer.
    """
    return _fallback_needed(_facets_to_draft(facets))


def _fallback_needed(draft: _Draft) -> bool:
    return not draft.title or not draft.company


# --------------------------------------------------------------------------- #
# Pure text classifiers
# --------------------------------------------------------------------------- #


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

    comp_name = ""

    m = _SCALE_RE.search(raw)
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
    elif scale and scale in raw:
        # If scale was already provided explicitly and exists in raw company string
        parts = raw.split(scale, 1)
        comp_name = parts[0].strip()
        rem = parts[1].strip() if len(parts) > 1 else ""
        if rem and not industry:
            industry = rem
    elif industry and raw.endswith(industry) and len(raw) > len(industry) + 2:
        # If industry was provided explicitly and exists at end of company string
        comp_name = raw[: -len(industry)].strip()
    else:
        comp_name = raw

    if comp_name and is_invalid_company_name(comp_name):
        comp_name = ""

    return comp_name, scale, industry


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
    if len(parts_by_space) > 2 and is_likely_location(parts_by_space[-1]):
        raw = " ".join(parts_by_space[:-1]).strip()
        trailing_city = True

    if any(sep in raw for sep in RECRUITER_SEPARATORS):
        name, title = split_recruiter_name(raw)
    elif " " in raw:
        # Some layouts separate name and title with a plain space instead. This branch
        # must stay *after* the separator check: "钟先生 ·" has a space and a separator,
        # and splitting on the space first would read the leftover separator as a title.
        head, _, tail = raw.partition(" ")
        name, _ = split_recruiter_name(head)
        title = tail.strip()
    else:
        name, _ = split_recruiter_name(raw)
        title = ""

    # Clean any trailing city from title if not already stripped
    if not trailing_city and title and " " in title:
        last_tok = title.rsplit(" ", 1)[1]
        if is_likely_location(last_tok):
            title = title.rsplit(" ", 1)[0].strip()

    is_headhunter = "猎头" in title or "猎头" in name
    return name, title, is_headhunter


def company_duplicates_card_title(company: str, title: str) -> bool:
    """True when an extracted 'company' is the card's own job title instead of an employer.

    Boss's recommendation popup cards reuse the position name in the company row, so a
    locator can read the title text back as a company. Left alone, it poisons the
    fingerprint (same job saved twice under different keys) and smuggles blacklisted
    employers past company-name screening. A trailing badge-junk suffix (' &@') is
    tolerated as the same mis-read, because a genuine employer does not begin with
    its own posting's full title.
    """
    c = (company or "").strip()
    t = (title or "").strip()
    if not c or not t:
        return False
    return c == t or (c.startswith(t) and len(c) - len(t) <= 3)


# --------------------------------------------------------------------------- #
# The ordered classifier
# --------------------------------------------------------------------------- #


@dataclass
class _Draft:
    """The working copy the rules mutate while the fallback classifier walks text nodes."""

    title: str = ""
    company: str = ""
    scale: str = ""
    industry: str = ""
    salary: str = ""
    recruiter_name: str = ""
    recruiter_title: str = ""
    is_headhunter: bool = False
    location: str = ""
    snippet: str = ""
    tags: list[str] = field(default_factory=list)


#: A rule claims a text node by returning True, which stops the walk for that node.
CardRule = Callable[[str, _Draft], bool]


def _rule_salary(text: str, draft: _Draft) -> bool:
    if draft.salary:
        return False
    if any(pattern.search(text) for pattern in _SALARY_RE):
        draft.salary = text
        return True
    if ("元" in text and any(c.isdigit() for c in text)) or (
        "K" in text and any(c.isdigit() for c in text)
    ):
        draft.salary = text
        return True
    return False


def _rule_recruiter(text: str, draft: _Draft) -> bool:
    """The recruiter line, including a city Boss glued onto the title.

    Fires on a name/title separator or on a known recruiter-title keyword — which is
    why this rule must stay ahead of the location rule: a bare "招聘专员" is a title,
    and a "冯女士·总经理助理 上海" carries the city *and* the recruiter on one line.
    """
    if draft.recruiter_name:
        return False
    if not (any(sep in text for sep in ("·", "•", "・")) or any(
        kw in text for kw in RECRUITER_TITLE_KEYWORDS
    )):
        return False

    name, title, is_headhunter = parse_recruiter_info(text)
    draft.recruiter_name = name
    if title:
        draft.recruiter_title = title
    if is_headhunter:
        draft.is_headhunter = True
    parts = text.rsplit(" ", 1)
    if not draft.location and len(parts) == 2 and is_likely_location(parts[1]):
        draft.location = parts[1].strip()
    return True


def _rule_location(text: str, draft: _Draft) -> bool:
    if draft.location:
        return False
    if (
        text in KNOWN_CITIES
        or text.endswith("市")
        or text.endswith("区")
        or text.endswith("县")
        or is_likely_location(text)
    ):
        draft.location = text
        return True
    return False


def _rule_requirement_tag(text: str, draft: _Draft) -> bool:
    if any(kw in text for kw in REQUIREMENT_KEYWORDS):
        draft.tags.append(text)
        return True
    return False


def _rule_title(text: str, draft: _Draft) -> bool:
    """The first substantive node is the job title, on every Boss card layout so far."""
    if draft.title:
        return False
    draft.title = clean_job_title(text)
    return True


def _rule_company(text: str, draft: _Draft) -> bool:
    if draft.company:
        return False
    name, scale, industry = parse_company_scale_industry(text)
    if not name or is_invalid_company_name(name) or company_duplicates_card_title(name, draft.title):
        return False
    draft.company = name
    if scale and not draft.scale:
        draft.scale = scale
    if industry and not draft.industry:
        draft.industry = industry
    return True


def _rule_scale(text: str, draft: _Draft) -> bool:
    if draft.scale or not _SCALE_RE.search(text):
        return False
    draft.scale = text
    return True


def _rule_tags_vs_snippet(text: str, draft: _Draft) -> bool:
    """The last rule always claims the node: a long line is a digest, a short one a tag."""
    if len(text) > SNIPPET_MIN_CHARS and not draft.snippet:
        draft.snippet = text
    elif len(text) <= TAG_MAX_CHARS and not is_invalid_company_name(text) and not is_likely_location(text):
        draft.tags.append(text)
    elif not draft.snippet:
        draft.snippet = text
    return True


#: The classifier, in order. A new Boss card layout becomes an entry here — inserted at
#: the position that makes it correct — not a new branch inside a 228-line method.
CLASSIFIER_RULES: tuple[CardRule, ...] = (
    _rule_salary,
    _rule_recruiter,
    _rule_location,
    _rule_requirement_tag,
    _rule_title,
    _rule_company,
    _rule_scale,
    _rule_tags_vs_snippet,
)


def classify_text_nodes(text_nodes: Sequence[str], draft: _Draft) -> _Draft:
    """Walk a card's ordered text nodes through :data:`CLASSIFIER_RULES`."""
    for raw in text_nodes:
        text = (raw or "").strip()
        if not text or text in PLATFORM_BADGE_MARKERS:
            continue
        for rule in CLASSIFIER_RULES:
            if rule(text, draft):
                break
    return draft


def _facets_to_draft(facets: CardFacets) -> _Draft:
    """Interpret the priority-1 reads before any text node is consulted."""
    title = clean_job_title(facets.title)
    company, scale, industry = parse_company_scale_industry(
        facets.company, explicit_scale=facets.scale, explicit_industry=facets.industry
    )
    if company and (is_invalid_company_name(company) or company_duplicates_card_title(company, title)):
        company = ""

    recruiter_name, recruiter_title, is_headhunter = parse_recruiter_info(facets.recruiter)

    location = facets.location
    # Safeguard: a locator can read the recruiter's title back as the city.
    if location and not is_likely_location(location) and any(
        kw in location for kw in RECRUITER_TITLE_KEYWORDS
    ):
        if not recruiter_title:
            recruiter_title = location
        if "猎头" in location:
            is_headhunter = True
        location = ""

    return _Draft(
        title=title,
        company=company.strip(),
        scale=scale,
        industry=industry,
        salary=facets.salary,
        recruiter_name=recruiter_name,
        recruiter_title=recruiter_title,
        is_headhunter=is_headhunter,
        location=location,
        snippet=facets.snippet,
        tags=list(facets.tags),
    )


def parse_card(facets: CardFacets, text_nodes: Sequence[str] = ()) -> ParsedCard | None:
    """Interpret one card, or return ``None`` when it is incomplete.

    ``None`` means "no usable observation" — the card has no title, no genuine company,
    or a company that is really its own title read back. Callers skip such a card
    rather than persisting a record nothing can deduplicate.
    """
    draft = _facets_to_draft(facets)

    # The text pass is the *fallback*: it runs only for a card whose locator reads did
    # not resolve both a title and a company. A complete card is never re-interpreted
    # from raw text, so a layout change cannot silently override an explicit read.
    if _fallback_needed(draft):
        classify_text_nodes(text_nodes, draft)

    if not draft.title or not draft.company or is_invalid_company_name(draft.company):
        return None

    tags = sanitize_tags(
        draft.tags,
        recruiter_name=draft.recruiter_name,
        recruiter_title=draft.recruiter_title,
        location=draft.location,
        company_name=draft.company,
        title=draft.title,
    )

    return ParsedCard(
        title=draft.title,
        company_name=draft.company.strip(),
        recruiter_name=draft.recruiter_name,
        recruiter_title=draft.recruiter_title,
        is_headhunter=draft.is_headhunter,
        salary_range=draft.salary,
        location=draft.location,
        tags=tuple(tags),
        snippet=draft.snippet,
        company_scale=draft.scale,
        industry=draft.industry,
    )
