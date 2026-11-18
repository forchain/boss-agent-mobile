# 0022. Single-Owner Launch Contract Reached Through the Server

We decided that `boss_agent.task_launch` is the only owner of Depth Expression and Task Provenance, that the Web Dashboard posts a launch *intent* to the server, and that the TypeScript mirror is deleted.

## Context

ADR 0018 accepted a TypeScript mirror of the launch contract because no server-side Python bridge existed at the time. Two implementations of the same rules — `task_launch.py` and `web/src/lib/taskLaunch.ts` — were pinned to each other by `config/task_launch.cases.json`, and every contract change required two implementations, two suites and a fixture to prevent drift. Meanwhile `/api/tasks` forwarded a client-built payload unexamined, so task admission was a conduit rather than a boundary.

## Decision

1. **One owner.** `boss_agent.task_launch` builds and validates every launch, including reruns (`rebuild_rerun_launch`). It is the sole writer of `target_action` and the sole validator that refuses a stated mode on a search or a half-stated legacy depth pair.
2. **Intent at the boundary.** `POST /api/tasks` accepts `{kind, source, search|task, mode}` and delegates construction through `scripts/launch_contract.py`. `POST /api/tasks/:id/rerun` accepts only a task id and rebuilds through the same owner, so an old payload shape cannot be resurrected. A contract refusal is a 4xx.
3. **Mirror retired.** `web/src/lib/taskLaunch.ts` and its vitest suites are deleted; no Web code constructs `task_type` or a launch payload. `config/task_launch.cases.json` remains the behavioural fixture, asserted only by the Python suite. ADR 0018 carries the amendment.

## Consequences

- A contract change touches one module and one suite; there is no second language to update.
- `/api/tasks` is a real admission boundary: an invalid intent cannot become a queued task.
- The same strategy produces the same payload from every entry (modal, searches page, scheduled-run button, rerun).
- The Web layer depends on a Python process for task creation; a missing interpreter surfaces as a readable error rather than as a stale second implementation.
