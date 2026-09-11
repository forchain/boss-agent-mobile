# 0009. Multi-Point Probabilistic Coordinate Probing for Inline Description Expansion

We decided to implement a multi-point probabilistic coordinate probing engine in `JobDetailPage` and `HumanizedGestureExecutor` to trigger ClickableSpan expansion for inline truncated job descriptions in the Boss 直聘 Android App.

## Context
In the Boss 直聘 mobile UI, the job description view (`com.hpbr.bosszhipin:id/tv_description`) renders truncated text ending with `... 查看更多`.
- The `... 查看更多` text is not an independent View element (e.g. Button or TextView) in the Android Accessibility / UiAutomator2 hierarchy, but an inline `ClickableSpan` inside a non-clickable `TextView`.
- Traditional Appium locators (XPath, ID, Text) cannot locate or click `查看更多`.
- Due to font sizing, screen width, and punctuation wrapping, `... 查看更多` does not always reside in a fixed position: it may wrap across the last two lines (e.g. "查看" at line $N-1$ right, "更多" at line $N$ left).
- Calling element center clicks or raw ADB taps either misses the ClickableSpan or risks anti-bot detection.

## Decision
1. **Configurable Coordinate Reference System**:
   Define probe points relative to the element's **bottom-left corner** (`origin: "bottom-left"`):
   - Values $\le 1.0$ represent width/height ratios of the element.
   - Values $> 1.0$ represent absolute pixel offsets ($X$ extending rightward, $Y$ extending upward into the element: $X = X_{BL} + dx$, $Y = Y_{BL} - dy$).
   - Stored in `config/locators.yaml` under `job_detail.inline_expand_probes` and overridable via `config/locators.local.yaml`.
2. **Multi-Tier Probability Probing**:
   Sequence probe attempts from highest to lowest probability across the last two text lines:
   - `[0.90, 20]`: Last line, far right (primary default for standard right-aligned truncation)
   - `[0.90, 60]`: Second-to-last line, far right (for cases where "查看" ends on the previous line)
   - `[0.70, 20]`: Last line, mid-right
   - `[0.25, 20]`: Last line, left
   - `[0.50, 60]`: Second-to-last line, center
3. **Viewport Safety & Early Stopping**:
   - Verify element bottom visibility before clicking; scroll upward if obstructed by bottom bars.
   - Apply Gaussian micro-jitter ($\pm 3\text{px}$) and touch down/up duration to each tap via `HumanizedGestureExecutor`.
   - Re-evaluate `tv_description.text` after each tap; immediately terminate the probe loop once text length expands or `查看更多` disappears.
