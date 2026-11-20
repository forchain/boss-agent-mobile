# 0021. Unified Automation Layer Behind a Platform-Neutral Driver Port

## Context
ADR 0002 split the automation framework from the Boss domain by package boundary. That split
did not actually isolate the platform: `gestures` imported Selenium/Appium to reach a W3C pointer
batch, page objects typed against an Appium `Remote`, and any future Web-platform worker would
have had to rewrite domain code. A package boundary that still leaks platform types is not a
seam.

## Decision
1. Fold the automation modules into the application package under `boss_agent.automation`
   (`port`, `gestures`, `locators`, `interceptors`). The LLM port lives in
   `boss_agent.llm_port`; concrete HTTP clients and the Appium driver live under
   `boss_agent.adapters`.
2. Define the seam explicitly in `boss_agent.automation.port`:
   - `DriverPort` is a `typing.Protocol` covering only what the automation code uses — element
     lookup, gesture execution, session lifecycle/liveness, and app activation.
   - `ElementHandle` and `By` describe a located element and its strategy without importing a
     platform client.
3. Keep Appium in exactly one module, `boss_agent.adapters.appium_driver`. `AppiumDriver`
   wraps an Appium `Remote`, explicitly implements every `DriverPort` member, returns
   `ElementHandle` wrappers from `find_elements`, and implements the W3C double-tap batch
   that the raw client cannot express. `AppiumSession` and `DriverConfig` live there too.
4. Keep `boss_agent.automation.__init__` platform-free: composition roots name the adapter
   directly, so importing the neutral facade never imports Appium.
5. Rename the UI telemetry logger namespace to `boss_agent.automation.ui`.
6. Guard the boundary in `tests/unit/test_framework_isolation.py`: only the `adapters` package
   may import Appium/Selenium, no neutral module may import an adapter, and no automation module
   may import a Boss domain layer. The guard resolves relative imports, so it cannot be bypassed
   by a `from . import ...` spelling.

## Consequences
- A Web-platform worker supplies its own `DriverPort` adapter; gestures, locators, interceptors,
  and page objects do not change.
- The old top-level framework package is gone, so there is one import root and no compatibility
  shim to keep alive.
- The `boss_agent` lazy facade itself must stay platform-free; only composition roots (worker
  CLI, live-test runner, E2E fixtures) import `appium_driver`.
