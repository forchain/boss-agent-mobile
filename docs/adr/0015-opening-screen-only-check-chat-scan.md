# 0015. CHECK_CHAT Reads the Opening Screen and Never Pages

We decided that the rejection cleanup (`CHECK_CHAT`) stops paging the 仅沟通 list altogether: a run reads the cards on the opening screen, re-reads that same screen after each acknowledgment, and ends once nothing on it is new. **This supersedes ADR 0014**, which bounded a paging scan by the previous run's completion time, and it removes the paging machinery ADR 0014 built — the execution cursor, the card-stamp parser, and the broker query that fed them.

## Context

Two attempts to bound the scan by paging had now been made, and neither held on a live device:

- **ADR 0011 / issue #207** bounded the traversal by depth. `max_scan_depth` bounds *LLM evaluations*, and a card carrying the Outbound Message Indicator is skipped before any evaluation, so a backlog of threads waiting on a recruiter reply costs nothing per card and ran on to the `MAX_INSPECTED_CARDS` (300) ceiling.
- **ADR 0014 / issue #239** bounded it by time instead. That put the stop line on the card stamps — a two-resolution localised string, read through a parse gate, compared against a completion time stamped by a *different machine's clock*. The run still kept paging.

Two properties of the list are why both failed: it is ordered newest-first and it yields an unbounded run of cards that are never removed — an outbound-waiting thread and a preserved invitation both stay exactly where they are, forever, at the top. Any scan that walks downwards from the top therefore walks the same cards on every dispatch, and no page bound can distinguish "the backlog is this deep" from "the backlog is deep again because nothing above it moved".

## Decision

1. **The scan is one screen.** A run reads `VIEWPORT_SIZE` cards from the opening screen and never scrolls; `CommunicationListPage.scroll_list` is deleted along with its test. `VIEWPORT_SIZE` is now both the read size and the scan's width.
2. **The screen is re-read after every acknowledgment.** Marking a conversation 不感兴趣 removes it from the list and the cards under it move up into the space it freed, so what a run can reach is a function of what it has already cleared. `visited_keys` is what stops a preserved card from being judged again on the re-read.
3. **The run ends when the screen holds nothing new**, recorded as `stop_reason="first_screen_exhausted"` and logged with the number of cards the last read returned. `empty_list`, `cancelled`, `lost_list`, `max_scan_depth` and `scan_ceiling` keep their meaning, and the ceiling keeps its role as the loop's termination guarantee — an acknowledgment can keep lifting fresh cards onto the screen, so a pointer bound alone would not terminate. `stalled` and `reached_last_execution_time` are gone.
4. **The paging machinery is removed, not parked.** `card_time` on `CommunicationCard`, the stamp field and child-node fallback in `CommunicationListPage`, `communication_list.card_time`, `card_time.py`, and `BaseTaskBroker.list_recent_successful_completions` had no consumer once nothing paged, so they were deleted rather than left as dead code. ADR 0014 and its implementation remain in git history (`c490138`) if the approach is ever wanted back.

## Trade-offs

- **Coverage is bounded by the screen, and the bound can be permanent.** A card below the fold is reached only after the cards above it leave the list, and the cards above it may never leave: a preserved invitation and an outbound-waiting thread are never removed, so they can hold the rest of the list out of view indefinitely. **Accepted deliberately by the repo owner on 2026-09-23** after the second runaway paging run, in exchange for a traversal whose cost is one screen's worth of work per dispatch and which cannot run away — the failure mode is now "this run judged everything it could see", not "this run scrolled for 300 cards". Recovering the older stretch would need a way to clear or skip a card without acting on it (a paging scan that starts *below* the last cleared card, or a settled-on-screen filter), which is out of scope here.
- **Every dispatch re-judges what the screen shows.** A preserved card is not remembered between runs, so an unchanged screen costs one LLM call per preserved card each time. This is the property ADR 0014 existed to remove, and it is back; it is bounded by the screen's width and by `max_scan_depth`, and it is the price of having no cursor to keep correct.
- **A stamp is no longer read at all.** The card's last-message time stops being a signal: the per-card `card_time` probe and the child-node fallback behind it are gone, so the extraction path is four field lookups per card again instead of five, and the broker no longer queries SUCCESS records to derive a window.
- **A run stopped by `lost_list`** — the platform failing to return to the list after an acknowledgment — still reports success, and under this ADR that matters less than it did: the next run re-reads the same screen and judges what it finds, since nothing is carried between runs to go stale.
