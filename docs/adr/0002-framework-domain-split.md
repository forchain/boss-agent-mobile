# 0002. Split Core Automation Framework and Boss Domain Layer

**Status**: SUPERSEDED by [0021](0021-unified-automation-layer-and-driver-port.md) (2026-11-20).

We decided to decouple the codebase into two top-level packages: an agnostic automation
framework and `boss_agent` (Boss-specific application logic).

## Context
The automation framework needs to be reusable for other Android applications in the future,
extractable as a standalone project, and strictly isolated from any business-specific logic.

## Decision
1. The framework package: Universal Android automation primitives (device/session management, UI
   selectors, humanized gestures, popup interceptor, LLM decision agent interface). No
   Boss-specific code.
2. `boss_agent`: Concrete Page Objects, match criteria, candidate profiles, and application
   workflows consuming the framework package.

## Consequences
The framework was later folded back into `boss_agent` so that a platform-neutral driver port,
rather than a package boundary, carries the isolation guarantee. See ADR 0021.
