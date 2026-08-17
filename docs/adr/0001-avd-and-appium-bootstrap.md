# 0001. Android Virtual Device & Appium for Automation Bootstrap

We decided to use official Android Command-line Tools with AVD (ARM64 Google APIs image) and Appium UiAutomator2 driver as the core runtime.

## Context
We need a headless/GUI capable, 100% idempotent environment setup script on macOS Apple Silicon that can download tools, create emulators, and install the Boss Android APK without manual UI intervention.

## Decision
1. Use Google Android SDK CLI tools (`sdkmanager`, `avdmanager`, `emulator`) with HVF acceleration on macOS ARM64.
2. Use Appium UiAutomator2 server and client for automation control.
3. Reject third-party desktop emulators (MuMu/Genymotion) and Docker-based emulators (Redroid) because they cannot be cleanly automated via pure CLI or have excessive overhead on macOS.
