"""
tests/unit/test_fast_tier_boundary_guard.py
===========================================
Guard tests asserting the Fast Unit tier boundary contract (spec #303, ticket #306).

Asserts that fast unit tests cannot spawn subprocesses or bind ports,
preserving the in-memory, side-effect-free nature of the tier,
with the sanctioned exception for collection-only invocations.
"""

import socket
import subprocess
import sys

import pytest


def test_guard_rejects_arbitrary_subprocess_execution():
    with pytest.raises(RuntimeError) as exc_info:
        subprocess.Popen([sys.executable, "-c", "print(1)"])
    assert "Fast Unit tier boundary violation" in str(exc_info.value)
    assert "subprocess execution is forbidden" in str(exc_info.value)


def test_guard_rejects_socket_port_binding():
    with pytest.raises(RuntimeError) as exc_info:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
    assert "Fast Unit tier boundary violation" in str(exc_info.value)
    assert "socket binding is forbidden" in str(exc_info.value)


def test_guard_permits_sanctioned_collection_only_subprocesses():
    """Collection-only pytest subprocesses are sanctioned (e.g. test_live_marker_isolation)."""
    import os
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:randomly",
            "tests/unit/test_greeting_prompt.py",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(repo_root),
        env=dict(os.environ),
    )
    stdout, stderr = proc.communicate(timeout=10)
    assert proc.returncode == 0
