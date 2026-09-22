# 0012. Communicated Job Detection, Re-application Cool-down, and Enterprise-Level Direct-Hire Exclusion

We decided to detect communicated jobs via the detail page call-to-action button (`btn_chat`), record them as `applied` with quota-decoupled timestamps, enforce zero-overhead card-level skipping, suppress all other postings under the same direct-hire enterprise, provide a configurable re-application cool-down window, and support manual state clearance.

## Context
When browsing job postings in the Boss 直聘 Android app:
- Newly discovered jobs display "立即沟通" on the detail page (`com.hpbr.bosszhipin:id/btn_chat`), whereas jobs previously contacted by the candidate display "继续沟通".
- Previously, the agent lacked platform communication state awareness; it relied solely on local database state and would repeatedly click into jobs already contacted manually on the candidate's phone, wasting mobile navigation time.
- Direct-hire enterprises (`is_headhunter == False`) share internal candidate records across HR teams. Contacting multiple roles at the same company simultaneously or after a rejection is ineffective and wastes daily greeting quota. Conversely, headhunters (`is_headhunter == True`) represent disparate clients and must not trigger company-wide suppression.
- Furthermore, past contact should not permanently blacklist a company or role: candidate hiring cycles renew, roles reopen, and user preferences evolve over time (e.g. after a 30-day cool-down).
- Finally, the existing daily greeting count queried `status == 'applied' && updated >= today`. Ingesting a historically communicated job as `applied` would falsely exhaust the daily greeting limit unless send tracking was decoupled.

## Decision
1. **Button-State Detection & Immediate Backout**:
   - In `JobDetailPage.extract_job_posting()`, inspect `btn_chat` (`com.hpbr.bosszhipin:id/btn_chat`).
   - If text matches "继续沟通": record job as `applied` (`platform_historical`), skip description expansion and JD extraction, and navigate back immediately.
   - If text matches "停止招聘" / "职位已关闭" / "已下线": record job as `ignored` (expired) and back out.
   - If text matches "立即沟通" / "聊一聊" / "去沟通" / "发消息": proceed with normal JD extraction, evaluation, or greeting.
2. **Quota Tracking Decoupled via `applied_at`**:
   - Add explicit timestamp `applied_at` on `job_records`.
   - Only set `applied_at` when the agent actually executes `chat_page.click_send()`.
   - `count_today_applied_jobs()` filters strictly on `applied_at >= {today_midnight}`, insulating daily quotas from platform historical imports.
3. **Enterprise-Level Direct-Hire Exclusion with In-Memory Caching**:
   - Direct-hire jobs (`is_headhunter == False` and not masked) in `applied` status trigger company-wide suppression for other roles under the same `company_name`.
   - Pre-load an in-memory set `applied_direct_companies` during task initialization and update it dynamically on new applications.
   - Suppress candidate cards at the zero-cost list-card layer (`JobListPage`) without clicking into detail pages.
   - Headhunters (`is_headhunter == True`) and masked company names (`is_masked_company_name`) are strictly exempted via guardrails.
4. **Re-application Cool-down Window**:
   - Introduce `communication_cooldown_days` (default 30 days; 0 = permanent) in `config/settings.yaml` and Settings UI.
   - Jobs or enterprise exclusions whose elapsed time since communication (`applied_at` or `created`) exceeds the cool-down window automatically expire, becoming eligible for re-scanning and re-application.
5. **Manual State Clearance & Reversion**:
   - In the Web UI (`/jobs` page under `已沟通` tab and `/settings`), provide controls to clear communication status for single jobs or entire enterprises.
   - Clearing resets `status` from `applied` back to `jd_saved` and nullifies `applied_at`, retaining extracted JD text and allowing re-evaluation/re-outreach without re-scraping.

## Trade-offs
- Skipping JD expansion for "继续沟通" jobs means historical jobs ingested from the feed will lack full JD text in the dashboard. This trade-off is accepted because mobile page expansion takes 3-5 seconds per card, and rapid feed throughput is prioritized.
- Direct-hire enterprise exclusion assumes standard company name matching. Subsidiaries with distinct legal entity names on Boss are treated independently unless matched by name.
- Dynamic cool-down calculation avoids heavy batch update cron jobs, but requires all card-level pre-checks to evaluate timestamps against the active threshold.
