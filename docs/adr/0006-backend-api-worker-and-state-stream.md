# 0006. Modular Monolith API, State Stream Broker, and Out-of-Process Worker

We decided to adopt a Modular Monolith FastAPI service, a PocketBase State Stream Broker, and an independent Out-of-Process Automation Worker bound 1:1 to each Virtual Device Session.

## Context
Mobile automation tasks against the Boss 直聘 Android App are long-running (5–30 minutes), require exclusive access to the virtual device UI screen, and are prone to Appium/ADB/emulator crashes. 
- In-process execution (e.g., FastAPI `BackgroundTasks`) risks blocking the ASGI event loop, lacks concurrency mutexes for screen exclusivity, and causes total Web API downtime if the automation driver crashes.
- Distributed message brokers (e.g., Celery, RabbitMQ, Redis) introduce unnecessary deployment and operational overhead given the physical device throughput limit (1–3 AVD instances per host).

## Decision
1. **Backend Application Service (FastAPI)**: Operates as a modular monolith handling authentication, candidate profile management, search rule configurations, and task admission. It publishes tasks to PocketBase and immediately returns `202 Accepted`.
2. **State Stream Broker (PocketBase)**: Serves as the persistence layer and lightweight task broker. Tasks are queued in `automation_tasks` with optimistic lock leases. PocketBase Realtime (SSE) provides instant status and log streaming directly to the Svelte frontend.
3. **Out-of-Process Automation Worker**: Runs as a dedicated Python daemon process bound 1:1 to a `Virtual Device Session`. It sequentially claims `pending` tasks, dispatches polymorphic `Task Handler Strategies` (`CHECK_LOGIN`, `SCRAPE_JOBS`, `AUTO_APPLY`, `CHECK_CHAT`), and handles `Takeover Handler` pause/resume lifecycles without screen contention.
