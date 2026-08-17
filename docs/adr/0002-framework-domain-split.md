# 0002. Split Core Automation Framework and Boss Domain Layer

We decided to decouple the codebase into two top-level packages: `droid_agent_core` (agnostic automation framework) and `boss_agent` (Boss-specific application logic).

## Context
The automation framework needs to be reusable for other Android applications in the future, extractable as a standalone project, and strictly isolated from any business-specific logic.

## Decision
1. `droid_agent_core`: Universal Android automation primitives (device/session management, UI selectors, humanized gestures, popup interceptor, LLM decision agent interface). No Boss-specific code.
2. `boss_agent`: Concrete Page Objects, match criteria, candidate profiles, and application workflows consuming `droid_agent_core`.
