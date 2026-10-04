"""
tests/unit/test_emulator_reconnect_selfheal.py
==============================================
Connection self-healing for the dedicated AVD (ticket #367).

The failure this covers is the one that looks healthy from the console: the AVD is booted
and `emulator.sh status` says ONLINE, but the Remote ADB Bridge has died, so every remote
client (a second adb server on the LAN, QtScrcpy, a phone on Wi-Fi) cannot get in. The
operator's only route back was `stop` + `start`, which takes the AVD down and gives up the
run -- when everything needed is a daemon restart and an `adb connect`.

So `reconnect` has one job: bring the LAN path back **without touching a running AVD**, and
refuse to pretend otherwise when there is nothing to reconnect to. The last part matters as
much as the first -- a recovery command that half-starts a bridge against an AVD that is not
there leaves the next `start` fighting a process it did not ask for.

Same harness as the other runner suites: the real `emulator.sh` in a throwaway runtime root,
driven by a scripted fake `adb` and a fake bridge daemon, so no shared AVD is touched and no
`live` marker is needed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from _runner_harness import FAKE_LAN_IP, TARGET_AVD, RunnerScriptHarness

from _service_harness import REPO_ROOT

NATIVE_SERIAL = "emulator-5554"
DOCTOR_SH = REPO_ROOT / "doctor.sh"


@pytest.fixture
def runner(tmp_path: Path) -> RunnerScriptHarness:
    harness = RunnerScriptHarness(tmp_path)
    yield harness
    harness.shutdown()


def lan_serial(runner: RunnerScriptHarness) -> str:
    """The transport the Remote ADB Bridge publishes for this scenario."""
    return f"{FAKE_LAN_IP}:{runner.bridge_port}"


def avd_up(runner: RunnerScriptHarness) -> None:
    """A booted AVD with no bridge running: the exact state #367 was filed about."""
    runner.script(devices=[(NATIVE_SERIAL, "device")], avd_name=TARGET_AVD, boot_completed="1")


def assert_all_green(runner: RunnerScriptHarness) -> None:
    """`status` reports a fully usable AVD: online, bridged, reachable over the LAN."""
    result = runner.run("status")
    assert result.returncode == 0, f"still unhealthy:\n{result.output}"
    assert "ONLINE and READY" in result.stdout, result.stdout
    assert "Remote ADB Bridge is LISTENING" in result.stdout, result.stdout
    assert "LAN ADB Connection is CONNECTED" in result.stdout, result.stdout


# ---------------------------------------------------------------------------
# The headline behaviour: heal the LAN path, keep the AVD
# ---------------------------------------------------------------------------
def test_reconnect_restores_a_dead_bridge_for_a_live_avd(runner: RunnerScriptHarness):
    """AC: AVD online + bridge offline -> `reconnect` returns the whole path to green."""
    avd_up(runner)
    assert not runner.port_is_bound(), "the scenario is supposed to start with no bridge"

    result = runner.run("reconnect")

    assert result.returncode == 0, f"reconnect failed:\n{result.output}"
    assert runner.port_is_bound(), "reconnect did not bring the bridge up"
    assert f"connect {lan_serial(runner)}" in runner.calls(), runner.calls()
    assert_all_green(runner)


def test_reconnect_recovers_when_the_bridge_is_up_but_the_lan_transport_dropped(
    runner: RunnerScriptHarness,
):
    """The other half of the outage the ticket names: bridge alive, LAN connection gone.

    This is the case a port check cannot see. The bridge is listening and holding its port, so
    anything watching the process or the socket calls the box healthy, while every remote
    client fails to connect. The verdict has to be taken from adb's own device list.
    """
    avd_up(runner)
    runner.start_bridge()

    result = runner.run("reconnect")

    assert result.returncode == 0, f"reconnect failed:\n{result.output}"
    assert f"connect {lan_serial(runner)}" in runner.calls(), runner.calls()
    assert_all_green(runner)


def test_reconnect_never_restarts_the_avd(runner: RunnerScriptHarness):
    """The whole point of the command: repair the network path, not the AVD.

    A `stop`/`start` cycle would satisfy "it connects again" while destroying the run, so the
    absence of a kill and a relaunch is the discriminating assertion here.
    """
    avd_up(runner)

    runner.run("reconnect")

    calls = runner.calls()
    assert "emu kill" not in calls, f"reconnect shut the AVD down:\n{calls}"
    assert "emulator: launched" not in calls, f"reconnect relaunched the AVD:\n{calls}"


def test_reconnect_is_idempotent(runner: RunnerScriptHarness):
    """AC: idempotent. A second run must not pile up bridges or claim to have fixed more.

    Recovery commands get run twice by impatient operators and by automation; the second run
    has to be a no-op that says so.
    """
    avd_up(runner)

    first = runner.run("reconnect")
    assert first.returncode == 0, f"first reconnect failed:\n{first.output}"
    pid_after_first = runner.bridge_pid()

    second = runner.run("reconnect")

    assert second.returncode == 0, f"second reconnect failed:\n{second.output}"
    assert runner.bridge_pid() == pid_after_first, (
        "the second reconnect replaced the bridge instead of leaving it alone"
    )
    assert runner.calls().count("connect ") == 1, (
        f"a healthy path was reconnected again:\n{runner.calls()}"
    )
    assert_all_green(runner)


# ---------------------------------------------------------------------------
# Refuse, cleanly, when there is nothing to reconnect to
# ---------------------------------------------------------------------------
def test_reconnect_reports_clearly_when_the_avd_is_still_booting(
    runner: RunnerScriptHarness,
):
    """AC: AVD not yet booted -> clear guidance, and no leftover process."""
    runner.script(devices=[(NATIVE_SERIAL, "device")], avd_name=TARGET_AVD, boot_completed="")

    result = runner.run("reconnect")

    assert result.returncode != 0, "a half-booted AVD was reported as repaired"
    assert "BOOTING" in result.stdout, result.stdout
    assert "emulator.sh" in result.stdout, f"no guidance was offered:\n{result.stdout}"
    # A bridge started against an AVD that is not listening yet serves nothing and becomes
    # the process the next `start` has to fight.
    assert not runner.port_is_bound(), "reconnect left a bridge running against a dead AVD"
    pid_file, ready_file = runner.bridge_files()
    assert not pid_file.exists() and not ready_file.exists(), (
        "reconnect left bridge state files behind"
    )


def test_reconnect_reports_clearly_when_the_avd_is_not_running(
    runner: RunnerScriptHarness,
):
    """Nothing to attach to means "start it", not a half-repaired network path."""
    runner.script(devices=[])

    result = runner.run("reconnect")

    assert result.returncode != 0
    assert "NOT RUNNING" in result.stdout, result.stdout
    assert "emulator.sh start" in result.stdout, f"no guidance was offered:\n{result.stdout}"
    assert not runner.port_is_bound(), "reconnect started a bridge with no AVD to serve"
    assert "connect " not in runner.calls(), runner.calls()


# ---------------------------------------------------------------------------
# `status` tells you about the self-heal, and can perform it
# ---------------------------------------------------------------------------
def test_status_points_at_reconnect_when_the_lan_path_is_down(runner: RunnerScriptHarness):
    """AC: the diagnostic names the recovery command, so the fix is discoverable."""
    avd_up(runner)

    result = runner.run("status")

    assert result.returncode == 0, f"status failed:\n{result.output}"
    assert "emulator.sh reconnect" in result.stdout, (
        f"status reported a broken LAN path without naming the fix:\n{result.stdout}"
    )


def test_status_offers_a_flag_and_heals_when_given_it(runner: RunnerScriptHarness):
    """AC: `status --fix` performs the repair instead of only describing it."""
    avd_up(runner)

    result = runner.run("status", "--fix")

    assert result.returncode == 0, f"status --fix failed:\n{result.output}"
    assert runner.port_is_bound(), "status --fix did not bring the bridge up"
    assert_all_green(runner)


def test_status_does_not_offer_a_repair_that_cannot_work(
    runner: RunnerScriptHarness,
):
    """A diagnostic must not point at a repair that can only fail.

    On a host with no non-loopback address there is nowhere to publish the bridge, so
    `reconnect` has nothing to do and exits non-zero. A `status` that still advertised it
    would send the operator through a command whose only possible outcome is an error — the
    one thing a health check exists to prevent.
    """
    avd_up(runner)

    result = runner.run("status", env={"HOST_LAN_IP": "127.0.0.1"})

    assert result.returncode == 0, f"status failed:\n{result.output}"
    assert "emulator.sh reconnect" not in result.stdout, (
        f"status recommends a repair this host cannot perform:\n{result.stdout}"
    )
    assert "No non-loopback LAN IP" in result.stdout, (
        f"status does not explain why LAN access is unavailable:\n{result.stdout}"
    )

    # And the command itself has to agree with that verdict.
    repair = runner.run("reconnect", env={"HOST_LAN_IP": "127.0.0.1"})
    assert repair.returncode != 0, "reconnect claimed success with nowhere to connect to"
    assert "No non-loopback LAN IP" in repair.stdout, repair.stdout


def test_plain_status_reports_but_never_repairs(runner: RunnerScriptHarness):
    """A diagnostic must stay read-only unless it is asked to act.

    `status` is what supervisors poll; a `status` that silently starts daemons turns a
    health check into a supervisor.
    """
    avd_up(runner)

    result = runner.run("status")

    assert result.returncode == 0
    assert not runner.port_is_bound(), "plain `status` started a bridge"
    assert "connect " not in runner.calls(), runner.calls()


# ---------------------------------------------------------------------------
# doctor.sh has to hand the operator the same lever
# ---------------------------------------------------------------------------
def test_doctor_is_syntactically_valid():
    """`doctor.sh` is a shell script; a syntax error there fails the operator's first move."""
    completed = subprocess.run(
        ["bash", "-n", str(DOCTOR_SH)], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr


def test_doctor_names_the_reconnect_command_for_a_dead_bridge():
    """AC: the health check must point at the recovery command, not just report a problem.

    `doctor.sh` resolves the AVD itself and has no way to be driven in this tier, so the
    contract is asserted structurally: the bridge warning has to quote the command that
    repairs it. A bare "bridge not running" leaves the operator guessing.
    """
    script = DOCTOR_SH.read_text(encoding="utf-8")

    assert "emulator.sh reconnect" in script, (
        "doctor.sh never mentions `./emulator.sh reconnect`, so the operator gets a warning "
        "with no lever attached"
    )
    # The guidance has to sit in the AVD/bridge section, not in some unrelated corner.
    bridge_line = next(
        (line for line in script.splitlines() if "emulator.sh reconnect" in line), ""
    )
    assert "log_warn" in bridge_line or "log_" in bridge_line, (
        f"the reconnect guidance is not attached to a doctor finding: {bridge_line!r}"
    )
