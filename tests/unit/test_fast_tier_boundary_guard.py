"""
tests/unit/test_fast_tier_boundary_guard.py
===========================================
Guard tests asserting the Fast Unit tier boundary contract (Spec #303, ticket #306).

Asserts that fast unit tests cannot spawn subprocesses or bind ports, preserving the
in-memory, side-effect-free nature of the tier. There is deliberately no exemption — not
even for a collection-only pytest run: the tier-selection suite that needs subprocesses
lives in `tests/e2e`, so any subprocess here is a violation.
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


def test_guard_rejects_collection_only_subprocesses_too():
    """No exemption survives: a `--collect-only` run is still a subprocess, and belongs to e2e."""
    with pytest.raises(RuntimeError) as exc_info:
        subprocess.Popen([sys.executable, "-m", "pytest", "--collect-only", "-q"])
    assert "subprocess execution is forbidden" in str(exc_info.value)


def test_guard_rejects_socket_port_binding():
    with pytest.raises(RuntimeError) as exc_info:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
    assert "Fast Unit tier boundary violation" in str(exc_info.value)
    assert "socket binding is forbidden" in str(exc_info.value)
