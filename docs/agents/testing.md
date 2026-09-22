# Test Guidelines

## Default isolation: `live` tests never run by accident

The test suite shares one Android Virtual Device, one Appium server, and one State Stream
Broker with every other worktree and coding agent on this machine. A stray device test
therefore steals the emulator from whoever is using it.

`pyproject.toml` sets `addopts = "-m 'not live'"`, so **any unadorned `pytest` invocation
deselects `@pytest.mark.live` tests**:

```bash
uv run --extra dev pytest          # unit + integration only — never touches the device
uv run --extra dev pytest -m live  # explicit opt-in: drives the real AVD
```

Device work belongs behind the `live` marker. If you add a test that boots the app, opens
Appium sessions, or otherwise wakes the emulator, mark it — an unmarked device test breaks
the guarantee for everyone working in parallel.

## Graceful shutdown and the E2E teardown gate

The Automation Worker (holds the Virtual Device Session) and the Web Dashboard (holds port
5173) must not be running while end-to-end tests execute. `tests/e2e/conftest.py` installs a
session-scoped autouse fixture that, before the first E2E test:

1. reads `.boss_agent/worker.pid` and `.boss_agent/web.pid` (and the port-5173 listener),
2. gracefully stops whatever it finds there,
3. verifies the service actually recorded its shutdown in `worker.log` / `web.log`, and
4. leaves it stopped — nothing is relaunched.

Shared infrastructure (PocketBase, Appium, the AVD) is deliberately out of scope and is
never signalled. Services that cannot confirm their shutdown (for example a Worker started
before the graceful-shutdown support landed) are force-killed and the run aborts with an
actionable message: restart the service from this worktree and re-run.

To bypass the gate deliberately — e.g. you want to keep your dashboard up while running a
read-only E2E check:

```bash
BOSS_AGENT_SKIP_SERVICE_GATE=1 uv run --extra dev pytest tests/e2e
```

Related lifecycle commands:

```bash
./run.sh stop          # SIGTERM the Automation Worker (it releases the Appium session)
./run.sh web stop      # stop the Web Dashboard, verifying port 5173 is released
```
