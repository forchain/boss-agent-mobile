# Boss Agent Mobile

Mobile automation framework and intelligent job application agent for the Boss 直聘 Android App.

## Language

**Core Automation Framework (`droid_agent_core`)**:
The reusable, application-agnostic Android automation engine handling driver lifecycles, element resolution, humanized gesture synthesis, popup interception, and LLM reasoning interfaces.
_Avoid_: Boss automation engine, base scripts, appium helper

**App Domain Layer (`boss_agent`)**:
The Boss-specific application module implementing page objects, candidate profiles, matching rules, greeting templates, and application workflows on top of `droid_agent_core`.
_Avoid_: Core app, main module

**Environment Provisioner**:
The idempotent multi-tier detection and setup lifecycle that verifies and configures Java, Android SDK tools, AVD emulators, Appium server, and target APKs.
_Avoid_: Install script, setup helper, env checker

**Virtual Device Session**:
The managed lifecycle of an Android Virtual Device (AVD) instance, its hardware profile, headless/GUI runtime states, and ADB port bindings.
_Avoid_: Emulator runner, VM instance

**Smoke Harness**:
The end-to-end verification pipeline that boots the emulator, launches Boss App, dismisses startup dialogs, checks auth readiness, navigates job cards, and parses job details.
_Avoid_: E2E test script, sanity check, launch test

**Session Persistence**:
The mechanism that detects user authentication status and preserves the logged-in app state across virtual device restarts to prevent repeated manual logins.
_Avoid_: Login keeper, cookie store, auth cache

**Humanized Interaction Engine**:
The gesture and timing synthesizer within `droid_agent_core` that applies Bézier curves, randomized touch down/up durations, spatial jitter, and variable pauses to emulate real user touch behavior.
_Avoid_: Click helper, sleep wrapper, tap util

**Takeover Handler**:
The safety interceptor that detects security challenges (slider captchas, SMS verification, session expiry), halts automation, and alerts the user for manual completion before resuming.
_Avoid_: Captcha solver, manual fallback

**Acceptance Baseline (`ACCEPTANCE.md`)**:
The self-contained, machine-verifiable single source of truth defining deliverables, executable acceptance criteria, runtime anomaly logs, and evidence records across agent sessions.
_Avoid_: Test doc, checklist, PRD, spec doc

**Agent Triad**:
The role separation protocol between Dev Agent (implementer), Test Agent (runner & edge-case explorer), and Acceptance Agent (clean-context gatekeeper) operating with isolated contexts.
_Avoid_: Multi-agent pipeline, subagent team

**Backend Application Service**:
The modular monolith FastAPI service handling authentication, rule configuration, task validation, and command submission into the persistent state stream.
_Avoid_: API gateway, microservice gateway, proxy service

**State Stream Broker**:
The lightweight persistence and event mechanism utilizing PocketBase tables and Realtime SSE subscriptions for task queueing, optimistic lease locks, and live UI status updates.
_Avoid_: Redis broker, RabbitMQ cluster, in-memory queue

**Automation Worker**:
The dedicated out-of-process execution daemon bound 1:1 to a Virtual Device Session that claims pending tasks from the State Stream Broker, executes mobile UI automation workflows, and reports execution telemetry.
_Avoid_: In-process background task, Celery pool, worker thread

**Task Handler Strategy**:
The polymorphic workflow dispatch within the Automation Worker that executes concrete automation jobs (`CHECK_LOGIN`, `SCRAPE_JOBS`, `AUTO_APPLY`, `CHECK_CHAT`) without inter-process device contention.
_Avoid_: Multi-worker router, sub-worker cluster

