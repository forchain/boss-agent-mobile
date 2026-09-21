"""
src/boss_agent/worker/handlers/search_entry.py
=============================================
Search initiation shared by the worker handlers (spec #193).

`JobListPage.open_search()` already unwinds any subpage through its two-anchor +
hardware-Back engine, so handlers must not pre-navigate or re-navigate around it;
what is left to share is the retry policy and its honest logging.
"""

import logging

from boss_agent.broker.pocketbase_adapter import BaseTaskBroker
from boss_agent.pages import JobListPage, SearchPage

logger = logging.getLogger(__name__)

MAX_SEARCH_ATTEMPTS = 2
OPEN_SEARCH_TIMEOUT_SEC = 5.0
SEARCH_SUBMIT_TIMEOUT_SEC = 10.0


async def run_search_entry(
    broker: BaseTaskBroker,
    task_id: str,
    list_page: JobListPage,
    search_page: SearchPage,
    keyword: str,
) -> bool:
    """Enter the search screen and submit ``keyword``, retrying once on failure.

    The caller passes the page objects it already holds, so handler-level test
    seams keep working. Returns True on a submitted search; on exhaustion the
    task log reports that the attempts are spent, rather than promising a retry
    that will not happen.
    """
    for attempt in range(MAX_SEARCH_ATTEMPTS):
        if not search_page.is_search_page():
            list_page.open_search(timeout_sec=OPEN_SEARCH_TIMEOUT_SEC)
        if search_page.search(keyword, timeout_sec=SEARCH_SUBMIT_TIMEOUT_SEC):
            return True
        if attempt < MAX_SEARCH_ATTEMPTS - 1:
            await broker.append_log(
                task_id,
                f"⚠️ 第 {attempt + 1}/{MAX_SEARCH_ATTEMPTS} 次进入搜索页面并搜索 '{keyword}'"
                f"失败，准备第 {attempt + 2} 次尝试...",
            )

    await broker.append_log(
        task_id,
        f"❌ 已尝试 {MAX_SEARCH_ATTEMPTS} 次仍未能进入搜索页面或执行关键词搜索: '{keyword}'，"
        f"重试次数已耗尽，终止任务以避免误操作推荐流",
    )
    return False
