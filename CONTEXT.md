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

**Job Record**:
The persisted entity representing a job posting discovered from mobile search results or scraping workflows in the Boss app.
_Avoid_: Scraped item, raw post, search card

**Job Fingerprint**:
The canonical deduplication identifier computed strictly from the three visible elements on the search result card: job title, company name, and recruiter name.
_Avoid_: Hash key, job uid, composite id

**Unmatched Job Stream**:
The dedicated triage view displaying newly discovered, deduplicated job records that have not yet undergone match evaluation.
_Avoid_: Job list, raw queue, inbox view

**SavedSearch**:
The database-persisted search strategy entity stored in PocketBase encapsulating search keyword, multi-dimensional filter conditions, task type, and Cron scheduling metadata.
_Avoid_: Search rule, search YAML, query profile

**Automation Scheduler**:
The background Cron evaluation engine that monitors active SavedSearches, detects scheduling matches, and dispatches automation tasks into the State Stream Broker.
_Avoid_: Crontab daemon, task timer, periodic runner

**Task Management Dashboard**:
The unified web operational command center (`/`) coordinating real-time active task telemetry, historical task audit logs, and scheduled automation jobs without duplicate entity widgets.
_Avoid_: Control panel, home view, main dashboard

**Settings Panel**:
The dedicated, extensible system configuration view (`/settings`) housing LLM parameters, connectivity testing, and modular placeholders for future device bindings and notification rules.
_Avoid_: Config tab, options modal, preference page

**Scheduled Job**:
The periodic automation instance defined by a standard Cron expression and bound to a SavedSearch strategy, evaluated by the Automation Scheduler and managed via the Task Management Dashboard.
_Avoid_: Cron task, recurring search, periodic run

**Candidate Profile (`candidate_profiles`)**:
The single source of truth structured candidate persona stored in PocketBase, encapsulating full unabbreviated work experiences, detailed project accomplishments, deep skill taxonomies, target preferences, and ground truth raw resume context.
_Avoid_: candidate_memory.json, candidate config, memory cache

**Resume Revision (`resume_revisions`)**:
The append-only versioned history entity in PocketBase tracking each uploaded resume file, timestamp, file metadata, extracted raw text, and incremental diff changelog against the active Candidate Profile.
_Avoid_: resume upload temp, upload record, candidate file

**Incremental Profile Merge**:
The LLM-driven structural diffing and human-in-the-loop review workflow that compares a newly uploaded Resume Revision against the existing Candidate Profile, surfacing detected deltas for confirmation before committing to the database.
_Avoid_: resume overwrite, profile replacement, auto-parse override

**Screening Policy (`ScreeningPolicy`)**:
The structured configuration encapsulating candidate negative constraints, title whitelists, title blacklists, company blacklists, and JD-level blacklists declared in `config/screening.local.yaml`. Blacklists enforce one-strike rejection. One-strike rejection is deterministic only over the compact card facets (title, tags, digest); a blacklisted term is never deterministically scanned across the full Job Description, because a passing mention there does not indict the role. The full-JD blacklist verdict is semantic: rejection only when the blacklisted subject matter constitutes the job's core requirement or primary stack, never when merely referenced as background, nice-to-have, or negation. The whitelist is not an inclusion gate and never rejects a job; it exists solely as relaxation tokens for App-Enforced Filters, encoding subject matter the candidate cares deeply about or is strong in, strong enough to widen a condition the app itself imposed.
_Avoid_: Filter keywords, blacklist config, keyword rules

**App-Enforced Filter**:
A screening condition the Boss platform does not offer natively in its search interface — recruitment channel (direct-hire vs headhunter), commute distance ceilings, and future peers — and that our app therefore evaluates in its own logic against jobs already retrieved from the platform. Because the platform cannot pre-filter these, jobs violating them are skipped by our own judgement rather than by platform relevance, and every such skip is subject to Whitelist Relaxation.
_Avoid_: platform filter, native search condition, search keyword

**Whitelist Relaxation (白名单放宽)**:
The exemption rule by which a job an App-Enforced Filter would skip is admitted into the normal pipeline after all when its card facets (title, tags, company, digest) hit a whitelist token, because strong personal interest or expertise is deemed to outweigh the violated condition. It is the sole surviving role of the whitelist: an interest signal that widens app-side constraints, never an inclusion gate that rejects non-matching jobs.
_Avoid_: whitelist gate, inclusion filter, positive match

**Configuration Realm (`config/*.yaml`)**:
The declarative, human-readable single source of truth for all global system configurations (LLM, screening policies, candidate persona, settings), managed by developers, administrators, or Web UI endpoints.
_Avoid_: runtime config, app data folder, temporary settings

**Runtime State Directory (`.boss_agent/`)**:
The system-managed directory housing runtime database state (`pb_data`), caches, execution logs, and transient artifacts. Never used as a manual configuration store.
_Avoid_: config store, settings dir, user preference folder


**Candidate Screener Graph (`JobApplicationState`)**:
The stateful LangGraph orchestrator governing the complete multi-tier lifecycle from card-level keyword filtering, JD extraction, semantic screening, to targeted greeting generation.
_Avoid_: Screening pipeline, match chain, agent workflow

**Keyword Screener**:
The zero-token deterministic gatekeeper node evaluating visible job card metadata (title, tags, company, digest) against the active Screening Policy before triggering expensive mobile navigation. Confined to the compact card facets by design, where collateral over-rejection is tolerated because the short text mirrors the role's core; it never operates on the full Job Description.
_Avoid_: Title filter, card checker, fast screener

**JD Semantic Screener Agent**:
The token-optimized LLM agent evaluating extracted job descriptions against negative constraints and blacklist criteria without candidate resume overhead. It is the sole blacklists authority at JD stage: deterministic keyword matching is forbidden over the full description, and rejection happens only when a blacklisted subject matter semantically constitutes the role's core requirement, not a passing mention.
_Avoid_: Deep filter, JD checker, prompt screener

**Greeting Drafter Agent**:
The high-context LLM agent generating anti-template, tailored ice-breaking messages combining full candidate profile highlights with extracted JD pain points. The active Screening Policy's blacklists are dynamically injected into its matching judgement, so a job that fits the resume on paper but centers on a blacklisted subject matter (e.g. a Java role when Java is blacklisted) is disqualified at draft time even when neither the resume nor card text reveals the conflict.
_Avoid_: Greeting generator, ice breaker, message writer

**Greeting Prompt (`greeting_prompt.local.md`)**:
The single living Markdown document encoding how the Greeting Drafter Agent should write ice-breakers — the one and only long-term greeting memory surface. Seeded from a default, directly human-editable, and applied verbatim by every generation path. Supersedes the retired Greeting Style Rules list (ADR 0010).
_Avoid_: style rules, rule list, memory database, 多条规则

**Prompt Refinement (打磨)**:
The LLM step that rewrites the entire Greeting Prompt in light of one concrete job example (critique plus before/after greetings), preserving all existing guidance and merging lessons; it only persists after the candidate approves a before/after diff. Raw critiques are never persisted; only the adopted document survives.
_Avoid_: rule distillation, memory extraction, 沉淀规则, append-log

**Resume Lifecycle Graph (`ResumeLifecycleState`)**:
The stateful LangGraph orchestrator governing candidate resume ingestion, text extraction, structured profile document generation, semantic diffing, and database persistence.
_Avoid_: Resume script, resume pipeline, upload helper

**Structured Profile Document (`profile_document`)**:
The first-class lossless markdown document representing candidate background, technical taxonomy, key projects, architectures, achievements, and open-source contributions used as ground-truth context for matching and greeting.
_Avoid_: raw summary string, candidate notes, unstructured bio

**Profile Normalizer**:
The deterministic self-healing node within the resume lifecycle ensuring core metadata (name, years of experience, target positions, skills) and structured documents are intact and properly formatted without null or missing critical sections.
_Avoid_: Schema fixer, data cleaner, fallback parser

**Job Digest (`digest`)**:
The concise, single-line job summary or snippet extracted directly from the search result card (`tv_digest`) without navigating into the detail page.
_Avoid_: snippet, preview text, job desc summary, card body

**Job Description (`job_description`)**:
The full, comprehensive job duties, tech stack expectations, and qualifications extracted exclusively from the Job Detail Page (`tv_description`) after navigation and expansion.
_Avoid_: digest, snippet, short JD, brief intro

**Card Preliminary Screening**:
The zero-token deterministic gatekeeper evaluation that examines the three card-level facets (`tv_position_name`, `fl_require_info`, `tv_digest`) against the active `ScreeningPolicy` to eliminate non-viable jobs before incurring expensive mobile navigation.
_Avoid_: card filter, quick check, preliminary pass

**Target Action (`target_action`)**:
The configured execution depth for a search task governing whether discovered jobs undergo complete JD enrichment (`save_jd`) or automated greeting (`auto_apply`). Note: card digest only (`digest_only`) is deprecated in favor of full JD ingestion.
_Avoid_: search mode, scrape level, crawl stage

**Inline Description Expansion (Bottom-Right Hotspot Tap)**:
The coordinate targeting mechanism that taps the fixed inline ClickableSpan touch hotspot located in the bottom-right area of the job description text module (`tv_description`) to trigger full description expansion, bypassing Android accessibility node limitations without external coordinate configuration.
_Avoid_: blind tap, ocr clicker, hardcoded absolute coordinates


**Job Lifecycle State**:
The progression state of a Job Record tracking its data richness and application stage across mobile automation and backend manual actions (`ignored`, `jd_saved`, `matched`, `applied`; historical `digest_only` records map to `jd_saved`). The terminal `applied` state encompasses both Agent-Dispatched (`agent_auto_send`) greetings and Platform Historical Contacts (`platform_historical`); upon cool-down expiry or manual clearance, an `applied` record transitions back to `jd_saved` with its JD preserved for re-engagement.
_Avoid_: job status flag, task progress, record phase

**Communication Action Button (`btn_chat`)**:
The primary call-to-action button widget (`com.hpbr.bosszhipin:id/btn_chat`) on the Job Detail Page reflecting platform engagement status ("立即沟通" / "聊一聊" for uncontacted jobs, "继续沟通" for previously contacted jobs, and "停止招聘" / "职位已关闭" for expired postings).
_Avoid_: chat trigger, detail button, contact icon

**Platform Historical Contact (`platform_historical`)**:
A job posting previously engaged by the candidate on the platform prior to or outside agent execution, identified by "继续沟通" on the detail page, ingested directly into the database as `applied` without JD extraction or greeting dispatch to enable zero-overhead list-card skipping in subsequent scans.
_Avoid_: manual chat, external application, old job

**Enterprise-Level Direct-Hire Exclusion (直招同企避嫌 / 已沟通排重)**:
The deduplication guardrail wherein active communication (`status == 'applied'`) with any direct-hire enterprise (`is_headhunter == False`, non-masked) automatically suppresses all other job postings from that same company during card preliminary screening. Headhunter channels (`is_headhunter == True`) and masked company names are strictly exempted.
_Avoid_: company ban, company blacklist, total block

**Re-application Cool-down Window (复投冷却时效)**:
The configurable temporal threshold (`communication_cooldown_days`, default 30 days) after which previously communicated jobs and direct-hire enterprise exclusions expire, permitting re-evaluation and fresh application outreach without permanent suppression.
_Avoid_: re-apply timer, retry delay, expire timeout

**Communication State Clearance (沟通状态重置)**:
The manual or automated state recovery action that transitions an `applied` job record back to `jd_saved` (clearing `applied_at`) and releases enterprise-level exclusion, allowing re-engagement without re-scraping the mobile JD.
_Avoid_: unapply, job unblock, delete communication

**Search Feed Boundary**:
The explicit platform termination marker (`tv_tips` displaying "暂无符合职位，为你推荐") signalling the end of genuine search keyword results and preventing automation pagination into unrelated recommendation feeds.
_Avoid_: bottom banner, footer divider, scroll end

**Two-Anchor Search Entry**:
The engine that recognises exactly two states for entering search — the search input box (`et_search`) or the home search entry icon (`img_icon`) — and otherwise presses the hardware Back key (bounded, re-activating the app if Back escaped it) until one appears, rather than probing unrelated page and dialog controls.
_Avoid_: search flow, entry heuristic, navigation cascade

**UI Operation Telemetry**:
The DEBUG-level `droid_agent_core.ui` log stream recording every concrete UI action (click, tap, typed text length, swipe, key press) and selector lookup with its elapsed seconds; silenceable by log level and never written to the task broker stream.
_Avoid_: UI logging, debug prints, action trace

**Daily Greeting Limit**:
The system safety threshold restricting outbound mobile greeting volume per calendar day to protect user accounts from platform rate limits and anti-bot challenges, evaluated strictly against successful agent greeting dispatches (`applied_at >= today`), automatically degrading `auto_apply` to `save_jd` upon exhaustion.
_Avoid_: daily quota, message cap, max chats
**Outbound Message Indicator**:
The deterministic status badge (`iv_msg_status` displaying `[送达]` or `[已读]`) prefixed to a conversation card in the communication list, signalling that the candidate sent the last message and allowing automation to instantly bypass threads awaiting recruiter reply.
_Avoid_: message badge, read tag, delivery marker

**Communication-Only Filter ("仅沟通")**:
The dedicated sub-tab/filter within the message screen (`tv_tab_3` -> `tv_title` matching "仅沟通") that isolates active reciprocal conversations, cleanly separating candidate-sent waiting items from unhandled incoming recruiter responses.
_Avoid_: 新招呼, 全部消息, 互动标签

**Rejection Blacklist Ingestion**:
The automated triage workflow that detects explicit recruiter rejections in the communication list, extracts the employer, and commits it into the active `ScreeningPolicy` company blacklist under existing guardrails to prevent future wasted daily applications.
_Avoid_: auto-block, recruiter kicker, 拒信拉黑脚本

**Headhunter Agency Guardrail**:
The company-name-derived protection that keeps staffing and recruitment agencies (人力资源, 劳务派遣, 人才服务, 猎头, 企业管理咨询 …) out of the company blacklist. A rejection card carries no recruiter title, so the 猎头 signal that card-level screening uses is unavailable and the agency must be recognised from its own registered name instead. Deliberately narrow: industry words real employers also carry (咨询, 科技) never trigger it, because the guardrail exists to protect the many employers an agency represents, and a false positive can never be undone by the rejected conversation.
_Avoid_: agency filter, blacklist whitelist, 猎头豁免

