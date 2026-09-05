# 0007. Database Persisted SavedSearches, Zero-Startup Automation Worker, and Cron Scheduler

We decided to persist all search presets (`SavedSearch`) in PocketBase instead of static YAML files, decouple Worker boot from automatic search execution, and introduce an ad-hoc and Cron-driven scheduling engine.

## Context
Previously, search queries and filter configs were stored statically in `config/searches.yaml`. 
- Modifying or creating new search criteria required manual file edits, risking syntax errors, and prevented remote configuration from the Web console.
- In earlier prototypes, workers might attempt search execution immediately on startup, creating concurrency conflicts, unexpected device operations, and lack of fine-grained control over execution timing.
- Users require multi-dimensional job search management (education, salary, experience, activity, company size, industry tags), on-demand manual triggering from the Web UI, and periodic unattended automation via Cron expressions.

## Decision
1. **Database Persisted SavedSearch (`saved_searches`)**:
   Migrated from `config/searches.yaml` to PocketBase collection `saved_searches` with schema fields: `name`, `description`, `keyword`, `filter`, `cron_expression`, `is_enabled`, `last_run_at`, and `target_task_type`. Initial startup provisions the table and automatically seeds default searches from `searches.yaml` if empty.
2. **Zero-Startup Automation Worker**:
   The `AutomationWorker` daemon boots in a purely receptive, idle listening state. It executes search tasks solely when explicitly commanded via pending `automation_tasks` dispatched to the `State Stream Broker`.
3. **Web Console CRUD & Ad-Hoc Triggering**:
   The Web UI (`/searches`) provides complete interactive CRUD modal management for `SavedSearch`, multi-dimensional filter selection, immediate dispatch (`AUTO_APPLY` / `SCRAPE_JOBS`), and live SSE synchronization.
4. **Cron Scheduler Engine (`AutomationScheduler`)**:
   A lightweight, dependency-free Cron evaluation engine (`AutomationScheduler`) polls enabled saved searches (`is_enabled=True`), parses standard 5-field Cron expressions, dispatches tasks at designated times, and records `last_run_at` to prevent duplicate executions within the same scheduling window.
