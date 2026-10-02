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

**Job Record Store (`JobRecordStore`)**:
The dedicated repository seam managing Job Record persistence, canonical deduplication fingerprints, direct-hire enterprise exclusion pools, daily greeting quota counting, and re-application cool-down lifecycle calculations independently from task lease brokerage.
_Avoid_: db helper, raw collection query, job cache

**Mobile Job Feed Pipeline (`JobFeedPipeline`)**:
The deep feed navigation and extraction engine encapsulating Two-Anchor Search Entry, bounded Back-only recovery, viewport card pagination, call-to-action button state checking, inline description hotspot expansion, and feed boundary termination without procedural handler micromanagement.
_Avoid_: scroll helper, feed crawler, page looper

**Automation Worker**:
The dedicated out-of-process execution daemon bound 1:1 to a Virtual Device Session that claims pending tasks from the State Stream Broker, executes mobile UI automation workflows, and reports execution telemetry.
_Avoid_: In-process background task, Celery pool, worker thread

**Task Handler Strategy**:
The polymorphic workflow dispatch within the Automation Worker that executes concrete automation jobs (`CHECK_LOGIN`, `SCRAPE_JOBS`, `AUTO_APPLY`, `CHECK_CHAT`) without inter-process device contention.
_Avoid_: Multi-worker router, sub-worker cluster

**Task Provenance (`source`)**:
The first-class lifecycle origin attribute on an `AutomationTask` (`manual`, `test`, `scheduler`) determining execution priority, dashboard visibility, and worker startup reclamation rules.
_Avoid_: task kind, is_test flag, task origin tag

**Automated Test Task (`source="test"`)**:
An automation task instantiated by test suites (`pytest`, `vitest`) during verification. Excluded from live Automation Worker execution and automatically cancelled upon worker startup sweep to prevent mobile device contention.
_Avoid_: test job, mock task, fake run, 测试用例任务

**Manual Task (`source="manual"`)**:
An automation task triggered intentionally by the user via the Task Management Dashboard or CLI for live job discovery or application workflows.
_Avoid_: user test, real task, active test, 主动测试

**Graceful Shutdown Protocol**:
The POSIX signal contract by which the Automation Worker and the Web Dashboard runner stop cooperatively: acknowledge the termination signal in their own log stream, halt the polling loop, abort or release in-flight resources (State Stream Broker task cancellation, Virtual Device Session release, port release), then confirm completion — so supervisors and test fixtures never have to rely on arbitrary sleeps or force-kills.
_Avoid_: hard stop, force quit, kill -9 policy

**E2E Pre-Test Teardown Gate**:
The session-scoped, opt-in test fixture (`BOSS_AGENT_ENFORCE_TEARDOWN=1`) that stops residual Automation Worker and Web Dashboard instances located through the shared runtime directory and verifies their shutdown feedback, guaranteeing exclusive use of the Virtual Device Session and the dashboard port when a run genuinely needs it. Left unopted, E2E runs touch nothing outside their own temporary state. Shared infrastructure (State Stream Broker, Appium, AVD) is deliberately out of its scope either way.
_Avoid_: test cleanup hook, pre-test reset script, teardown helper

**Fast Unit Test**:
The in-memory verification tier (`tests/unit/`) that exercises module interfaces against mocked collaborators — no Automation Worker, no Appium session, no bound host port, and no live LLM endpoint. It is the tier an unadorned `pytest` runs, and the one that must finish in tens of seconds with zero side effects on the machine.
_Avoid_: quick check, small spec, unit suite

**Service Integration Test (`@pytest.mark.e2e`)**:
The end-to-end tier that drives real services — the Automation Worker CLI, the SvelteKit Web Dashboard, nested test sessions — against ephemeral ports and per-test temporary state directories, so a run neither collides with nor shuts down services belonging to other worktrees. Opted into explicitly, never part of the default run. Live Device Tests deliberately do not carry this marker, so marker selection can never reach the emulator.
_Avoid_: integration spec, heavy test, slow suite

**Live Device Test (`@pytest.mark.live`)**:
The device tier that drives the shared Android Virtual Device through Appium, guarded by an explicit marker that no default, broad-path, or E2E-marker invocation can override — waking the emulator out from under another worktree is the failure this tier exists to prevent.
_Avoid_: real test, device suite, emulator spec

**Job Record**:
The persisted entity representing a job posting discovered from mobile search results or scraping workflows in the Boss app.
_Avoid_: Scraped item, raw post, search card

**Job Fingerprint**:
The canonical deduplication identifier computed strictly from the three visible elements on the search result card: job title, company name, and recruiter name.
_Avoid_: Hash key, job uid, composite id

**Unmatched Job Stream**:
The dedicated triage view displaying newly discovered, deduplicated job records that have not yet undergone match evaluation.
_Avoid_: Job list, raw queue, inbox view

**Job Detail Modal**:
The responsive modal dialog presentation for portrait and narrow viewports (< lg / 1024px) in the jobs discovery workbench (`/jobs`), encapsulating the Job Detail Studio. Triggered strictly by explicit user card interaction rather than initial page load, and supporting dismissal via backdrop click, sticky close button, and keyboard ESC key, as well as automatic dismissal upon terminal or removal actions (ignore, delete, blacklist, dispatch apply).
_Avoid_: Detail popup, floating sheet, job drawer, mobile studio page, 下拉抽屉

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
The structured configuration encapsulating candidate negative constraints, title whitelists, title blacklists, company blacklists, two business district lists, and JD-level blacklists declared in `config/settings.local.yaml`. The district lists are the 商圈黑名单 (`business_district_blacklist`), which refuses a location outright, and the 考察名单 (`business_district_inspect_list`), which names the only locations whose commute distance is worth measuring. Both are matched twice, at the Card Preliminary Screening stage over the card's location facet and again on the Job Location Line once the detail page has been read, which is what lets a 地铁站 be treated as a 商圈; the first match costs nothing, the second cannot precede the detail page because the platform publishes a station nowhere else. Blacklists enforce one-strike rejection. One-strike rejection is deterministic only over the compact card facets (title, tags, company, digest, location); a blacklisted term is never deterministically scanned across the full Job Description, because a passing mention there does not indict the role. The full-JD blacklist verdict is semantic: rejection only when the blacklisted subject matter constitutes the job's core requirement or primary stack, never when merely referenced as background, nice-to-have, or negation. The whitelist is not an inclusion gate and never rejects a job; it exists solely as relaxation tokens for App-Enforced Filters, encoding subject matter the candidate cares deeply about or is strong in, strong enough to widen a condition the app itself imposed.
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


**Candidate Screener (`CandidateScreener`)**:
The unified deep module consolidating zero-token card preliminary keyword checks, App-Enforced Filters, Whitelist Relaxation, JD semantic blacklist evaluation, and living Greeting Prompt drafting behind a minimal two-method interface (`evaluate_card` and `evaluate_job`). Supersedes shallow pass-through graph wrappers.
_Avoid_: filter runner, card checker, matcher script

**Candidate Screener Graph (`JobApplicationState`)**:
The LangGraph workflow that runs card screening and JD evaluation as two traced stages. It is a thin adapter over the Candidate Screener (ADR 0013): the graph contributes run configuration, tags and the serialized state contract, while every screening rule lives in the screener module.
_Avoid_: Screening pipeline, match chain, agent workflow

**Keyword Screener**:
The zero-token deterministic gatekeeper stage of `CandidateScreener.evaluate_card`, evaluating visible job card metadata (title, tags, company, digest, location) against the active Screening Policy before triggering expensive mobile navigation. Confined to the compact card facets by design, where collateral over-rejection is tolerated because the short text mirrors the role's core; it never operates on the full Job Description.
_Avoid_: Title filter, card checker, fast screener

**JD Semantic Screener Agent**:
The token-optimized LLM stage of `CandidateScreener.evaluate_job`, evaluating extracted job descriptions against negative constraints and blacklist criteria without candidate resume overhead. It is the sole blacklists authority at JD stage: deterministic keyword matching is forbidden over the full description, and rejection happens only when a blacklisted subject matter semantically constitutes the role's core requirement, not a passing mention.
_Avoid_: Deep filter, JD checker, prompt screener

**Greeting Drafter Agent**:
The high-context LLM stage of `CandidateScreener.evaluate_job`, generating anti-template, tailored ice-breaking messages combining full candidate profile highlights with multi-dimensional job context: Search Filter Context (e.g. target education '硕士'), card requirement tags, card digest, and full extracted JD. When search filters or card tags specify preferences (e.g. master's degree or master preferred), the agent elevates them to strong preferences even if the JD body only lists a lower minimum requirement (e.g. bachelor's), naturally highlighting the candidate's academic research fit and engineering achievements in the match reasons and greeting draft. The active Screening Policy's blacklists are dynamically injected into its matching judgement, so a job that fits the resume on paper but centers on a blacklisted subject matter (e.g. a Java role when Java is blacklisted) is disqualified at draft time even when neither the resume nor card text reveals the conflict. It only runs when a JD carries enough substance to greet from; a thin JD yields no greeting rather than a fabricated one.
_Avoid_: Greeting generator, ice breaker, message writer

**Search Filter Context (`search_filter`)**:
The structured multi-dimensional search parameters (target education, salary range, experience, activity, company scales, industries) configured in `FilterConfig` and threaded through `JobPosting` and `CandidateScreener.evaluate_job`. It conveys top-of-funnel recruiter intent to downstream evaluation and greeting drafting stages, allowing LLM matching to recognize elevated candidate qualifications (such as master's degree fit) that may not be explicitly repeated in the JD body.
_Avoid_: query params, filter payload, raw search dict

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
The zero-token deterministic gatekeeper evaluation that examines the compact card-level facets (`tv_position_name`, `fl_require_info`, `tv_digest`, `location`) against the active `ScreeningPolicy` to eliminate non-viable jobs before incurring expensive mobile navigation. Its `location` facet names a district and no 地铁站; a station entry in the 商圈黑名单 is therefore judged later, on the Job Location Line, which costs no tokens but does cost the detail page the card stage would have skipped — the trade the platform's own rendering forces.
_Avoid_: card filter, quick check, preliminary pass

**Depth Expression**:
The one key in a task payload that answers "does this run put a greeting on the wire" — `target_action`, the operator's configured Execution Depth. Issue #302 removed the second answer (`preview_only` + `auto_send`, which the send gate demanded both of) from everything the launch contract produces. The worker still *reads* that pair while tasks written by an older builder are in the queue, and records which shape it read, so the log says whether a run's depth was declared once or reconstructed from the legacy pair; a payload that states exactly half of the pair is refused rather than defaulted.
_Avoid_: preview flags, send flags, depth boolean

**Execution Depth**:
The single choice an operator makes about how far a run goes: 深度存JD (`save_jd`) enriches and stops, 自动打招呼 (`auto_apply`) enriches, matches and sends. It is the Target Action, seen from the operator's side, and it is the only depth statement a launch carries — issue #298 removed the separate "preview it, do not send it" switch, because a second key on one intent is how a strategy that says 自动沟通 came to greet nobody. Reading a greeting before it goes out is a thing a human does in the Web Dashboard, not a mode the agent runs in. The only surviving drill is the 拒信清扫 `dry_run`, which governs replies in the inbox rather than greeting depth.
_Avoid_: preview mode, safe mode, auto-send flag, send switch

**Target Action (`target_action`)**:
The configured execution depth for a search task governing whether discovered jobs undergo complete JD enrichment (`save_jd`) or automated greeting (`auto_apply`). The launch contract derives depth from it alone, so an `auto_apply` strategy actually sends its greeting whoever dispatches it; a caller that states a depth for a search or a targeted application is refused. Note: card digest only (`digest_only`) is deprecated in favor of full JD ingestion.
_Avoid_: search mode, scrape level, crawl stage

**Inline Description Expansion (Bottom-Right Hotspot Tap)**:
The coordinate targeting mechanism that taps the fixed inline ClickableSpan touch hotspot located in the bottom-right area of the job description text module (`tv_description`) to trigger full description expansion, bypassing Android accessibility node limitations without external coordinate configuration.
_Avoid_: blind tap, ocr clicker, hardcoded absolute coordinates

**Job Location Line (`tv_required_location`)**:
The Job Detail Page header row that names where the office is, rendered as `上海·浦东新区·张江(近13/16号线华夏中路地铁站)` and read in the same capture pass as the title, company and salary — before any scroll, because expanding the description can recycle the header out of the accessibility tree. It is the platform's only publication of a metro station: the card's location facet names a district and nothing finer, so a station an operator wants to refuse or to measure is knowable nowhere else. It degrades rather than fails — a line with no parenthesised suffix, or none at all (a headhunter posting may render none, since the platform conceals their client), records no station and rejects nothing. The two district lists match over this line as well as over the card facet, which is why a station name is written into the same 商圈黑名单 an operator already edits.
_Avoid_: card location, district facet, tv_distance, address

**Commute Distance Probe (`home_tip_vf`)**:
The bottom-of-page scroll search for the distance widget (`home_tip_vf`) on a Job Detail Page, executed only while the commute ceiling is active, only for direct-hire postings, and only for a posting whose district — or 地铁站 — is on the operator's 考察名单 (commute inspection list, spec #328). All three gates must hold: headhunter postings conceal the hiring enterprise and its office address, so the platform never renders the tip for them and probing there spends swipe budget discovering that; a district nobody asked to have measured is not worth 3-5 seconds of swipes, and an empty list therefore skips every posting. The card stage can only see a district, so the decision is taken twice: once on the card facet before the detail page is opened, and once on the Job Location Line, which is already in hand before any swipe. That second look may only turn a probe on, never off. A headhunter posting therefore keeps `commute_distance_km` at `None` and passes commute screening fail-open, exactly like any other unknown distance.
_Avoid_: distance filter, geolocation check, address lookup


**Job Lifecycle State**:
The progression state of a Job Record tracking its data richness and application stage across mobile automation and backend manual actions (`ignored`, `jd_saved`, `matched`, `applied`; historical `digest_only` records map to `jd_saved`). The terminal `applied` state encompasses both Agent-Dispatched (`agent_auto_send`) greetings and Platform Historical Contacts (`platform_historical`); upon cool-down expiry or manual clearance, an `applied` record transitions back to `jd_saved` with its JD preserved for re-engagement.
_Avoid_: job status flag, task progress, record phase

**Greeting Provenance (`greeting_source`)**:
Who wrote the greeting a Job Record holds: `human` — generated or edited in the Web Dashboard — or `agent_draft`. Provenance decides what the agent owes the text: a human copy is skipped by generation and sent verbatim, salutation and all, while anything else (including a record written before the field existed) is drafted for as usual. It exists because previewing stopped being a run mode: the way to read a greeting before it goes out is to write it here, and that only holds if the record remembers whose words they are. The 定向投递 modal's edited copy outranks the record's own.
_Avoid_: greeting owner, approved flag, manual greeting

**Draft Rung (`matched`)**:
The state of a Job Record whose greeting has been written by someone — an agent, or a human
in the dashboard — but has never left the device — a backend "AI 评估", an agent run that drafted it, or a run whose daily quota ran out. It counts as *known* in the Job Lifecycle ladder and therefore satisfies a 深度存JD pass, but it never satisfies 自动打招呼: only a delivered message does (issue #299). Treating a draft as finished is what left records stranded, unreachable by any later run.
_Avoid_: offline draft, pending manual send, soft-applied

**Inventory JD Reuse**:
Reading a Job Record's stored job description instead of the detail page, when the stored text passes the same two judgements a freshly extracted one is put through — enough signal to screen and greet from, and no `查看更多` / `展开` / trailing-ellipsis left in it. It skips the expansion tap, the body re-read, and the bottom distance probe *for a headhunter posting*, but never the contact-control probe, the commute ceiling (a distance never measured is still probed, and stays fail-open when unknown), the platform-history record, the same-employer guard or the quota count. It exists for the visits the Depth Visit Rule now allows, not instead of them.
_Avoid_: JD cache, skip scraping, fast path

**Depth Visit Rule**:
Whether a run still owes a Job Record a detail-page visit, judged separately from the monotonic write ladder. A 深度存JD run is satisfied by a record that already holds a JD; an 自动打招呼 run is satisfied only by `applied`. The two questions share a status column and nothing else, and merging them is the defect #299 fixes.
_Avoid_: state machine skip, rank check, already-progressed test

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
The system safety threshold restricting outbound mobile greeting volume per calendar day to protect user accounts from platform rate limits and anti-bot challenges, evaluated strictly against successful agent greeting dispatches (`applied_at` within today), automatically degrading `auto_apply` to an offline draft (`status: matched`) upon exhaustion while job discovery continues uninterrupted.
_Avoid_: daily quota, message cap, max chats

**Chat Triage (`ChatTriage`)**:
The deep module that owns the 仅沟通 rejection story end to end: the two-tier preflight short-circuit (Tier 1 bottom message tab dot, Tier 2 仅沟通 unread badge count), the unread-badge driven scanning with downward scroll swipes bounded by badge clearing and max scroll swipes ceiling, the zero-token Outbound Message Indicator bypass (where an unrecognised badge is evaluated, never skipped), the classify → guardrail → blacklist-ingest → acknowledge ordering, the dry run that touches nothing, and the stop-reason taxonomy a run ends on — the scan's exits (`unread_cleared`, `scroll_ceiling`, `empty_list`, `cancelled`, `max_scan_depth`, `scan_ceiling`, `lost_list`) plus the entry failure `list_unreachable`, recorded when the list could never be opened and no card was read. Its whole interface is one verb — `scan()` — returning a Triage Report of outcomes rather than call sequences, and its device world crosses two injected ports (a list reader and a chat actor) whose production adapters wrap the page objects, so a card arrives as data with an opaque handle and never as a device reference. It owns the Communication-Only Filter, Communication Unread Badge, Message Tab Unread Dot, Outbound Message Indicator, Rejection Blacklist Ingestion and the Headhunter Agency Guardrail as concepts; 仅沟通 List Recovery stays inside the page object behind its list reader (ADR 0016), the company blacklist itself stays owned by the Screening Policy, and `CHECK_CHAT` is only the dispatcher that resolves a run's settings and policy from the task payload and maps the report into the task telemetry the dashboard renders.
_Avoid_: chat handler, message triage loop, 拒信处理流程

**Outbound Message Indicator**:
The deterministic status badge (`iv_msg_status` displaying `[送达]` or `[已读]`) prefixed to a conversation card in the communication list, signalling that the candidate sent the last message and allowing automation to instantly bypass threads awaiting recruiter reply.
_Avoid_: message badge, read tag, delivery marker

**Communication-Only Filter ("仅沟通")**:
The dedicated sub-tab/filter within the message screen (`tv_tab_3` -> `tv_title` matching "仅沟通") that isolates active reciprocal conversations, cleanly separating candidate-sent waiting items from unhandled incoming recruiter responses.
_Avoid_: 新招呼, 全部消息, 互动标签

**Communication Unread Badge (仅沟通未读角标)**:
The numeric badge adjacent to the '仅沟通' sub-tab (`tv_count`, anchored on that tab's own title node so another sub-tab's count is never read as its own) indicating the count of unread recruiter conversations in the communication filter. Serves as the Tier 2 preflight probe and the primary termination driver for the chat triage scan; an absent or zero reading only ends a run once it holds across the settle window, since a badge that has not been drawn yet is indistinguishable from a cleared one.
_Avoid_: 未读计数, 小红点, 消息角标

**Message Tab Unread Dot (底栏消息未读红点)**:
The red notification dot on the bottom navigation '消息' tab (`fl_tab_3_red_dot`). Serves as the Tier 1 preflight probe: when the bottom bar is visible and lacks this dot, the entire account is zero-unread and triage short-circuits without navigating into the list. An absence must hold across the settle window before it counts, and a bar that is not on screen at all reads as *not* clear — nothing can be concluded about an account whose navigation is not visible.
_Avoid_: 底栏红点, 导航圆点, 消息红点

**Rejection Blacklist Ingestion**:
The automated triage workflow that detects explicit recruiter rejections in the communication list, extracts the employer, and commits it into the active `ScreeningPolicy` company blacklist under existing guardrails to prevent future wasted daily applications.
_Avoid_: auto-block, recruiter kicker, 拒信拉黑脚本

**Headhunter Agency Guardrail**:
The company-name-derived protection that keeps staffing and recruitment agencies (人力资源, 劳务派遣, 人才服务, 猎头, 企业管理咨询 …) out of the company blacklist. A rejection card carries no recruiter title, so the 猎头 signal that card-level screening uses is unavailable and the agency must be recognised from its own registered name instead. Deliberately narrow: industry words real employers also carry (咨询, 科技) never trigger it, because the guardrail exists to protect the many employers an agency represents, and a false positive can never be undone by the rejected conversation.
_Avoid_: agency filter, blacklist whitelist, 猎头豁免

**仅沟通 List Recovery (仅沟通自愈导航)**:
The bounded multi-tier navigation that lands a dispatched `CHECK_CHAT` on the 仅沟通 list from whatever screen the app happens to be on. Each step prefers an on-screen back affordance (`iv_back` / `iv_back_ai`) over the hardware Back key, clicks `消息` → `仅沟通` the moment the column is reachable, re-activates Boss if a Back press escaped it, and pauses between actions; exhausting the step budget is the only way the task reports an unreachable list (ADR 0016).
_Avoid_: back-button scanning, navigation cascade, retry loop

**Startup Rejection Cleanup Barrier (开服清扫闸门)**:
The startup rule that a service queues one marked `CHECK_CHAT` before anything else and holds every search task (`SCRAPE_JOBS` / `AUTO_APPLY`) until it reaches a terminal state, so employers that already rejected the candidate are in the company blacklist before the first search or greeting is dispatched. Derived from the pending queue rather than from process memory, so a cleanup queued by the Automation Scheduler binds the Automation Worker too, and any terminal outcome releases the barrier instead of deadlocking the pipeline (ADR 0016).
_Avoid_: startup lock, init mutex, 启动检查

**Unread-Badge Bounded Scan (未读角标驱动扫描)**:
The `CHECK_CHAT` traversal rule: a run checks two tiers of unread preflight probes for a short-circuit on zero-unread accounts, reads the visible screen of the 仅沟通 list, evaluates unread messages while skipping outbound cards, and performs bounded downward scroll gestures (`scroll_message_list`) only when unread messages remain below the fold. The traversal terminates once the unread badge is cleared (`unread_cleared`), or upon reaching safety ceilings (`scroll_ceiling` at `max_scroll_swipes` or list bottom; `scan_ceiling` at the card bound). Every probe absence that ends or continues a run is confirmed across a settle window first, so a screen that has not finished rendering cannot pass for a clean account. Supersedes First-Screen Scan (ADR 0017 supersedes ADR 0015).
_Avoid_: infinite scroll, unconstrained sweep, opening-screen only scan

**System Doctor (`doctor.sh`)**:
The holistic health diagnostic and remediation CLI tool that inspects end-to-end operational readiness across PocketBase State Stream, SvelteKit Web Dashboard, Python Worker, Appium automation server, Android Virtual Device, and LLM configuration with actionable remediation steps.
_Avoid_: sanity script, health checker, debug helper

**Dedicated Runner Scripts (`emulator.sh`, `appium.sh`, `pocketbase.sh`, `web.sh`, `worker.sh`)**:
The first-class shell lifecycle scripts managing process states (start, stop, restart, status, daemon mode) with persistent logging and auto-attach log streaming across all operational infrastructure tiers.
_Avoid_: helper scripts, launcher utils, batch scripts

**Master Service Orchestrator (`run.sh`)**:
The top-level orchestration entrypoint coordinating service groups (`infra`, `app`, `all`) and dispatching subsystem commands (`worker`, `web`, `pb`, `emu`, `appium`, `doctor`, `live`) without implementing inline process management. Default bare execution (`./run.sh`) boots application services in the background and automatically attaches to the live Automation Worker log stream.
_Avoid_: monolithic runner, kitchen-sink script

**Infrastructure Services (基础服务)**:
The machine-shared backend services (`pocketbase.sh`, `emulator.sh`, `appium.sh`) that maintain persistent state, device emulation, and OS automation bridges, remaining running across multiple parallel worktree switches.
_Avoid_: worker services, client tier, host daemons

**Application Services (应用服务)**:
The per-worktree operational components (`worker.sh`, `web.sh`) that execute automation tasks and render the user dashboard, subject to worktree-level preemption and restart.
_Avoid_: backend services, shared infra, base daemons

**Cross-Worktree Service Preemption**:
The operational contract whereby executing `restart` on an application service (`web.sh restart`, `worker.sh restart`, or `run.sh restart`) gracefully stops lingering processes from other worktrees per the Graceful Shutdown Protocol and binds the port or mobile device session exclusively to the active worktree.
_Avoid_: port clash, session steal, silent conflict



