# 0017. Unread-Badge Bounded Check Chat Paging and Two-Tier Preflight Short-Circuit

We decided that rejection triage (`CHECK_CHAT`) introduces a **two-tier preflight short-circuit** and restores **bounded downward paging driven by the 仅沟通 unread badge**. **This supersedes ADR 0015**, which restricted `CHECK_CHAT` to the opening screen without scrolling.

## Context

ADR 0015 deliberately stopped paging on the 仅沟通 list after runaway paging failures under ADR 0011 and ADR 0014. Under ADR 0015, `CHECK_CHAT` read only the opening viewport (`VIEWPORT_SIZE` cards) and never scrolled.

While this prevented runaway scrolling, two severe operational flaws emerged in live production:

1. **Starvation by Outbound Backlog**: Candidates routinely send messages (e.g. initial greetings or follow-ups). These threads display the Outbound Message Indicator (`[送达]` / `[已读]`) and are never removed from the list. When an account has 4 or more outbound conversations at the top of the list, any incoming recruiter rejection or reply below the fold is permanently obscured. Because ADR 0015 forbade scrolling, the agent was blinded to unread rejections pushed down by outbound backlog, failing to acknowledge them or blacklist rejecting employers.
2. **Startup Cleanup Barrier Latency on Zero-Unread Accounts**: ADR 0016 introduced the Startup Rejection Cleanup Barrier (`CHECK_CHAT` queued before job search/greeting tasks). On accounts with zero unread messages, ADR 0015 still executed a full list recovery navigation, extracted and evaluated the entire opening screen of cards, and logged `first_screen_exhausted`, incurring tens of seconds of device automation latency and unnecessary LLM token/latency costs before the startup barrier could drop.

Both issues share a common root: the traversal lacked an awareness of the platform's native unread status indicators.

## Decision

1. **Two-Tier Preflight Short-Circuit**:
   - **Tier 1 (Account-level probe)**: When the bottom navigation bar is visible, check for the red notification dot on the 消息 tab (`fl_tab_3_red_dot`). If absent, the account has zero unread messages across all categories and the run may skip the list entirely with `stop_reason="unread_cleared"` without reading a card — but only once that absence has been confirmed (next bullet).
   - **Tier 2 (Category-level probe)**: Once on the 消息 screen, check the unread badge count on the 仅沟通 sub-tab (`tv_count`). If absent or `<= 0` (e.g. unread dot was caused by "新招呼" or "全部"), the run logs the zero-unread status and exits with `stop_reason="unread_cleared"` — on the same confirmed-absence terms.
   - **Absence Is Confirmed, Not Assumed**: An absent probe is ambiguous — a clean account and a screen that has not finished rendering look identical — and only one of the two justifies a skip. A short-circuit therefore requires the absence to hold across `UNREAD_ABSENCE_CONFIRMATIONS` (`3`) readings spaced by `SETTLE_PAUSE_SEC` (`0.35s`); a single reading is never enough. The rule covers every place a probe can end a run: the two preflight tiers, the mid-run badge check that terminates a scan (§2), and the acknowledgment feedback in §4 — a badge not yet drawn there is indistinguishable from one that just cleared. A probe that fails confirmation does not fail the run; it falls through to the scan, where a wrong guess costs time rather than coverage. In particular a bar that is *not on screen* never reads as clear: nothing can be concluded about an account whose navigation is not visible.
2. **Unread-Badge Driven Traversal**:
   - The scan tracks visible cards across viewports using `visited_keys`.
   - After inspecting pending cards on the current screen, if the unread badge count on 仅沟通 has cleared to zero (or is absent), the run terminates immediately with `stop_reason="unread_cleared"`.
   - If unhandled unread messages remain (`unread_badge_count > 0`), the agent performs a humanized downward scroll gesture on the conversation `RecyclerView` (`scroll_message_list`) and continues inspecting newly uncovered cards.
3. **Safety Ceilings and Termination Taxonomy**:
   - **Scroll Swipe Ceiling**: Traversal is capped at `max_scroll_swipes` (default `5`), configurable via task payload or `ChatAcknowledgmentSettings.max_scroll_swipes`. If this ceiling is reached while unread messages remain, the run stops with `stop_reason="scroll_ceiling"`.
   - **Bottom-of-List Detection**: If 2 consecutive downward scroll gestures reveal no new cards, the list has reached its physical bottom, terminating with `stop_reason="scroll_ceiling"`.
   - **Per-Card Inspection Ceiling**: `max_inspected_cards` is tightened from `300` to `50`, and `max_scan_depth` stays at `30`. The ceiling is no longer a coverage bound — the unread badge and `max_scroll_swipes` are — so it only remains as the loop's termination guarantee. `50` is derived from the traversal's own reach rather than picked freely: `max_scroll_swipes` (`5`) opens at most six viewports of `VIEWPORT_SIZE` (`10`) cards, so a run that the swipe ceiling would not already have stopped cannot exceed roughly fifty cards. Aligning the two means the card ceiling never truncates a run the swipe ceiling considers legal. A run that stops here reports `stop_reason="scan_ceiling"` whatever its swipe count: the bound that fired is the card ceiling, and blaming the swipe limit would describe a limit that was never reached and leave the log tag contradicting the telemetry.
   - Retired `first_screen_exhausted` in favor of `unread_cleared` and `scroll_ceiling`.
4. **Immediate Acknowledgment Feedback**:
   - Whenever an explicit rejection is acknowledged and marked as 不感兴趣 (removing it from the list), the list reader immediately re-reads the unread badge count, confirming an absence across the settle window before believing it. Only a confirmed zero completes the scan without unnecessary scrolling or further card reads.

## Consequences

- **Sub-Second Startup Barrier Clearance**: Accounts with zero unread messages clear the startup barrier on a Tier 1 or Tier 2 preflight without reading a card, dropping the barrier immediately for subsequent `SCRAPE_JOBS` and `AUTO_APPLY` tasks.
- **Confirmed Absence Costs a Settle Window**: A clean account now spends roughly `UNREAD_ABSENCE_CONFIRMATIONS × SETTLE_PAUSE_SEC` (`~0.7s`) confirming before it clears, and a run whose badge briefly reads absent pays the same before continuing. That is the price of never skipping a real unread because a screen had not finished rendering — well inside the tens of seconds ADR 0015 spent, and paid only when a probe is about to make the run skip work.
- **Outbound Backlog Resilience**: Unread recruiter replies below a stack of outbound cards are reliably discovered and processed via bounded paging.
- **Zero Runaway Scrolling**: Unlike early depth-based paging, the traversal is strictly bounded by the actual unread badge count, a tight swipe ceiling (5 swipes), and bottom-of-list detection.
- **Telemetry Parity**: `scroll_swipes` is added to task output telemetry and dashboard summary lines, providing full visibility into scrolling behavior.
- **Supersedes ADR 0015**: Replaces opening-screen-only traversal with badge-driven bounded paging.
