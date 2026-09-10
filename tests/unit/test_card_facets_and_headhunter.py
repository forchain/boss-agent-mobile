"""
tests/unit/test_card_facets_and_headhunter.py
=============================================
Unit tests for card facets (company_scale, industry, tags, digest)
and recruiter-title-based headhunter detection.
"""

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

from boss_agent.broker.provisioner import JOB_RECORDS_FIELDS, provision_sqlite_database
from boss_agent.models import JobPosting, JobRecord
from boss_agent.pages import (
    JobCardBrief,
    JobListPage,
    clean_job_title,
    parse_company_scale_industry,
    parse_recruiter_info,
)


def test_parse_recruiter_info_headhunter_detection():
    """Recruiter title containing '猎头' must resolve to is_headhunter=True, others to False."""
    # Headhunter cases
    name1, title1, is_hh1 = parse_recruiter_info("钟先生 · 猎头顾问")
    assert name1 == "钟先生"
    assert title1 == "猎头顾问"
    assert is_hh1 is True

    name2, title2, is_hh2 = parse_recruiter_info("林先生·资深猎头顾问")
    assert name2 == "林先生"
    assert title2 == "资深猎头顾问"
    assert is_hh2 is True

    name3, title3, is_hh3 = parse_recruiter_info("猎头总监 王女士")
    assert is_hh3 is True

    # Direct hiring cases (must be is_headhunter=False)
    name4, title4, is_hh4 = parse_recruiter_info("农女士 · 高级招聘专员")
    assert name4 == "农女士"
    assert title4 == "高级招聘专员"
    assert is_hh4 is False

    name5, title5, is_hh5 = parse_recruiter_info("冯女士 · 总经理助理 上海")
    assert name5 == "冯女士"
    assert "总经理助理" in title5
    assert is_hh5 is False

    name6, title6, is_hh6 = parse_recruiter_info("张先生")
    assert name6 == "张先生"
    assert title6 == ""
    assert is_hh6 is False

    name7, title7, is_hh7 = parse_recruiter_info("")
    assert name7 == ""
    assert title7 == ""
    assert is_hh7 is False


def test_parse_company_scale_industry():
    """Separates company name, scale, and industry from combined strings or explicit values."""
    # 1. Combined string like in Boss app search card line 2
    comp1, scale1, ind1 = parse_company_scale_industry("某中型人工智能公司 100-499人 人工智能")
    assert comp1 == "某中型人工智能公司"
    assert scale1 == "100-499人"
    assert ind1 == "人工智能"

    # 2. Authentic direct company with scale and industry
    comp2, scale2, ind2 = parse_company_scale_industry("深至科技 100-499人 人工智能")
    assert comp2 == "深至科技"
    assert scale2 == "100-499人"
    assert ind2 == "人工智能"

    # 3. Already split parameters passed explicitly
    comp3, scale3, ind3 = parse_company_scale_industry("游族网络", explicit_scale="1000-9999人", explicit_industry="游戏")
    assert comp3 == "游族网络"
    assert scale3 == "1000-9999人"
    assert ind3 == "游戏"

    # 4. Fallback without scale
    comp4, scale4, ind4 = parse_company_scale_industry("未知初创企业")
    assert comp4 == "未知初创企业"
    assert scale4 == ""
    assert ind4 == ""


def test_models_support_card_facets_and_headhunter_flag():
    """JobCardBrief, JobRecord, and JobPosting support company_scale, industry, tags, recruiter_title, and is_headhunter."""
    # 1. JobCardBrief
    card = JobCardBrief(
        title="技术负责人-CTO级别 | pre-ipo 公司 | 医疗AI &@",
        company_name="某中型人工智能公司",
        recruiter_name="钟先生",
        recruiter_title="猎头顾问",
        company_scale="100-499人",
        industry="人工智能",
        tags=["10年以上", "硕士", "容器技术", "网络交换技术", "分布式技术"],
        digest="核心研发与平台建设领导团队研发医疗领域专用的大模型...",
    )
    assert card.company_scale == "100-499人"
    assert card.industry == "人工智能"
    assert len(card.tags) == 5
    assert card.recruiter_title == "猎头顾问"
    # Auto-detected from recruiter_title
    assert card.is_headhunter is True

    # 2. Direct hiring JobCardBrief
    card_direct = JobCardBrief(
        title="技术负责人",
        company_name="深至科技",
        recruiter_name="农女士",
        recruiter_title="高级招聘专员",
        company_scale="100-499人",
        industry="人工智能",
        tags=["10年以上", "硕士", "互联网/AI"],
        digest="负责核心研发团队管理",
    )
    assert card_direct.is_headhunter is False

    # 3. JobRecord
    rec = JobRecord(
        title="技术负责人",
        company_name="深至科技",
        recruiter_name="农女士",
        recruiter_title="高级招聘专员",
        company_scale="100-499人",
        industry="人工智能",
        tags=["10年以上", "硕士"],
        digest="负责核心研发团队管理",
        job_description="完整职位描述...",
    )
    assert rec.company_scale == "100-499人"
    assert rec.industry == "人工智能"
    assert rec.tags == ["10年以上", "硕士"]
    assert rec.recruiter_title == "高级招聘专员"
    assert rec.is_headhunter is False

    # 4. JobPosting
    posting = JobPosting(
        title="资深架构师",
        company_name="成都某中型...智能公司",
        salary_range="8.5-10万元",
        job_description="岗位职责...",
        recruiter_name="林先生",
        recruiter_title="猎头顾问",
        company_scale="100-499人",
        industry="人工智能",
        is_headhunter=True,
    )
    assert posting.company_scale == "100-499人"
    assert posting.industry == "人工智能"
    assert posting.is_headhunter is True


def test_provision_sqlite_database_adds_all_new_facet_columns(tmp_path: Path):
    """Ensure database provisioner adds all 5 new columns to existing job_records table."""
    field_names = [f["name"] for f in JOB_RECORDS_FIELDS]
    for expected_field in ("company_scale", "industry", "tags", "recruiter_title", "is_headhunter"):
        assert expected_field in field_names

    db_file = tmp_path / "data.db"
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE _collections (
            id TEXT PRIMARY KEY,
            system BOOLEAN DEFAULT FALSE,
            type TEXT DEFAULT "base",
            name TEXT UNIQUE NOT NULL,
            fields JSON DEFAULT "[]" NOT NULL,
            indexes JSON DEFAULT "[]" NOT NULL,
            listRule TEXT DEFAULT NULL,
            viewRule TEXT DEFAULT NULL,
            createRule TEXT DEFAULT NULL,
            updateRule TEXT DEFAULT NULL,
            deleteRule TEXT DEFAULT NULL,
            options JSON DEFAULT "{}" NOT NULL,
            created TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%fZ')),
            updated TEXT DEFAULT (strftime('%Y-%m-%d %H:%M:%fZ'))
        )
    """)
    cursor.execute("""
        INSERT INTO _collections (id, system, type, name, fields, listRule, viewRule, createRule, updateRule, deleteRule)
        VALUES ('pbc_job_records', 0, 'base', 'job_records', '[]', '', '', '', '', '')
    """)
    # Simulate legacy table missing the new facet columns
    cursor.execute("""
        CREATE TABLE job_records (
            id TEXT PRIMARY KEY,
            fingerprint TEXT UNIQUE,
            title TEXT,
            company_name TEXT,
            recruiter_name TEXT,
            salary_range TEXT,
            location TEXT,
            digest TEXT,
            job_description TEXT,
            status TEXT DEFAULT 'unmatched'
        )
    """)
    conn.commit()
    conn.close()

    # Run provisioning
    assert provision_sqlite_database(db_file) is True

    # Verify all 5 new columns were added
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(job_records)")
    columns = [row[1] for row in cursor.fetchall()]
    conn.close()

    for expected_col in ("company_scale", "industry", "tags", "recruiter_title", "is_headhunter"):
        assert expected_col in columns


def test_job_list_page_extracts_rich_card_facets():
    """JobListPage.extract_visible_job_cards extracts scale, industry, tags, and classifies headhunter status."""
    mock_driver = MagicMock()
    page = JobListPage(mock_driver)

    # Create mock card element
    mock_card = MagicMock()
    mock_title = MagicMock()
    mock_title.text = "技术负责人-CTO级别 | pre-ipo 公司 | 医疗AI &@"

    mock_salary = MagicMock()
    mock_salary.text = "20-26万元·18月"

    mock_company = MagicMock()
    mock_company.text = "某中型人工智能公司 100-499人 人工智能"

    mock_recruiter = MagicMock()
    mock_recruiter.text = "钟先生 · 猎头顾问"

    mock_location = MagicMock()
    mock_location.text = "上海"

    mock_digest = MagicMock()
    mock_digest.text = "核心研发与平台建设领导团队研发医疗领域专用的大模型..."

    # Tag elements
    mock_tag_container = MagicMock()
    mock_tag1 = MagicMock()
    mock_tag1.text = "10年以上"
    mock_tag2 = MagicMock()
    mock_tag2.text = "硕士"
    mock_tag3 = MagicMock()
    mock_tag3.text = "容器技术"
    mock_tag_container.find_elements.return_value = [mock_tag1, mock_tag2, mock_tag3]

    def mock_find_elements(by, value):
        val_str = str(value)
        if "view_job_card" in val_str or "cl_card_container" in val_str or "item_job" in val_str:
            return [mock_card]
        if "tv_position_name" in val_str:
            return [mock_title]
        if "tv_salary_statue" in val_str:
            return [mock_salary]
        if "tv_company_name" in val_str:
            return [mock_company]
        if "tv_employer" in val_str:
            return [mock_recruiter]
        if "tv_distance" in val_str:
            return [mock_location]
        if "tv_digest" in val_str:
            return [mock_digest]
        if "fl_require_info" in val_str:
            return [mock_tag_container]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements
    mock_card.find_elements.side_effect = mock_find_elements

    cards = page.extract_visible_job_cards(max_cards=1)
    assert len(cards) == 1
    card = cards[0]

    assert card.title == "技术负责人-CTO级别 | pre-ipo 公司 | 医疗AI"
    assert card.company_name == "某中型人工智能公司"
    assert card.company_scale == "100-499人"
    assert card.industry == "人工智能"
    assert card.recruiter_name == "钟先生"
    assert card.recruiter_title == "猎头顾问"
    assert card.is_headhunter is True
    assert card.tags == ["10年以上", "硕士", "容器技术"]
    assert card.digest.startswith("核心研发与平台建设")


def test_clean_job_title_removes_placeholders_and_badges():
    """clean_job_title must remove trailing '&@', '&@ &@', whitespace and tags."""
    assert clean_job_title("技术负责人-CTO级别｜pre-ipo公司｜医疗AI &@") == "技术负责人-CTO级别｜pre-ipo公司｜医疗AI"
    assert clean_job_title("CTO，外企AI Startup，可远程办公 &@") == "CTO，外企AI Startup，可远程办公"
    assert clean_job_title("算法高级工程师-DataAgent &@  &@") == "算法高级工程师-DataAgent"
    assert clean_job_title("资深架构师 &@") == "资深架构师"
    assert clean_job_title("【MLBB】AI开发工程师 &@") == "【MLBB】AI开发工程师"
    assert clean_job_title("外企-全栈开发工程师-不加班1075 @%") == "外企-全栈开发工程师-不加班1075"
    assert clean_job_title("资深前端开发 @%") == "资深前端开发"
    assert clean_job_title("AI应用工程师 %") == "AI应用工程师"
    assert clean_job_title("移动端架构师 @") == "移动端架构师"
    assert clean_job_title("全栈工程师 &@%") == "全栈工程师"
    assert clean_job_title("") == ""


def test_parse_recruiter_info_with_dot_delimiter_and_trailing_characters():
    """Separates name and title cleanly on '·' and removes trailing dots."""
    name1, title1, is_hh1 = parse_recruiter_info("王女士 · 猎头顾问")
    assert name1 == "王女士"
    assert title1 == "猎头顾问"
    assert is_hh1 is True

    name2, title2, is_hh2 = parse_recruiter_info("钟先生 ·")
    assert name2 == "钟先生"
    assert title2 == ""
    assert is_hh2 is False

    name3, title3, is_hh3 = parse_recruiter_info("买先生·产品研发")
    assert name3 == "买先生"
    assert title3 == "产品研发"
    assert is_hh3 is False

    name4, title4, is_hh4 = parse_recruiter_info("戴女士 · 招聘专家")
    assert name4 == "戴女士"
    assert title4 == "招聘专家"
    assert is_hh4 is False


def test_job_list_page_guards_against_recruiter_title_in_location():
    """When location locator captures recruiter title (e.g. '猎头顾问'), it moves to recruiter_title and triggers is_headhunter."""
    mock_driver = MagicMock()
    page = JobListPage(mock_driver)

    mock_card = MagicMock()
    mock_title = MagicMock()
    mock_title.text = "资深架构师 &@"
    mock_comp = MagicMock()
    mock_comp.text = "成都某中型人工智能公司 100-499人 人工智能"
    mock_rec = MagicMock()
    mock_rec.text = "林先生 ·"
    mock_loc = MagicMock()
    mock_loc.text = "猎头顾问"  # Misrouted by locator

    def mock_find_elements(by, value):
        val_str = str(value)
        if "view_job_card" in val_str:
            return [mock_card]
        if "tv_position_name" in val_str:
            return [mock_title]
        if "tv_company_name" in val_str:
            return [mock_comp]
        if "tv_employer" in val_str:
            return [mock_recruiter] if "mock_recruiter" in locals() else [mock_rec]
        if "tv_distance" in val_str:
            return [mock_loc]
        return []

    mock_driver.find_elements.side_effect = mock_find_elements
    mock_card.find_elements.side_effect = mock_find_elements

    cards = page.extract_visible_job_cards(max_cards=1)
    assert len(cards) == 1
    card = cards[0]
    assert card.title == "资深架构师"
    assert card.recruiter_name == "林先生"
    assert card.recruiter_title == "猎头顾问"
    assert card.is_headhunter is True
    assert card.location == ""  # Safeguarded: not '猎头顾问'


def test_job_list_page_skips_cards_without_company_name_or_unknown_company():
    """JobListPage.extract_visible_job_cards must ignore partially visible cards without company name."""
    mock_driver = MagicMock()
    page = JobListPage(mock_driver)

    mock_card_valid = MagicMock()
    mock_card_no_company = MagicMock()
    mock_card_unknown_company = MagicMock()

    mock_title1 = MagicMock()
    mock_title1.text = "AI工程师"
    mock_comp1 = MagicMock()
    mock_comp1.text = "字节跳动"

    mock_title2 = MagicMock()
    mock_title2.text = "Agent 开发工程师"
    # No company element returned for card 2 (partially scrolled out)

    mock_title3 = MagicMock()
    mock_title3.text = "CTO，外企AI"
    mock_comp3 = MagicMock()
    mock_comp3.text = "未知公司"

    def mock_driver_find(by, value):
        val_str = str(value)
        if "view_job_card" in val_str:
            return [mock_card_valid, mock_card_no_company, mock_card_unknown_company]
        return []

    mock_driver.find_elements.side_effect = mock_driver_find

    def mock_card_find(card_instance):
        def _find(by, value):
            val_str = str(value)
            if card_instance is mock_card_valid:
                if "tv_position_name" in val_str:
                    return [mock_title1]
                if "tv_company_name" in val_str:
                    return [mock_comp1]
            elif card_instance is mock_card_no_company:
                if "tv_position_name" in val_str:
                    return [mock_title2]
                if "tv_company_name" in val_str:
                    return []  # Missing company element
            elif card_instance is mock_card_unknown_company:
                if "tv_position_name" in val_str:
                    return [mock_title3]
                if "tv_company_name" in val_str:
                    return [mock_comp3]
            return []
        return _find

    mock_card_valid.find_elements.side_effect = mock_card_find(mock_card_valid)
    mock_card_no_company.find_elements.side_effect = mock_card_find(mock_card_no_company)
    mock_card_unknown_company.find_elements.side_effect = mock_card_find(mock_card_unknown_company)

    cards = page.extract_visible_job_cards(max_cards=10)
    assert len(cards) == 1
    assert cards[0].title == "AI工程师"
    assert cards[0].company_name == "字节跳动"


def test_backfill_purges_unknown_company_records(tmp_path: Path):
    """Database provisioner backfill must delete records where company_name is missing or '未知公司'."""
    db_file = tmp_path / "data.db"
    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE _collections (
            id TEXT PRIMARY KEY, system BOOLEAN, type TEXT, name TEXT UNIQUE,
            fields JSON, indexes JSON, listRule TEXT, viewRule TEXT,
            createRule TEXT, updateRule TEXT, deleteRule TEXT, options JSON,
            created TEXT, updated TEXT
        )
    """)
    cursor.execute("""
        INSERT INTO _collections (id, system, type, name, fields)
        VALUES ('pbc_job_records', 0, 'base', 'job_records', '[]')
    """)
    cursor.execute("""
        CREATE TABLE job_records (
            id TEXT PRIMARY KEY,
            fingerprint TEXT UNIQUE,
            title TEXT,
            company_name TEXT,
            recruiter_name TEXT,
            status TEXT DEFAULT 'unmatched'
        )
    """)
    cursor.execute("""
        INSERT INTO job_records (id, fingerprint, title, company_name, recruiter_name)
        VALUES 
            ('valid_1', 'fp_1', 'AI工程师', '真实科技公司', '张HR'),
            ('invalid_1', 'fp_2', '残缺职位1', '未知公司', '招聘者'),
            ('invalid_2', 'fp_3', '残缺职位2', '', '招聘者'),
            ('invalid_3', 'fp_4', '残缺职位3', NULL, '招聘者')
    """)
    conn.commit()
    conn.close()

    assert provision_sqlite_database(db_file) is True

    conn = sqlite3.connect(str(db_file))
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, company_name FROM job_records")
    remaining = cursor.fetchall()
    conn.close()

    assert len(remaining) == 1
    assert remaining[0][0] == "valid_1"
    assert remaining[0][2] == "真实科技公司"

