# boss-agent-mobile

Android mobile automation framework and intelligent job application agent for Boss 直聘.

## Overview
`boss-agent-mobile` is designed to automate job discovery, AI matching, and application workflows on Android devices. Operating directly on mobile provides richer feature sets and significantly lower anti-bot risk compared to web scrapers.

## Project Structure
- `src/droid_agent_core/`: Universal, application-agnostic Android automation framework with Bézier gesture synthesis, popup interceptors, and LLM reasoning interfaces.
- `src/boss_agent/`: Boss 直聘 domain implementation (Page Objects, workflows, session persistence, and job parsers).
- `scripts/bootstrap.py`: Idempotent environment provisioner for JDK, Android SDK, AVD emulators, Appium, and APK installation.

## Key Documents
- [CONTEXT.md](file:///Volumes/Data/orca/workspaces/boss-agent-mobile/sturgeon/CONTEXT.md): Project domain glossary and ubiquitous language.
- [ACCEPTANCE.md](file:///Volumes/Data/orca/workspaces/boss-agent-mobile/sturgeon/ACCEPTANCE.md): Agent-friendly Phase 1 Acceptance Baseline & Multi-Agent Protocol.
- [docs/adr/](file:///Volumes/Data/orca/workspaces/boss-agent-mobile/sturgeon/docs/adr/): Architectural Decision Records (ADR 0001 - 0005).

## Multi-Agent Protocol
This project utilizes the **Agent Triad Model** (Dev Agent, Test Agent, Acceptance Agent) operating in isolated contexts to maintain high rigor, prevent context saturation, and ensure zero confirmation bias.
