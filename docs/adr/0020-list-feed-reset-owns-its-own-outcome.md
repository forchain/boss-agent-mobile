# 0020. The 仅沟通 Feed Reset Is a Step of Its Own, and Narrates Only What It Did

We decided that resetting the 仅沟通 feed to its newest messages is a step the caller performs and narrates, not a side effect of `CommunicationListPage.open_list()`. **This amends ADR 0016**: the `open_list()` step list that ADR enumerates is the contract, and the feed reset is not one of its steps.

## Context

ADR 0016 decided the multi-tier `open_list()` recovery and enumerated its steps: click `消息` → `仅沟通`, unwind one screen at a time when the bottom tab is not reached, re-activate Boss when a Back press escapes, settle between actions.

The 仅沟通 RecyclerView, however, is shared state that survives navigation. Returning to the list restores the scroll position it had when it was left, so a `CHECK_CHAT` dispatch arriving while the list sits mid-history reads stale cards and never sees the newest messages — the ones the run exists to find. The only reset the screen offers is a double-tap on the bottom `消息` tab.

Folding that reset into `open_list()` made it invisible. `CommunicationListPage.scroll_to_top()` returns `False` when the navigation bar is absent — there is nothing to double-tap, and the page performs no gesture at all — but `open_list()` discarded the return value, so the run narrated `🔝 [Navigation] 双击「消息」导航按钮，快速回到最新消息列表顶部` for a device action that never happened. The port member `ChatListReader.scroll_to_top()` was correspondingly dead: no production caller, so no caller could observe the outcome.

## Decision

1. **The reset is the caller's step.** `open_list()` reports the landing alone. `ChatTriage._enter_list()` calls `ChatListReader.scroll_to_top()` after a successful landing, through the protocol member that already existed for it.
2. **The narration follows the return value.** A performed reset logs the success line; a declined one logs that the navigation bar was unavailable and the feed may still be mid-history. A run never claims a gesture the device did not make.
3. **A declined reset is not a navigation failure.** The list is showing either way, so the scan continues from wherever the feed stands. Only an unreachable list ends the run (`list_unreachable`).
4. **Exactly one reset per entry.** Keeping the gesture out of the recovery loop is what guarantees this: a caller that also reset would double-tap `消息` on every entry.

## Consequences

- **Honest task telemetry**: an operator reading the log can tell a run that reached the newest messages from one that did not, instead of seeing the same line for both.
- **The protocol port earns its keep**: `ChatListReader.scroll_to_top()` now sits on the path the narration depends on, so the port's contract and the run's behaviour cannot drift apart.
- **`open_list()` is narrower**: a caller that wants the newest messages must ask for the reset. That is the point — the reset is a decision with an outcome, not a free side effect, and ADR 0016's recovery steps are unchanged by it.
- **A missed reset is now visible**: a run that lands on a mid-history feed without a navigation bar no longer looks identical to one that reset successfully; the anomaly is recorded as `ANOM-009`.
