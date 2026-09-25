"""
tests/unit/test_run_sh_avd_gate.py
==================================
The Worker pre-flight AVD gate must fail fast (spec #241, ticket #242).

`run.sh` refuses to start the Automation Worker unless the dedicated AVD is online and
booted. That gate used to run its own unbounded adb queries, so a device stuck `offline`
froze `./run.sh` before the Worker ever started — and before any of its actionable
diagnostics could be printed.
"""

from __future__ import annotations

import http.server
import re
import socketserver
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from _runner_harness import STATUS_BUDGET_SEC, TARGET_AVD, RunnerScriptHarness

from _service_harness import REPO_ROOT, free_port

RUN_SH = REPO_ROOT / "run.sh"

# A direct adb query, as opposed to the word "adb" appearing in a hint or a script path.
_ADB_INVOCATION = re.compile(r"\badb\s+(devices|-s|shell|emu|connect|wait-for-device)")


class _AlwaysOkHandler(http.server.BaseHTTPRequestHandler):
    """Answers 200 to every probe, standing in for PocketBase and the Appium server."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args: object) -> None:
        """Silence the stub's request log."""


@pytest.fixture
def stub_services() -> Iterator[dict[str, str]]:
    """One reachable endpoint standing in for both the broker and Appium.

    The gate under test runs *after* those two health checks, so they must answer before
    `run.sh` can reach the AVD gate at all.
    """
    with socketserver.TCPServer(("127.0.0.1", free_port()), _AlwaysOkHandler) as server:
        url = f"http://127.0.0.1:{server.server_address[1]}"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield {"POCKETBASE_URL": url, "APPIUM_URL": url}
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.fixture
def harness(tmp_path: Path) -> RunnerScriptHarness:
    return RunnerScriptHarness(tmp_path)


def test_worker_gate_aborts_promptly_when_the_dedicated_avd_is_offline(
    harness: RunnerScriptHarness, stub_services: dict[str, str]
) -> None:
    """The offline device that used to freeze `./run.sh` must fail the gate in seconds."""
    harness.script(devices=[("emulator-5554", "offline")])

    result = harness.run_script("run.sh", budget=STATUS_BUDGET_SEC, env=stub_services)

    assert result.elapsed < STATUS_BUDGET_SEC, f"run.sh took {result.elapsed:.2f}s"
    assert result.returncode != 0
    assert "not running" in result.output.lower(), result.output
    assert "./emulator.sh" in result.output, "the failure must point at the recovery command"
    assert not (harness.runtime_root / ".boss_agent" / "worker.pid").exists(), (
        "a failed pre-flight gate must not launch the Worker daemon"
    )


def test_worker_gate_aborts_promptly_while_the_device_is_still_booting(
    harness: RunnerScriptHarness, stub_services: dict[str, str]
) -> None:
    harness.script(devices=[("emulator-5554", "device")], avd_name=TARGET_AVD, boot_completed="")

    result = harness.run_script("run.sh", budget=STATUS_BUDGET_SEC, env=stub_services)

    assert result.elapsed < STATUS_BUDGET_SEC, f"run.sh took {result.elapsed:.2f}s"
    assert result.returncode != 0
    assert "emulator-5554" in result.output
    assert "booting" in result.output.lower(), result.output
    assert not (harness.runtime_root / ".boss_agent" / "worker.pid").exists()


def test_run_sh_never_queries_adb_directly() -> None:
    """The gate must delegate to `emulator.sh status`, the single bounded AVD authority."""
    script = RUN_SH.read_text(encoding="utf-8")
    offenders = [
        f"  run.sh:{number}: {line.strip()}"
        for number, line in enumerate(script.splitlines(), start=1)
        if not line.strip().startswith("#") and _ADB_INVOCATION.search(line)
    ]
    assert not offenders, (
        "a direct adb query in run.sh is unbounded and can freeze the worker pre-flight "
        "gate:\n" + "\n".join(offenders)
    )
    assert "./emulator.sh status" in script, "run.sh must delegate the AVD gate to emulator.sh"
