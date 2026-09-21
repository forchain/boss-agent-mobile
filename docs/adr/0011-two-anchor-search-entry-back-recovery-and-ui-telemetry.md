# 0011. Two-Anchor Search Entry, Back-Only Recovery, and DEBUG UI Telemetry

We decided to replace cascading locator probing with a two-anchor search-entry engine, to recover from unexpected screens by pressing the hardware Back key only, and to instrument every low-level UI action with `DEBUG` logs under the `droid_agent_core.ui` logger namespace.

## Context
Entering search from a subpage took 20-40 seconds per cold attempt, and a failed search run took 148 seconds before reporting failure.

- `job_list.search_icon` carried 7 candidate XPaths. Every miss forces UiAutomator2 to serialize and traverse the whole accessibility node tree (~0.8-1.5s per candidate), so a single "not on home page" check cost 5-8 seconds.
- `navigate_to_home()` looped up to 6 times, and each iteration probed the filter close button, industry cancel button, and four different page back buttons before pressing Back — and `open_search()` called `navigate_to_home()` again internally.
- The worker handlers additionally ran a `navigate_to_home()` before search initiation and another one between retry attempts.
- The only visibility into any of this was business-level logging and PocketBase task streams, which cannot show which UI call is stalled.

## Decision
1. **Single verified anchor for the home state and search entry**:
   `job_list.search_icon` is exactly one XPath (`.../ly_menu/RelativeLayout[2]/ImageView[@resource-id='img_icon']`). No fallback list. A miss costs one query, not seven.
2. **Two-anchor entry engine in `JobListPage.open_search()`**:
   - search input already on screen -> return immediately, no navigation;
   - entry icon on screen -> click it, wait for the input box;
   - neither -> press hardware `KEYCODE_BACK`, wait 0.5s, re-evaluate, capped at 10 attempts, re-activating Boss if the foreground package is no longer `com.hpbr.bosszhipin`.
   `navigate_to_home()` follows the same Back-only model. No dialog/back-button scanning anywhere.
3. **Handlers delegate entry, they do not pre-navigate**:
   `ScrapeJobsHandler` / `AutoApplyHandler` call `open_search()` directly; `navigate_to_home()` remains only for the `enable_search=False` recommendation feed. Retry logs report `第 n/2 次` and mark exhaustion on the terminal attempt instead of promising a retry.
4. **DEBUG telemetry inside the UI layer**:
   `HumanizedGestureExecutor` (click/tap/type/swipe) and `BaseBossPage` (`_find_by_selectors`, `find_by_key`, `wait_for_key`, `press_back`) emit `logging.getLogger("droid_agent_core.ui").debug(...)` with element summaries, coordinates, text length (never raw text) and per-selector elapsed seconds. `scripts/worker.py --log-level DEBUG` switches the namespace on; at INFO it is silent and never reaches the PocketBase stream.

## Trade-offs
- Blind Back presses can escape the app (to the launcher) instead of unwinding a screen; the bounded count plus the foreground re-activation guard keep that self-correcting rather than fatal.
- Back does not unwind a bottom tab: measured on emulator-5554, two Back presses from the 消息 tab left the app outside the home anchor, while clicking the 职位 tab returned home. The recovery loop therefore clicks the 职位 tab anchor (one fast `find_now` query) before each Back press — one deliberate exception to "two anchors only", because that tab *is* the home column.
- Recovery now depends on the two anchors alone. If a future Boss build renames `img_icon` or `et_search`, entry fails fast and loudly instead of silently succeeding via some legacy fallback — that is the intended behaviour, and the DEBUG telemetry is what makes the new anchor discoverable.
