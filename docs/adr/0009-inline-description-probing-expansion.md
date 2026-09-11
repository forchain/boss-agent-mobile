# 0009. Bottom-Right Hotspot Targeting for Inline Description Expansion

We decided to implement a direct bottom-right hotspot targeting mechanism in `JobDetailPage` and `HumanizedGestureExecutor` to trigger ClickableSpan expansion for inline truncated job descriptions in the Boss 直聘 Android App without requiring external coordinate configuration.

## Context
In the Boss 直聘 mobile UI, the job description view (`com.hpbr.bosszhipin:id/tv_description`) renders truncated text ending with `... 查看更多`.
- The `... 查看更多` text is not an independent View element (e.g. Button or TextView) in the Android Accessibility / UiAutomator2 hierarchy, but an inline `ClickableSpan` inside a non-clickable `TextView`.
- Traditional Appium locators (XPath, ID, Text) cannot locate or click `查看更多`.
- Empirical testing on live devices confirmed that Boss 直聘 binds the expand touch hotspot specifically to the **bottom-right corner** of `tv_description`, regardless of where the preceding text lines wrap.
- Calling element center clicks or raw ADB taps either misses the ClickableSpan or risks anti-bot detection, while requiring manual multi-point configuration in YAML adds unnecessary operational complexity.

## Decision
1. **Direct Bottom-Right Hotspot Targeting**:
   Eliminate external probe configuration from `config/locators.yaml`. Target the bottom-right corner of `tv_description` directly:
   - Primary hotspot: ~90% element width, ~25px above the bottom edge (`[0.90, 25.0]` relative to bottom-left).
   - Secondary fallback: ~80% element width, ~25px above bottom edge (`[0.80, 25.0]`).
2. **Viewport Safety & Anti-Detection Gestures**:
   - Verify that the bottom of `tv_description` is within the visible viewport; smoothly scroll upward if obstructed by the bottom floating action bar (`立即沟通`).
   - Apply Gaussian micro-jitter ($\pm 3\text{px}$) and touch down/up duration via `HumanizedGestureExecutor.human_click_at_point`.
3. **Early-Stopping Verification**:
   - Re-evaluate `tv_description.text` after the tap; immediately terminate once `查看更多` is no longer in text or text length significantly expands.
