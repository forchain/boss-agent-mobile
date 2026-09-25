# 0016. 仅沟通 Cleanup: On-Screen Back Recovery, a Dedicated Category Tab, and the Startup Cleanup Barrier

We decided that the rejection cleanup (`CHECK_CHAT`) navigates itself onto the 仅沟通 list from any screen — preferring an on-screen back affordance over the hardware Back key — that it gets its own category tab in the task launch modal, and that a starting service queues one cleanup and holds every search task back until it settles. **This amends ADR 0011**, which decided that recovery scans no back buttons anywhere.

## Context
Running the rejection triage against a live device exposed three failures:

- `CommunicationListPage.open_list()` assumed the 消息 bottom tab was already on screen. Dispatched while the app sat on a job detail, an open chat, a filter sheet or the launcher, it failed instantly with `❌ [List] 无法进入「仅沟通」列表，任务终止` — no recovery at all.
- The naive alternative (press hardware Back until the tab appears) is what ADR 0011 already rejected for search entry, and for a good reason: the 消息 column sits deeper than the 职位 column, and a Back press from a chat room or from the column itself can walk straight out of Boss to the launcher.
- The rejection cleanup trigger lived inside the 🛠️ 系统诊断 tab. A one-click action that can send real messages sat behind a label that promises a read-only system check.
- Search and auto-apply tasks could run before the cleanup had ingested the rejecting employers, re-applying to companies that had already rejected the candidate.

ADR 0011's "no back-button scanning" rule was driven by *cost*, measured on the search-entry path: a 7-candidate locator list cost 5-8 seconds per miss. That reasoning does not carry over unchanged: the cleanup runs once per task (not once per attempt inside a hot loop), and here the on-screen button is the *safer* action rather than merely a faster one.

## Decision
1. **Multi-tier recovery in `CommunicationListPage.open_list()`**:
   - Pre-check `is_on_list()`; an app already on the list is never clicked at all.
   - Otherwise loop at most `LIST_RECOVERY_MAX_STEPS` (6) times: try `消息` → `仅沟通` (`_open_message_column`), confirm the landing, and only then unwind one screen (`_recover_one_step`).
   - `_recover_one_step` probes a single short locator list `communication_list.back_btn` (the measured `iv_back` / `iv_back_ai`, then the generic 返回 content-desc) and clicks it with a humanized click. **The hardware `KEYCODE_BACK` is the fallback, not the default.**
   - Every step re-runs `_ensure_foreground()`, so a Back press that did escape to the launcher re-activates Boss instead of being compounded by the next press.
   - Each action is followed by a jittered settle pause (`BACK_INTERVAL_SEC`), so recovery cannot degenerate into a runaway click burst.
   - The handler logs the recovery and fails the task only when the bounded loop is exhausted.
2. **Locator cost stays bounded**: `communication_list.back_btn` carries exactly three candidates, cheapest first, rather than reusing the 5-candidate `navigation.back_btn`. The ADR 0011 measurement stands — every missed candidate is a full accessibility-tree scan — so the amendment is scoped to a path that pays it once per recovery step, not once per element probe in a loop.
3. **拒信清扫 is a first-class category tab** in `TaskLaunchModal.svelte` (`🧹 拒信清扫` / `chat_cleanup`), beside `🔍 搜索策略任务` and `🛠️ 系统诊断`. The diagnostics tab keeps only diagnostic tasks (`CHECK_LOGIN`); the cleanup tab owns the reply text, scan depth, drill-mode toggle and dispatch button.
4. **Startup barrier (`StartupCleanupGate`, issue #230)**:
   - `run_cleanup_on_startup` (default **true**, env `RUN_CLEANUP_ON_STARTUP`) makes a starting worker daemon and scheduler queue one `CHECK_CHAT` marked `payload["startup_cleanup"] = True`.
   - The gate is derived from the **pending queue**, not from a task id held in memory: a cleanup queued by the scheduler binds the worker daemon too.
   - `held_types()` holds `SCRAPE_JOBS` / `AUTO_APPLY` while a marked cleanup is still pending; anything terminal — `success`, `failed`, `cancelled` — releases the barrier, so a failed cleanup can never deadlock the search pipeline.
   - `prioritize()` claims marked cleanups ahead of tasks queued before them, closing the head-of-queue gap a stale pending search would otherwise occupy. The worker's pending scan therefore uses the gate's own window: a narrower one could hide the cleanup while its barrier held every task in view.
   - **Concurrent arms collapse to one cleanup.** `arm()` serialises on an in-process lock and re-reads the queue before creating, which covers the `--enable-scheduler` deployment (one shared gate, both services). Two separate processes can still both read an empty queue before either write lands, so `arm()` then re-reads once more: the oldest marked cleanup wins, and every racer cancels itself unless it wins. However the two re-reads interleave, exactly one cleanup is left queued — without that, the duplicate would run, and with dry-run off it would reply a second time to whoever is still on the list.

## Trade-offs
- The on-screen back button can be ambiguous: several apps-internal screens carry an `iv_back`, and clicking one unwinds a screen the caller may not have expected. Bounded by the step budget, backstopped by the foreground guard, and strictly better than leaving the app entirely.
- Recovery probes fast-fail, so a screen that is merely slow to paint costs a Back press rather than a wait. That is the ADR 0011 trade-off restated for this path — the step budget and the foreground guard absorb it, where polling out the element budget per step would turn one unreachable list into a 35-second stall.
- The barrier is computed from the pending queue, so it assumes a single worker per queue: a *running* cleanup does not block a *second* worker polling the same broker. Multi-device deployments would need a "non-terminal marked task" query on the broker instead.
- Deriving the barrier from the pending queue means a cleanup left pending by a crashed-but-not-swept worker keeps search tasks held until the lease sweeper requeues or fails it. That is the intended behaviour, and it is what makes the guarantee hold across restarts.
- The step budget is a constructor parameter (`max_steps`, defaulting to `LIST_RECOVERY_MAX_STEPS = 6`) rather than a settings key: it is a mechanical bound on a recovery loop, not something a deployment tunes, and `run_cleanup_on_startup` is the only switch this feature exposes to configuration.
- The Web settings writer rebuilds `config/settings.local.yaml` from a fixed template, so `run_cleanup_on_startup` had to be added there too; otherwise saving any unrelated setting from the dashboard would have silently dropped the switch and re-enabled the default barrier.
