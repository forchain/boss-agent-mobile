# 0005. Anti-Detection, Humanized Interaction, and Manual Takeover

We decided to embed a Bézier-curve humanized touch synthesizer in `droid_agent_core` and a human-in-the-loop takeover handler in `boss_agent`.

## Context
Boss 直聘 enforces anti-bot protections on both Web and Mobile platforms, analyzing interaction trajectories, timing anomalies, and hardware fingerprints.

## Decision
1. `droid_agent_core` generates multi-point Bézier touch movements, randomized bounding-box click offsets, and realistic down/up touch pressure durations rather than instantaneous ADB/UiAutomator clicks.
2. `boss_agent` implements a Takeover Handler that pauses automation upon encountering slide captchas or SMS verification, allowing manual intervention in the emulator GUI before resuming.
3. Session state is persistently mounted to avoid recurring new-device authentication triggers.
