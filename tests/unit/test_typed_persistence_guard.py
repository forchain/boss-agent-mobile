"""
tests/unit/test_typed_persistence_guard.py
==========================================
Guard test preventing broad-exception swallowing across persistence and broker seams
(Spec #303 / #308, ADR 0013).

Fails if any repository store, broker adapter, or provisioner seam reintroduces
a bare broad-exception handler (`except Exception:`, `except:`, `except BaseException:`)
that absorbs errors and returns an empty sentinel (`None`, `[]`, `{}`, `False`, `0`)
instead of propagating a typed failure (`TransportError`, `ValidationError`, etc.).
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


def is_broad_exception(node: ast.ExceptHandler) -> bool:
    """Whether an ExceptHandler catches Exception, BaseException, or is a bare except."""
    if node.type is None:
        return True
    if isinstance(node.type, ast.Name) and node.type.id in ("Exception", "BaseException"):
        return True
    if isinstance(node.type, ast.Tuple):
        return any(
            isinstance(elt, ast.Name) and elt.id in ("Exception", "BaseException")
            for elt in node.type.elts
        )
    return False


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
    return bool(isinstance(node.value, ast.Set) and len(node.value.elts) == 0)


def find_swallowed_broad_exceptions(tree: ast.AST, file_path: str = "") -> list[str]:
    """Find ExceptHandler blocks that catch broad exceptions and return empty values without raising."""
    violations: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue

        if not is_broad_exception(node):
            continue

        # Check if handler raises an exception
        has_raise = any(isinstance(stmt, ast.Raise) for stmt in ast.walk(node))
        if has_raise:
            continue

        # Check if handler contains an empty return
        empty_returns = [stmt for stmt in ast.walk(node) if is_empty_return(stmt)]
        if empty_returns:
            return_exprs = [ast.unparse(r) for r in empty_returns]
            violations.append(
                f"{file_path}:{node.lineno}: broad exception caught without re-raise, returning empty: {return_exprs}"
            )

    return violations


def test_guard_catches_synthetic_swallow_patterns():
    """Verify that the AST guard correctly flags broad-exception swallow patterns."""
    bad_code_none = """
def get_thing():
    try:
        do_io()
    except Exception as e:
        logger.warning(e)
        return None
"""
    violations = find_swallowed_broad_exceptions(ast.parse(bad_code_none), "synthetic.py")
    assert len(violations) == 1
    assert "returning empty: ['return None']" in violations[0]

    bad_code_list = """
def list_things():
    try:
        do_io()
    except:
        return []
"""
    violations = find_swallowed_broad_exceptions(ast.parse(bad_code_list), "synthetic.py")
    assert len(violations) == 1
    assert "returning empty: ['return []']" in violations[0]

    bad_code_false = """
def delete_thing():
    try:
        do_io()
    except (Exception,):
        return False
"""
    violations = find_swallowed_broad_exceptions(ast.parse(bad_code_false), "synthetic.py")
    assert len(violations) == 1
    assert "returning empty: ['return False']" in violations[0]


def test_guard_permits_proper_typed_and_reraising_patterns():
    """Verify that specific exceptions and re-raising handlers are permitted."""
    good_typed_raise = """
def get_thing():
    try:
        do_io()
    except Exception as e:
        raise TransportError(str(e)) from e
"""
    assert find_swallowed_broad_exceptions(ast.parse(good_typed_raise)) == []

    good_specific_catch = """
def parse_date(val):
    try:
        return datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return None
"""
    assert find_swallowed_broad_exceptions(ast.parse(good_specific_catch)) == []


def test_repository_and_provisioner_seams_have_no_broad_swallows():
    """Ensure no repository store, broker adapter, or provisioner seam swallows broad exceptions."""
    all_violations: list[str] = []

    for file_path in SEAM_FILES:
        assert file_path.exists(), f"Expected seam file to exist: {file_path}"
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
        violations = find_swallowed_broad_exceptions(tree, str(file_path.relative_to(REPO_ROOT)))
        all_violations.extend(violations)

    assert not all_violations, (
        "Found broad-exception swallow patterns in repository/broker seams:\n"
        + "\n".join(all_violations)
    )
