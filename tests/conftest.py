"""
tests.conftest
==============
Suite-wide guards.

The screening blacklist is persisted by line-oriented surgery on a real config
file, and one of the appenders resolves its own target when the caller passes no
path. That default lands in the checkout, so the guard below makes it impossible
for any test -- present or future -- to edit a developer's screening config.
"""

import pytest


@pytest.fixture(autouse=True)
def _sandbox_screening_config_writes(tmp_path_factory, monkeypatch):
    """Redirect the *default* screening-config write target into the test sandbox.

    ``resolve_writable_screening_config_path()`` with no root resolves through
    ``resolve_git_common_root()``, which in a worktree is the shared *main* checkout
    rather than the worktree the tests run in. A test that forgets to pass a path
    would then rewrite the developer's real ``config/settings.local.yaml``.

    Only the fallback is redirected. Explicit ``root=``/``path=`` callers keep their
    real behaviour, and ``resolve_git_common_root`` itself is left untouched so
    ``test_settings.py`` and ``test_pb_runner_lifecycle.py`` still assert against the
    genuine resolver.
    """
    from boss_agent import screening_config

    real_resolver = screening_config.resolve_writable_screening_config_path
    sandbox = tmp_path_factory.mktemp("screening-config") / "settings.local.yaml"

    def guarded(root=None):
        return real_resolver(root) if root is not None else sandbox

    monkeypatch.setattr(screening_config, "resolve_writable_screening_config_path", guarded)
