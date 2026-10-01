"""
tests/unit/test_typed_persistence_guard.py
==========================================
Guard test preventing silent failure absorption across persistence and broker seams
(Spec #303 / #308, ADR 0013).

Fails if any repository store, broker adapter, or provisioner seam has an ``except`` handler
that absorbs a failure into an empty sentinel (``None``, ``[]``, ``{}``, ``False``, ``0``)
without re-raising.

The defect class is *"a failure absorbed into an empty value"*, not *"the handler was spelled
``except Exception``"*. A typed ``except ValidationError: return {}`` is the same bug with
better branding — it once let a rejected job-record write pass for a saved one, under-counting
the daily greeting quota and the direct-hire exclusion pool. So the guard no longer looks at
the *type* the handler catches; it looks at whether the handler swallows.

An intentional degradation — a cache read that legitimately yields "nothing cached" — is
allowed, but only when it says so out loud with a ``# persistence-guard: allow`` marker on the
handler, so the exception is a recorded decision rather than an accident.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

SEAM_FILES = [
    REPO_ROOT / "src/boss_agent/job_store.py",
    REPO_ROOT / "src/boss_agent/candidate_memory_store.py",
    REPO_ROOT / "src/boss_agent/saved_search_store.py",
    REPO_ROOT / "src/boss_agent/broker/pocketbase_adapter.py",
    REPO_ROOT / "src/boss_agent/broker/provisioner.py",
]

# Put this on an `except` handler that deliberately degrades to an empty value instead of
# re-raising, so the intent is recorded and the guard stays meaningful for new handlers.
ALLOW_MARKER = "# persistence-guard: allow"


def is_empty_return(node: ast.AST) -> bool:
    """Check if a node is a return statement returning None, empty list/dict/set, False, or 0."""
    if not isinstance(node, ast.Return):
        return False
    if node.value is None:
        return True
    if isinstance(node.value, ast.Constant) and node.value.value in (None, False, 0):
        return True
    if isinstance(node.value, ast.List) and len(node.value.elts) == 0:
        return True
    if isinstance(node.value, ast.Dict) and len(node.value.keys) == 0:
        return True
    if isinstance(node.value, ast.Set) and len(node.value.elts) == 0:
        return True
    # ``return dict()`` is the same empty sentinel spelled differently.
    value = node.value
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in ("dict", "list", "set")
        and not value.args
    )


def find_swallowed_empty_returns(tree: ast.AST, file_path: str = "", source: str = "") -> list[str]:
    """Find ``except`` handlers that return an empty sentinel without re-raising.

    Every handler is examined, whatever it catches: the swallowed-value defect does not
    care about the handler's spelling. A handler is exempt only when its source carries
    the ``ALLOW_MARKER``.
    """
    lines = source.splitlines()
    violations: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue

        # A handler that re-raises somewhere is propagating, not swallowing.
        if any(isinstance(stmt, ast.Raise) for stmt in ast.walk(node)):
            continue

        empty_returns = [stmt for stmt in ast.walk(node) if is_empty_return(stmt)]
        if not empty_returns:
            continue

        span = lines[node.lineno - 1 : (node.end_lineno or node.lineno)]
        if any(ALLOW_MARKER in line for line in span):
            continue

        caught = ast.unparse(node.type) if node.type is not None else "bare except"
        return_exprs = [ast.unparse(r) for r in empty_returns]
        violations.append(
            f"{file_path}:{node.lineno}: `except {caught}` absorbs failure into {return_exprs} "
            f"without re-raising; add `{ALLOW_MARKER}` if this degradation is intentional"
        )

    return violations


def _check(code: str) -> list[str]:
    return find_swallowed_empty_returns(ast.parse(code), "synthetic.py", code)


def test_guard_catches_broad_swallow_patterns():
    """A broad handler that returns an empty sentinel is flagged."""
    bad_code_none = """
def get_thing():
    try:
        do_io()
    except Exception as e:
        logger.warning(e)
        return None
"""
    violations = _check(bad_code_none)
    assert len(violations) == 1
    assert "returning ['return None']" in violations[0] or "into ['return None']" in violations[0]

    bad_code_list = """
def list_things():
    try:
        do_io()
    except:
        return []
"""
    assert len(_check(bad_code_list)) == 1

    bad_code_false = """
def delete_thing():
    try:
        do_io()
    except (Exception,):
        return False
"""
    assert len(_check(bad_code_false)) == 1


def test_guard_catches_typed_swallow_patterns():
    """A *typed* handler that returns an empty sentinel is the same bug, and is flagged too.

    This is the regression Spec #303 exists to remove: ``except ValidationError: return {}``
    in ``upsert_job_record`` let a rejected write pass for a saved record.
    """
    typed_swallow = """
async def upsert(record):
    try:
        return await self._write(record)
    except ValidationError:
        return {}
"""
    violations = _check(typed_swallow)
    assert len(violations) == 1
    assert "ValidationError" in violations[0]

    typed_none = """
def parse_date(val):
    try:
        return datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return None
"""
    assert len(_check(typed_none)) == 1


def test_guard_permits_reraising_and_marked_degradation():
    """Re-raising handlers pass; an intentional degradation passes only with the marker."""
    typed_raise = """
def get_thing():
    try:
        do_io()
    except Exception as e:
        raise TransportError(str(e)) from e
"""
    assert _check(typed_raise) == []

    marked = """
def read_cache(path):
    try:
        return load(path)
    except (json.JSONDecodeError, OSError):  # persistence-guard: allow
        return None
"""
    assert _check(marked) == []


def test_repository_and_provisioner_seams_have_no_swallowed_empty_returns():
    """No repository store, broker adapter, or provisioner seam may swallow a failure away."""
    all_violations: list[str] = []

    for file_path in SEAM_FILES:
        assert file_path.exists(), f"Expected seam file to exist: {file_path}"
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        violations = find_swallowed_empty_returns(
            tree, str(file_path.relative_to(REPO_ROOT)), source
        )
        all_violations.extend(violations)

    assert not all_violations, (
        "Found failure-absorbing handlers in repository/broker seams:\n" + "\n".join(all_violations)
    )
