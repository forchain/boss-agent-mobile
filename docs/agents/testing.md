# Test Guidelines

Three tiers, one contract: iterate on the file you are changing, verify the fast unit tier
before you call the work done, and opt into the tiers that touch real infrastructure.

| Tier | Where | Command | May touch |
| --- | --- | --- | --- |
| **Fast Unit Test** | `tests/unit/` | `uv run --extra dev pytest` | In-memory only: no daemon, no device, no fixed port, no live LLM call |
| **Service Integration / E2E Test** | `tests/e2e/`, `@pytest.mark.e2e` | `uv run --extra dev pytest tests/e2e` | Real services on ephemeral ports, state under `tmp_path` |
| **Live Device Test** | `tests/e2e/`, `@pytest.mark.live` | `uv run --extra dev pytest tests/e2e -m live` | The shared AVD + Appium session |

## Stage 1 — iterative development: run the one file you are changing

```bash
uv run --extra dev pytest tests/unit/test_job_matching.py
uv run --extra dev pytest tests/unit/test_job_matching.py -k cooldown
```

Do **not** run the whole suite on every edit. A full pass costs minutes and tells you
nothing about the file you just touched. Run the targeted file, then go back to editing.

## Stage 2 — before you finish: the fast unit tier

```bash
uv run --extra dev pytest        # == pytest tests/unit
```

This is the pre-completion gate for every change, human or agent. It collects
`tests/unit` only, finishes in tens of seconds rather than minutes, and has zero side
effects on the machine: no processes are signalled, no ports are bound, no shared runtime
state is written, and no live LLM call is made. Use
`uv run --extra dev pytest --collect-only -q` when you want to see exactly what a run
would execute.

The tier stays that fast because `tests/unit/conftest.py` removes the waiting that a mocked
driver can never resolve: the sleep between UI polls and the humanized pauses between
gestures. Timeout semantics, jitter, and every assertion are untouched — and a new test that
somehow depends on real pacing will say so loudly rather than quietly costing seconds.

## Stage 3 — end-to-end, only when the change is end-to-end

```bash
uv run --extra dev pytest tests/e2e          # the E2E tier (device tests stay out)
uv run --extra dev pytest tests/e2e -m live  # explicit opt-in: drives the real AVD
```

Run Stage 3 when you changed E2E workflows, service lifecycle, or the fixtures themselves —
not as a routine "is everything green" sweep. It needs the shared PocketBase State Stream
Broker for the dashboard suites, and it starts real servers, so it is minutes, not seconds.

Both tiers stay isolated from concurrent work:

- The dashboard E2E test allocates an ephemeral port and logs into its own `tmp_path`, so a
  Web Dashboard you (or another worktree) already run on port 5173 is neither collided with
  nor shut down.
- Residual Worker / Web Dashboard teardown is **opt-in**:

  ```bash
  BOSS_AGENT_ENFORCE_TEARDOWN=1 uv run --extra dev pytest tests/e2e
  ```

  Only then does the session gate stop the services recorded in `.boss_agent/worker.pid` /
  `web.pid` (the Automation Worker holds the Virtual Device Session, the dashboard holds
  port 5173) and verify each one logged its shutdown. Shared infrastructure — PocketBase,
  Appium, the AVD — is never signalled. Services stay stopped afterwards: relaunch with
  `./run.sh worker` and `./run.sh web`.

## How the tiers are enforced

`pyproject.toml` carries the default: `testpaths = ["tests/unit"]` and
`addopts = "-m 'not live and not e2e'"`. An unadorned run therefore cannot reach a device
test or a service test, and neither can a broad path (`pytest tests` deselects both
markers). The root `conftest.py` keeps the deliberate invocations working on top of that:

- pointing at `tests/e2e` lifts only the `not e2e` clause — the `live` guard survives, so
  device tests still need `-m live`;
- a marker expression that *names* a tier (`pytest -m live`, `pytest -m e2e`) puts the whole
  `tests/` tree in scope, since that is a tier selection rather than a filter. A filter-only
  expression (`pytest -m "not live"`) keeps the default unit-tier scope, and any explicit
  path always wins over both rules — nothing widens a run you already narrowed.

Live device tests never carry the `e2e` marker, so no marker-based selection can reach the
AVD by accident.

`tests/unit/test_live_marker_isolation.py` asserts all of this against real
collection-only subprocesses — if you change the defaults, that suite is what tells you
what broke.

## Where does my new test go?

- **Everything mocked** (driver, broker, LLM client) → `tests/unit/`. It must stay
  memory-safe: no real daemon, no fixed host port, no live LLM endpoint. A handler built
  without a client calls the configured LLM — the screener fails open, so the only symptom is
  latency and a token bill — so pass a stub client (`llm_client=MagicMock()` with a
  `chat_completion_json` verdict). The tier-boundary suite `test_live_marker_isolation.py` is
  the one sanctioned exception to "no subprocesses": it spawns `--collect-only` pytest runs to
  verify the defaults, and collection executes nothing.
- **Needs a real service, process, or port** → `tests/e2e/` with `@pytest.mark.e2e`. Use
  `free_port()` / `tmp_path` from `tests/_service_harness.py`, never port 5173 or the shared
  `.boss_agent/` directory.
- **Drives the emulator or Appium** → `tests/e2e/` with `@pytest.mark.live`. An unmarked
  device test breaks the guarantee for everyone sharing the AVD.

## CI

`uv run --extra dev pytest` is the immediate PR check (tens of seconds, no infrastructure);
`uv run --extra dev pytest tests/e2e` belongs in a downstream job with PocketBase running.
