"""
tests/e2e/test_doctor_delegation.py
===================================
`doctor.sh` delegates every service-readiness probe to a runner's `status` verb (#477).

The System Doctor used to re-implement what the runners already own: direct `lsof` port
probing (which, unlike the runners', was not LISTEN-only and could match an SSE client), a
hardcoded 5173, a bare `pgrep` and an unbounded ADB loop. Those copies had drifted weaker
than the runners they duplicated, and nothing could drive the doctor in a harness.

Two properties are pinned here, and neither needs a live service:

* **wiring** — every one of the five services is checked by running *its own* runner with
  `status`, and the doctor starts no probe of its own; and
* **configured ports** — the probes that do run follow the ports the operator configured,
  never the historical defaults, so a copy of the doctor cannot quietly re-hardcode them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from _service_harness import REPO_ROOT, free_port

pytestmark = pytest.mark.e2e

DOCTOR_SH = REPO_ROOT / "doctor.sh"
RUNNER_LIB = REPO_ROOT / "runner_lib.sh"
RUNNERS = ("worker.sh", "dashboard.sh", "pocketbase.sh", "appium.sh", "emulator.sh")
BASH = shutil.which("bash") or "/bin/bash"

#: The historical ports the runners' own defaults use. A configured port must replace
#: them: seeing one of these in a probe means the doctor (or a copy of it) hardcoded it.
LEGACY_PORTS = ("5173", "8090", "4723", "6555")


def _copy_runtime_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".boss_agent").mkdir(parents=True)
    for script in (DOCTOR_SH.name, RUNNER_LIB.name, *RUNNERS):
        shutil.copy2(REPO_ROOT / script, root / script)
        (root / script).chmod(0o755)
    return root


def _run_doctor(root: Path, *, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, "doctor.sh"],
        cwd=str(root),
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_every_service_is_checked_by_its_own_runner_status_verb(tmp_path: Path) -> None:
    """The wiring: five services, five `status` invocations, no probe of the doctor's own."""
    calls = tmp_path / "calls.log"
    root = _copy_runtime_root(tmp_path)
    for runner in RUNNERS:
        (root / runner).write_text(
            f'#!/usr/bin/env bash\necho "{runner} $*" >> "{calls}"\nexit 0\n',
            encoding="utf-8",
        )
        (root / runner).chmod(0o755)

    # An unreachable broker keeps the collection checks off the developer's live server;
    # it does not change which runner the doctor asks about readiness.
    _run_doctor(
        root,
        env={
            "POCKETBASE_URL": f"http://127.0.0.1:{free_port()}",
            "WEB_PORT": str(free_port()),
            "APPIUM_PORT": str(free_port()),
            "REMOTE_ADB_PORT": str(free_port()),
        },
    )

    recorded = calls.read_text(encoding="utf-8").splitlines()
    assert sorted(recorded) == sorted(f"{runner} status" for runner in RUNNERS), (
        "the System Doctor still probes a service itself instead of delegating its status"
    )

    source = DOCTOR_SH.read_text(encoding="utf-8")
    for probe in ("lsof", "pgrep", "adb "):
        assert probe not in source, (
            f"doctor.sh still carries its own `{probe}` probe; the runners own that rule"
        )


@pytest.fixture
def fake_toolchain(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A PATH whose tooling records every probe and answers "nothing is running".

    Only `lsof`, `ps`, `pgrep` and `adb` are faked: they are the probes whose argv carries
    a port or a configured pattern, which is what the port assertions read. Real shell
    tools (`find`, `tr`, `date`) stay available so the runners behave normally.
    """
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    tool_log = tmp_path / "tools.log"

    def _script(name: str, body: str) -> None:
        path = bin_dir / name
        path.write_text(
            f"#!/usr/bin/env bash\nprintf '%s %s\\n' '{name}' \"$*\" >> '{tool_log}'\n{body}",
            encoding="utf-8",
        )
        path.chmod(0o755)

    _script("lsof", "exit 1\n")
    _script("pgrep", "exit 1\n")
    _script("ps", "exit 1\n")
    _script("adb", "exit 0\n")
    for shadowed in ("curl", "node", "npm", "uv"):
        _script(shadowed, "exit 0\n" if shadowed != "curl" else "exit 22\n")

    sdk = tmp_path / "fake-android-sdk"
    for relative, body in (
        ("platform-tools/adb", "#!/usr/bin/env bash\nexit 0\n"),
        ("emulator/emulator", "#!/usr/bin/env bash\nexit 0\n"),
    ):
        path = sdk / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    ports = {name: free_port() for name in ("web", "appium", "pb", "bridge")}
    env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "HOME": str(tmp_path / "home"),
        "ANDROID_HOME": str(sdk),
        "POCKETBASE_URL": f"http://127.0.0.1:{ports['pb']}",
        "PB_HTTP": f"127.0.0.1:{ports['pb']}",
        "WEB_PORT": str(ports["web"]),
        "APPIUM_URL": f"http://127.0.0.1:{ports['appium']}",
        "APPIUM_PORT": str(ports["appium"]),
        "REMOTE_ADB_PORT": str(ports["bridge"]),
    }
    Path(env["HOME"]).mkdir()
    return tool_log, env


def test_probes_follow_the_configured_ports_and_never_the_legacy_defaults(
    tmp_path: Path, fake_toolchain
) -> None:
    """The doctor cannot re-acquire a hardcoded port behind the runners' backs (#477)."""
    tool_log, env = fake_toolchain
    root = _copy_runtime_root(tmp_path)

    result = _run_doctor(root, env=env)

    assert result.returncode == 1, "nothing is running, so the doctor must report failure"
    recorded = tool_log.read_text(encoding="utf-8")

    for port in (env["PB_HTTP"].split(":")[-1], env["WEB_PORT"], env["APPIUM_PORT"]):
        assert port in recorded, f"the configured port {port} was never probed:\n{recorded}"
    for legacy in LEGACY_PORTS:
        assert legacy not in recorded, (
            f"the doctor probed the legacy port {legacy} instead of the configured one:\n{recorded}"
        )
    assert "adb devices" in recorded, (
        "the AVD probe did not go through emulator.sh's bounded adb path"
    )
