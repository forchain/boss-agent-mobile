"""
tests/unit/test_candidate_entities_isolation.py
==============================================
Runtime architectural isolation gate for the candidate entity (Issue #386, Spec #382).

`candidate_entities` is a leaf: the class can be built, serialized and rendered with
nothing but the standard library. ``tests/unit/test_candidate_entities.py`` already
proves that by reading the module's own AST and walking its transitive first-party
imports. That proof has a hole: it never executes the package ``__init__``. And
importing *any* submodule of a package executes that ``__init__`` first, so
``import boss_agent.candidate_entities`` was enough to pull in ``memory`` ->
``langsmith`` / ``rich`` / ``droid_agent_core.llm`` and roughly 1300 modules with
it. The entity was pure; the act of importing it was not.

So this module tests the *runtime import path*, which is the seam that actually
leaked. The assertion is behavioural -- "does reaching for the profile drag in an
LLM client, an HTTP stack and a terminal renderer?" -- and it is written against
public imports rather than against the shape of ``__init__.py``.

The fast tier forbids spawning a process (``tests/unit/conftest.py``), so a fresh
interpreter is not available. Two consequences shape the harness below:

* a ``sys.meta_path`` spy is installed at position 0 to observe import attempts;
* the modules under test and the forbidden roots are evicted from ``sys.modules``
  first, because a cached entry short-circuits the finder and would hide the very
  import being hunted.

The spy records and then *declines* (``find_spec`` returns ``None``), so the real
import machinery runs and a forbidden import genuinely repopulates ``sys.modules``.
That keeps the assertion literal -- "nothing landed" -- instead of trusting a stub to
stand in for the real thing. Substituting empty stub modules was the obvious cheaper
route and it is wrong: ``matching.py`` calls ``Console(stderr=True)`` at module
level, so a stub turns an honest finding into ``TypeError: 'module' object is not
callable``. ``sys.modules`` and the ``droid_agent_core.llm`` parent binding are both
snapshotted, so the rest of the session keeps the exact objects it started with.
"""

from __future__ import annotations

import contextlib
import importlib
import sys
from collections.abc import Iterator, Sequence
from importlib.abc import MetaPathFinder
from types import ModuleType

import pytest

#: Third-party packages the candidate entity must never drag in. ``droid_agent_core.llm``
#: is listed as a dotted root on purpose: the bare ``droid_agent_core`` package is a
#: legitimate dependency of the UI tier, the LLM client inside it is not.
FORBIDDEN_ROOTS = (
    "droid_agent_core.llm",
    "httpx",
    "langsmith",
    "requests",
    "rich",
)

#: Both spellings of the entity. The facade re-exports it, and the facade is reached
#: through the same package ``__init__``, so both must be covered.
ENTITY_MODULES = ("boss_agent.candidate_entities", "boss_agent.entities")

_MISSING = object()


class _ImportSpy(MetaPathFinder):
    """Record every import of a forbidden root, then let it happen for real.

    Returning ``None`` keeps the spy a pure observer: the import proceeds through the
    normal finders, which is what makes the ``sys.modules`` assertion meaningful.
    """

    def __init__(self, forbidden: Sequence[str]) -> None:
        self._forbidden = tuple(forbidden)
        self.attempts: list[str] = []

    def forbidden_root(self, fullname: str) -> str | None:
        return next(
            (
                root
                for root in self._forbidden
                if fullname == root or fullname.startswith(f"{root}.")
            ),
            None,
        )

    def find_spec(self, fullname, path=None, target=None) -> None:
        if self.forbidden_root(fullname) is not None:
            self.attempts.append(fullname)
        return None

    @property
    def touched_roots(self) -> list[str]:
        """The forbidden packages that were reached, deduplicated.

        Reporting roots rather than all 200+ submodule names keeps a failure readable;
        ``attempts`` still carries the full list for anyone who wants the detail.
        """
        return sorted({self.forbidden_root(name) for name in self.attempts} - {None})


@contextlib.contextmanager
def _fresh_package_import(
    spy: _ImportSpy, module_name: str
) -> Iterator[tuple[ModuleType, str | None]]:
    """Import ``module_name`` with no cached ``boss_agent`` and no real heavy modules.

    Yields the module and any exception the import raised. Everything is restored on
    the way out, so the rest of the session keeps the exact module objects it had.
    """
    saved_modules = dict(sys.modules)
    parent_attrs = _snapshot_parent_attrs(spy._forbidden)
    try:
        for key in list(sys.modules):
            if key == "boss_agent" or key.startswith("boss_agent.") or spy.forbidden_root(key):
                del sys.modules[key]

        sys.meta_path.insert(0, spy)
        module: ModuleType | None = None
        failure: str | None = None
        try:
            module = importlib.import_module(module_name)
        except BaseException as exc:  # noqa: BLE001 - the failure is the assertion
            failure = f"{type(exc).__name__}: {exc}"

        landed = sorted(root for root in spy._forbidden if root in sys.modules)
        yield module, failure, landed
    finally:
        with contextlib.suppress(ValueError):
            sys.meta_path.remove(spy)
        sys.modules.clear()
        sys.modules.update(saved_modules)
        for parent, child, value in parent_attrs:
            if value is _MISSING:
                with contextlib.suppress(AttributeError):
                    delattr(parent, child)
            else:
                setattr(parent, child, value)


def _snapshot_parent_attrs(names: Sequence[str]) -> list[tuple[ModuleType, str, object]]:
    """Remember the ``pkg.submodule`` binding, which loading a stub would overwrite."""
    saved: list[tuple[ModuleType, str, object]] = []
    for name in names:
        parent_name, _, child = name.rpartition(".")
        if not parent_name:
            continue
        parent = sys.modules.get(parent_name)
        if parent is not None:
            saved.append((parent, child, getattr(parent, child, _MISSING)))
    return saved


@pytest.mark.parametrize("module_name", ENTITY_MODULES)
def test_reaching_for_the_profile_does_not_import_an_llm_or_network_stack(module_name: str) -> None:
    """AC#2: importing the entity imports no LangSmith, LLM client, HTTP or renderer."""
    spy = _ImportSpy(FORBIDDEN_ROOTS)
    with _fresh_package_import(spy, module_name) as (module, failure, landed):
        assert failure is None, f"importing {module_name} failed: {failure}"
        assert module is not None
        assert spy.attempts == [], (
            f"{module_name} imported {len(spy.attempts)} modules under {spy.touched_roots}"
        )
        assert landed == [], f"{module_name} left {landed} in sys.modules"


def test_the_spy_would_actually_catch_a_forbidden_import() -> None:
    """Guard the guard: a spy that never fires would make the test above vacuous.

    ``memory`` is exactly the eager import this ticket removes, so if the spy cannot
    see it, the assertion proves nothing.
    """
    spy = _ImportSpy(FORBIDDEN_ROOTS)
    with _fresh_package_import(spy, "boss_agent.memory") as (_, failure, _):
        assert failure is None, f"importing boss_agent.memory failed: {failure}"

    assert spy.attempts, "the spy observed no forbidden import while importing boss_agent.memory"


def test_the_isolation_harness_restores_sys_modules() -> None:
    """The harness is itself code under test: it must leave no residue behind."""
    before = dict(sys.modules)
    spy = _ImportSpy(FORBIDDEN_ROOTS)
    with _fresh_package_import(spy, "boss_agent.candidate_entities") as (_, _, _):
        pass

    assert dict(sys.modules).keys() == before.keys()
    assert spy not in sys.meta_path


def test_every_exported_symbol_is_still_reachable_as_an_attribute() -> None:
    """Lazy resolution must not cost the package any of its public surface."""
    import boss_agent

    for name in boss_agent.__all__:
        assert getattr(boss_agent, name) is not None, f"boss_agent.{name} no longer resolves"


def test_dir_lists_the_lazy_names() -> None:
    """``dir()`` is how a REPL and ``help()`` discover a lazy package's surface."""
    import boss_agent

    listed = set(dir(boss_agent))
    missing = sorted(set(boss_agent.__all__) - listed)
    assert missing == []


def test_an_unknown_name_raises_attribute_error() -> None:
    """PEP 562 requires ``AttributeError`` for names the package does not define."""
    import boss_agent

    with pytest.raises(AttributeError):
        boss_agent.definitely_not_an_exported_symbol  # noqa: B018 - the access is the assertion


def test_a_symbol_whose_module_cannot_import_raises_attribute_error() -> None:
    """Optional dependencies stay tolerated.

    The eager ``__init__`` wrapped most imports in ``contextlib.suppress(ImportError)``,
    so a missing dependency skipped one name and left the rest usable. Lazy loading has
    to keep that: the failure has to surface as a clean ``AttributeError`` on the one
    symbol, not as an ``ImportError`` that takes unrelated exports down with it.
    """

    class _BlockMemory(MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None) -> None:
            if fullname == "boss_agent.memory":
                raise ModuleNotFoundError(f"No module named {fullname!r}", name=fullname)
            return None

    blocker = _BlockMemory()
    spy = _ImportSpy(FORBIDDEN_ROOTS)
    with _fresh_package_import(spy, "boss_agent") as (boss_agent, failure, _):
        assert failure is None, f"importing boss_agent failed: {failure}"
        assert boss_agent is not None

        sys.meta_path.insert(0, blocker)
        try:
            with pytest.raises(AttributeError):
                boss_agent.StructuredCandidateProfile  # noqa: B018 - the access is the assertion

            # An unrelated export from an untouched module still resolves.
            assert boss_agent.CandidateProfile is not None
            assert boss_agent.JobPosting is not None
        finally:
            with contextlib.suppress(ValueError):
                sys.meta_path.remove(blocker)
