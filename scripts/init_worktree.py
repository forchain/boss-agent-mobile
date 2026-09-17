#!/usr/bin/env python3
"""
scripts/init_worktree.py
========================
Automated Git Worktree Initializer & Synchronizer for Boss Agent Mobile.

Features:
1. Operates on the current worktree by default (no required arguments), or a named worktree.
2. Locates the primary repository root across worktrees and clones.
3. Synchronizes local `main` branch with `origin/main` before creation/rebase.
4. Rebases current (or target) worktree branch onto the latest `main`.
5. Automatically discovers and symlinks untracked shared local configs (`*.local.*`, `.env`, memory files)
   into the worktree's `config/` directory without clobbering git-tracked assets.
6. Ergonomic CLI interface for humans (Rich output) and AFK Agents (`--json` output).

Usage:
  python3 scripts/init_worktree.py                        # Sync & rebase current worktree
  python3 scripts/init_worktree.py [name] [--branch <b>]  # Initialize/sync named worktree
  python3 scripts/init_worktree.py --no-rebase
  python3 scripts/init_worktree.py --json
  python3 scripts/init_worktree.py --dry-run
"""

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    HAS_RICH = True
    console = Console()
except ImportError:
    HAS_RICH = False
    console = None  # type: ignore


@dataclass
class SymlinkEntry:
    source: str
    target: str
    status: str  # "created", "already_linked", "relinked", "skipped", "error"
    details: str = ""


@dataclass
class MainSyncStatus:
    base_ref: str = "main"
    main_commit: str | None = None
    remote_commit: str | None = None
    is_dirty: bool = False
    dirty_reason: str = ""
    updated_worktree: bool = False
    warning: str | None = None


@dataclass
class WorktreeInitResult:
    success: bool
    name: str
    worktree_path: str
    branch: str
    main_commit: str | None = None
    base_ref: str = "main"
    main_updated: bool = False
    main_dirty: bool = False
    main_dirty_reason: str = ""
    warning: str | None = None
    created: bool = False
    rebased: bool = False
    symlinks: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GitWorktreeManager:
    """Manages Git worktree operations, remote fetching, and main branch synchronization."""

    def __init__(self, cwd: str | Path | None = None):
        self.cwd = Path(cwd).resolve() if cwd else Path.cwd().resolve()

    def _run_git(self, args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
        cmd = ["git"] + args
        work_dir = cwd or self.cwd
        return subprocess.run(
            cmd,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            check=False,
        )

    def get_current_branch(self, cwd: Path | None = None) -> str:
        """Get the name of the currently checked out branch."""
        res = self._run_git(["branch", "--show-current"], cwd=cwd)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
        res = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
        return "HEAD"

    def get_main_repo_root(self) -> Path:
        """Find the root directory of the primary repository."""
        res = self._run_git(["rev-parse", "--git-common-dir"])
        if res.returncode != 0 or not res.stdout.strip():
            # Fallback to show-toplevel
            top_res = self._run_git(["rev-parse", "--show-toplevel"])
            if top_res.returncode == 0 and top_res.stdout.strip():
                return Path(top_res.stdout.strip()).resolve()
            return self.cwd

        common_git_dir = Path(res.stdout.strip())
        if not common_git_dir.is_absolute():
            common_git_dir = (self.cwd / common_git_dir).resolve()

        if common_git_dir.name == ".git":
            return common_git_dir.parent.resolve()
        return common_git_dir.resolve()

    def get_default_workspaces_dir(self, main_repo_root: Path) -> Path:
        """Infer the standard workspaces directory for multi-agent worktrees."""
        # 1. Environment variable override
        if "WORKSPACES_ROOT" in os.environ:
            return Path(os.environ["WORKSPACES_ROOT"]).resolve()

        # 2. Check if current worktree is inside an existing workspaces tree (e.g. /workspaces/boss-agent-mobile/<wt>)
        if "workspaces" in self.cwd.parts:
            parts = list(self.cwd.parts)
            ws_idx = parts.index("workspaces")
            if ws_idx + 1 < len(parts):
                return Path(*parts[: ws_idx + 2]).resolve()

        # 3. Check standard /Volumes/Data/orca/workspaces/<repo_name>
        candidate_orca = Path("/Volumes/Data/orca/workspaces") / main_repo_root.name
        if candidate_orca.parent.exists():
            return candidate_orca.resolve()

        # 4. Sibling workspaces directory
        sibling_ws = main_repo_root.parent / "workspaces" / main_repo_root.name
        return sibling_ws.resolve()

    def find_worktree_for_branch(self, branch: str = "main") -> Path | None:
        """Find the worktree path that currently has the specified branch checked out."""
        res = self._run_git(["worktree", "list", "--porcelain"])
        if res.returncode != 0 or not res.stdout.strip():
            return None
        target_ref = f"refs/heads/{branch}"
        current_wt: Path | None = None
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.startswith("worktree "):
                current_wt = Path(line.split("worktree ", 1)[1].strip()).resolve()
            elif line.startswith("branch "):
                ref = line.split("branch ", 1)[1].strip()
                if ref == target_ref and current_wt:
                    return current_wt
            elif not line:
                current_wt = None
        return None

    def check_worktree_dirty(self, path: Path) -> tuple[bool, str]:
        """Check if a worktree has uncommitted tracked changes or conflicts."""
        res = self._run_git(["status", "--porcelain", "-uno"], cwd=path)
        if res.returncode != 0:
            return True, f"Failed to inspect git status in {path}: {res.stderr or res.stdout}"
        out = res.stdout.strip()
        if out:
            lines = out.splitlines()
            return True, f"{len(lines)} uncommitted change(s) in tracked files"
        return False, ""

    def sync_main_branch(
        self, remote: str = "origin", fetch: bool = True, dry_run: bool = False
    ) -> MainSyncStatus:
        """Fetch remote main and fast-forward local main branch / working tree safely.

        If the worktree checking out 'main' is dirty, falls back to remote main (e.g. 'origin/main')
        for worktree base, leaves local main untouched, and reports a warning for user action.
        """
        # Resolve local main commit
        rev_res = self._run_git(["rev-parse", "refs/heads/main"])
        local_commit = rev_res.stdout.strip() if rev_res.returncode == 0 else None

        if not fetch:
            return MainSyncStatus(base_ref="main", main_commit=local_commit)

        # Check if remote exists
        remotes_res = self._run_git(["remote"])
        available_remotes = remotes_res.stdout.splitlines() if remotes_res.returncode == 0 else []
        if remote not in available_remotes:
            return MainSyncStatus(base_ref="main", main_commit=local_commit)

        # 1. Fetch remote main
        fetch_res = self._run_git(["fetch", remote, "main"])
        if fetch_res.returncode != 0:
            # Proceed with local main if fetch fails / offline
            return MainSyncStatus(base_ref="main", main_commit=local_commit)

        # 2. Get remote main commit
        remote_ref_res = self._run_git(["rev-parse", f"refs/remotes/{remote}/main"])
        if remote_ref_res.returncode != 0 or not remote_ref_res.stdout.strip():
            return MainSyncStatus(base_ref="main", main_commit=local_commit)
        target_sha = remote_ref_res.stdout.strip()

        # 3. Locate worktree checking out main
        main_wt = self.find_worktree_for_branch("main")

        if main_wt is not None:
            # Check if main worktree is dirty
            is_dirty, dirty_reason = self.check_worktree_dirty(main_wt)
            if is_dirty:
                warning = (
                    f"⚠️ 本地 main 工作区存在未提交修改 ({main_wt})，无法自动同步工作区代码！\n"
                    f"👉 未提交改动: {dirty_reason}\n"
                    f"👉 本次已自动降级为直接基于远程 '{remote}/main' 进行初始化/rebase。\n"
                    f"👉 请稍后手动前往本地 main 仓库处理未提交的修改！"
                )
                return MainSyncStatus(
                    base_ref=f"{remote}/main",
                    main_commit=target_sha,
                    remote_commit=target_sha,
                    is_dirty=True,
                    dirty_reason=dirty_reason,
                    updated_worktree=False,
                    warning=warning,
                )

            # Main worktree is clean -> fast-forward merge remote main into working tree
            if dry_run:
                return MainSyncStatus(
                    base_ref="main",
                    main_commit=target_sha,
                    remote_commit=target_sha,
                    is_dirty=False,
                    updated_worktree=True,
                )

            merge_res = self._run_git(["merge", "--ff-only", f"refs/remotes/{remote}/main"], cwd=main_wt)
            if merge_res.returncode == 0:
                return MainSyncStatus(
                    base_ref="main",
                    main_commit=target_sha,
                    remote_commit=target_sha,
                    is_dirty=False,
                    updated_worktree=True,
                )
            else:
                # Merge failed (e.g. untracked file collision or non-ff diverged history)
                err_msg = merge_res.stderr.strip() or merge_res.stdout.strip() or "Fast-forward merge failed"
                warning = (
                    f"⚠️ 本地 main 分支与远程同步失败 ({main_wt})！\n"
                    f"👉 失败原因: {err_msg}\n"
                    f"👉 本次已自动降级为直接基于远程 '{remote}/main' 进行初始化/rebase。\n"
                    f"👉 请稍后手动前往本地 main 仓库检查冲突或分叉！"
                )
                return MainSyncStatus(
                    base_ref=f"{remote}/main",
                    main_commit=target_sha,
                    remote_commit=target_sha,
                    is_dirty=True,
                    dirty_reason=err_msg,
                    updated_worktree=False,
                    warning=warning,
                )

        # 4. No worktree is currently checking out main -> update ref directly
        if local_commit:
            ff_check = self._run_git(["merge-base", "--is-ancestor", "refs/heads/main", target_sha])
            if ff_check.returncode == 0:
                if not dry_run:
                    self._run_git(["update-ref", "refs/heads/main", target_sha])
                return MainSyncStatus(
                    base_ref="main",
                    main_commit=target_sha,
                    remote_commit=target_sha,
                    is_dirty=False,
                    updated_worktree=False,
                )
            else:
                warning = (
                    f"⚠️ 本地 main 分支与远程 '{remote}/main' 分叉，无法 fast-forward！\n"
                    f"👉 本次已自动降级为直接基于远程 '{remote}/main' 进行初始化/rebase。"
                )
                return MainSyncStatus(
                    base_ref=f"{remote}/main",
                    main_commit=target_sha,
                    remote_commit=target_sha,
                    is_dirty=True,
                    dirty_reason="Diverged branches",
                    updated_worktree=False,
                    warning=warning,
                )
        else:
            # Local main doesn't exist yet
            if not dry_run:
                self._run_git(["update-ref", "refs/heads/main", target_sha])
            return MainSyncStatus(
                base_ref="main",
                main_commit=target_sha,
                remote_commit=target_sha,
                is_dirty=False,
                updated_worktree=False,
            )

    def create_or_update_worktree(
        self,
        target_path: Path,
        branch_name: str,
        base_ref: str = "main",
        rebase: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Create a new worktree or update/rebase an existing one."""
        target_path = target_path.resolve()

        if target_path.exists():
            is_wt = (target_path / ".git").exists()
            if not is_wt:
                try:
                    is_empty = not any(target_path.iterdir())
                except Exception:
                    is_empty = False
                if not is_empty:
                    raise RuntimeError(
                        f"Target path '{target_path}' exists and is not a valid git worktree."
                    )
                if not dry_run:
                    target_path.rmdir()
            else:
                # Valid existing worktree
                rebased = False
                current_br = self.get_current_branch(cwd=target_path)
                target_branch = branch_name or current_br

                if rebase and not dry_run and target_branch != base_ref and target_branch != "main":
                    ancestor_check = self._run_git(
                        ["merge-base", "--is-ancestor", base_ref, target_branch],
                        cwd=target_path,
                    )
                    if ancestor_check.returncode != 0:
                        rebase_res = self._run_git(["rebase", base_ref], cwd=target_path)
                        if rebase_res.returncode != 0:
                            self._run_git(["rebase", "--abort"], cwd=target_path)
                            raise RuntimeError(
                                f"Rebase on '{base_ref}' failed with conflicts: {rebase_res.stderr or rebase_res.stdout}"
                            )
                        rebased = True
                    else:
                        rebased = False
                return {
                    "created": False,
                    "updated": True,
                    "path": str(target_path),
                    "branch": target_branch,
                    "rebased": rebased,
                }

        if dry_run:
            return {
                "created": True,
                "updated": False,
                "path": str(target_path),
                "branch": branch_name,
                "rebased": False,
            }

        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Check if local branch already exists
        br_res = self._run_git(["branch", "--list", branch_name])
        branch_exists = bool(br_res.stdout.strip()) if br_res.returncode == 0 else False

        if branch_exists:
            add_res = self._run_git(["worktree", "add", str(target_path), branch_name])
            if add_res.returncode != 0:
                raise RuntimeError(f"Failed to add worktree: {add_res.stderr or add_res.stdout}")
            rebased = False
            if rebase and branch_name != base_ref and branch_name != "main":
                ancestor_check = self._run_git(
                    ["merge-base", "--is-ancestor", base_ref, branch_name],
                    cwd=target_path,
                )
                if ancestor_check.returncode != 0:
                    rebase_res = self._run_git(["rebase", base_ref], cwd=target_path)
                    if rebase_res.returncode != 0:
                        self._run_git(["rebase", "--abort"], cwd=target_path)
                        raise RuntimeError(
                            f"Rebase on '{base_ref}' failed with conflicts: {rebase_res.stderr or rebase_res.stdout}"
                        )
                    rebased = True
                else:
                    rebased = False
            return {
                "created": True,
                "updated": False,
                "path": str(target_path),
                "branch": branch_name,
                "rebased": rebased,
            }
        else:
            add_res = self._run_git(
                ["worktree", "add", "-b", branch_name, str(target_path), base_ref]
            )
            if add_res.returncode != 0:
                raise RuntimeError(f"Failed to create worktree: {add_res.stderr or add_res.stdout}")
            return {
                "created": True,
                "updated": False,
                "path": str(target_path),
                "branch": branch_name,
                "rebased": False,
            }


class ConfigSymlinkManager:
    """Discovers and symlinks shared local configs (*.local.*, .env) into target worktrees."""

    def __init__(self, main_repo_root: Path):
        self.main_repo_root = main_repo_root.resolve()

    def get_git_tracked_files(self) -> set[str]:
        """List all files tracked by git in the main repository."""
        res = subprocess.run(
            ["git", "ls-files"],
            cwd=str(self.main_repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            return set(res.stdout.splitlines())
        return set()

    def discover_shared_configs(self) -> list[Path]:
        """Find local untracked config files in main repo."""
        tracked = self.get_git_tracked_files()
        shared_files: list[Path] = []

        config_dir = self.main_repo_root / "config"
        if config_dir.exists() and config_dir.is_dir():
            for item in config_dir.iterdir():
                if not item.is_file():
                    continue
                rel_path = f"config/{item.name}"
                # Must not be git-tracked (e.g. locators.yaml is tracked)
                if rel_path in tracked:
                    continue

                # Local overrides, yaml, json, or memory states
                if (
                    ".local." in item.name
                    or item.name.startswith(".env")
                    or item.name == "candidate_memory.json"
                ):
                    shared_files.append(item)

        # Main repo root level local configs (.env, .env.local)
        for root_item in [self.main_repo_root / ".env", self.main_repo_root / ".env.local"]:
            if root_item.exists() and root_item.is_file():
                rel_root = root_item.name
                if rel_root not in tracked:
                    shared_files.append(root_item)

        return sorted(shared_files, key=lambda p: str(p))

    def link_boss_agent(
        self, target_worktree: Path, dry_run: bool = False
    ) -> SymlinkEntry | None:
        """Symlink .boss_agent directory so worktree shares the main branch's database and runtime storage."""
        target_worktree = target_worktree.resolve()
        if target_worktree == self.main_repo_root:
            return None

        main_boss_agent = self.main_repo_root / ".boss_agent"
        if not dry_run:
            main_boss_agent.mkdir(parents=True, exist_ok=True)
            (main_boss_agent / "pb_data").mkdir(parents=True, exist_ok=True)

        target_boss_agent = target_worktree / ".boss_agent"
        try:
            rel_boss_agent_src = os.path.relpath(main_boss_agent, target_boss_agent.parent)
        except Exception:
            rel_boss_agent_src = str(main_boss_agent)

        if dry_run:
            return SymlinkEntry(
                source=str(main_boss_agent),
                target=str(target_boss_agent),
                status="created (dry-run)",
                details="Would symlink .boss_agent to main repo .boss_agent",
            )

        if target_boss_agent.is_symlink():
            current_target = target_boss_agent.resolve()
            if current_target == main_boss_agent.resolve():
                return SymlinkEntry(
                    source=str(main_boss_agent),
                    target=str(target_boss_agent),
                    status="already_linked",
                    details="Symlink already intact",
                )
            else:
                target_boss_agent.unlink()
                target_boss_agent.symlink_to(rel_boss_agent_src)
                return SymlinkEntry(
                    source=str(main_boss_agent),
                    target=str(target_boss_agent),
                    status="relinked",
                    details="Re-pointed existing symlink to main repo .boss_agent",
                )

        if target_boss_agent.exists():
            if target_boss_agent.is_dir() and not any(target_boss_agent.iterdir()):
                target_boss_agent.rmdir()
                target_boss_agent.symlink_to(rel_boss_agent_src)
                return SymlinkEntry(
                    source=str(main_boss_agent),
                    target=str(target_boss_agent),
                    status="created",
                    details="Replaced empty directory with symlink to main repo .boss_agent",
                )
            else:
                return SymlinkEntry(
                    source=str(main_boss_agent),
                    target=str(target_boss_agent),
                    status="skipped",
                    details="Regular non-empty directory or file already exists",
                )

        try:
            target_boss_agent.symlink_to(rel_boss_agent_src)
            return SymlinkEntry(
                source=str(main_boss_agent),
                target=str(target_boss_agent),
                status="created",
                details="Symlink to main repo .boss_agent established successfully",
            )
        except Exception as e:
            return SymlinkEntry(
                source=str(main_boss_agent),
                target=str(target_boss_agent),
                status="error",
                details=str(e),
            )

    def link_shared_configs(
        self, target_worktree: Path, dry_run: bool = False
    ) -> list[SymlinkEntry]:
        """Create relative symlinks for all shared config files and .boss_agent in the target worktree."""
        target_worktree = target_worktree.resolve()
        results: list[SymlinkEntry] = []

        if target_worktree == self.main_repo_root:
            return results

        boss_agent_entry = self.link_boss_agent(target_worktree=target_worktree, dry_run=dry_run)
        if boss_agent_entry:
            results.append(boss_agent_entry)

        configs = self.discover_shared_configs()
        for src_file in configs:
            # Determine target relative location
            try:
                rel_to_main = src_file.relative_to(self.main_repo_root)
            except ValueError:
                rel_to_main = Path("config") / src_file.name

            target_file = target_worktree / rel_to_main

            if dry_run:
                results.append(
                    SymlinkEntry(
                        source=str(src_file),
                        target=str(target_file),
                        status="created (dry-run)",
                        details="Would create symlink",
                    )
                )
                continue

            target_file.parent.mkdir(parents=True, exist_ok=True)

            # Compute relative symlink target
            try:
                rel_symlink_src = os.path.relpath(src_file, target_file.parent)
            except Exception:
                rel_symlink_src = str(src_file)

            if target_file.is_symlink():
                current_target = target_file.resolve()
                if current_target == src_file.resolve():
                    results.append(
                        SymlinkEntry(
                            source=str(src_file),
                            target=str(target_file),
                            status="already_linked",
                            details="Symlink already intact",
                        )
                    )
                    continue
                else:
                    # Broken or different symlink -> recreate
                    target_file.unlink()
                    target_file.symlink_to(rel_symlink_src)
                    results.append(
                        SymlinkEntry(
                            source=str(src_file),
                            target=str(target_file),
                            status="relinked",
                            details="Re-pointed existing symlink",
                        )
                    )
                    continue

            if target_file.exists():
                results.append(
                    SymlinkEntry(
                        source=str(src_file),
                        target=str(target_file),
                        status="skipped",
                        details="Regular non-symlink file already exists",
                    )
                )
                continue

            # Create new symlink
            try:
                target_file.symlink_to(rel_symlink_src)
                results.append(
                    SymlinkEntry(
                        source=str(src_file),
                        target=str(target_file),
                        status="created",
                        details="Symlink established successfully",
                    )
                )
            except Exception as e:
                results.append(
                    SymlinkEntry(
                        source=str(src_file),
                        target=str(target_file),
                        status="error",
                        details=str(e),
                    )
                )

        return results


def init_worktree(
    name: str | None = None,
    branch: str | None = None,
    workspaces_dir: str | Path | None = None,
    custom_path: str | Path | None = None,
    remote: str = "origin",
    fetch: bool = True,
    rebase: bool = True,
    link_config: bool = True,
    cwd: str | Path | None = None,
    dry_run: bool = False,
) -> WorktreeInitResult:
    """Master orchestrator for initializing/synchronizing a worktree with latest main and shared configs."""
    manager = GitWorktreeManager(cwd=cwd)
    main_repo_root = manager.get_main_repo_root()

    # 1. Resolve target path and workspace name
    if custom_path:
        target_path = Path(custom_path).resolve()
        ws_name = name or target_path.name
    elif name:
        candidate_path = Path(name)
        if candidate_path.is_absolute() or candidate_path.exists():
            target_path = candidate_path.resolve()
            ws_name = target_path.name
        else:
            ws_root = (
                Path(workspaces_dir).resolve()
                if workspaces_dir
                else manager.get_default_workspaces_dir(main_repo_root)
            )
            target_path = ws_root / name
            ws_name = name
    else:
        # Default: initialize / synchronize the CURRENT worktree directory
        target_path = manager.cwd
        ws_name = target_path.name

    # 2. Resolve branch name
    if branch:
        branch_name = branch
    elif target_path == manager.cwd:
        branch_name = manager.get_current_branch(cwd=target_path)
    else:
        branch_name = f"feat/{ws_name}"

    # 3. Synchronize main branch
    sync_res = manager.sync_main_branch(remote=remote, fetch=fetch, dry_run=dry_run)
    if isinstance(sync_res, MainSyncStatus):
        sync_status = sync_res
    elif isinstance(sync_res, str):
        sync_status = MainSyncStatus(base_ref="main", main_commit=sync_res)
    else:
        sync_status = MainSyncStatus(base_ref="main", main_commit=None)

    base_ref = sync_status.base_ref
    main_commit = sync_status.main_commit

    # 4. Create or update worktree
    try:
        wt_info = manager.create_or_update_worktree(
            target_path=target_path,
            branch_name=branch_name,
            base_ref=base_ref,
            rebase=rebase,
            dry_run=dry_run,
        )
    except Exception as e:
        return WorktreeInitResult(
            success=False,
            name=ws_name,
            worktree_path=str(target_path),
            branch=branch_name,
            main_commit=main_commit,
            base_ref=base_ref,
            main_updated=sync_status.updated_worktree,
            main_dirty=sync_status.is_dirty,
            main_dirty_reason=sync_status.dirty_reason,
            warning=sync_status.warning,
            message=f"Worktree synchronization failed: {e}",
        )

    # 5. Link shared configs
    symlinks_data: list[dict[str, Any]] = []
    if link_config:
        symlink_mgr = ConfigSymlinkManager(main_repo_root=main_repo_root)
        symlink_entries = symlink_mgr.link_shared_configs(
            target_worktree=target_path, dry_run=dry_run
        )
        symlinks_data = [asdict(e) for e in symlink_entries]

    msg = "Worktree synchronized and configs linked successfully."
    if sync_status.warning:
        msg = f"Worktree synchronized based on {base_ref}. (Warning: local main is dirty)."

    return WorktreeInitResult(
        success=True,
        name=ws_name,
        worktree_path=str(target_path),
        branch=branch_name,
        main_commit=main_commit,
        base_ref=base_ref,
        main_updated=sync_status.updated_worktree,
        main_dirty=sync_status.is_dirty,
        main_dirty_reason=sync_status.dirty_reason,
        warning=sync_status.warning,
        created=wt_info.get("created", False),
        rebased=wt_info.get("rebased", False),
        symlinks=symlinks_data,
        message=msg,
    )


def print_rich_report(result: WorktreeInitResult, dry_run: bool = False) -> None:
    """Render a human-friendly Rich report to terminal."""
    if not HAS_RICH or console is None:
        print(f"=== Worktree Synchronized: {result.name} ===")
        print(f"Path: {result.worktree_path}")
        print(f"Branch: {result.branch}")
        print(f"Base Ref: {result.base_ref}")
        print(f"Base Commit: {result.main_commit}")
        print(
            f"Local main Status: {'Dirty' if result.main_dirty else ('Updated' if result.main_updated else 'Up to date')}"
        )
        print(f"Rebased: {'Yes' if result.rebased else 'No'}")
        print(f"Symlinks: {len(result.symlinks)} configured")
        if result.warning:
            print(f"\n{result.warning}\n")
        return

    status_str = (
        "[bold yellow]DRY-RUN[/bold yellow]" if dry_run else "[bold green]READY[/bold green]"
    )
    title = f"🚀 Worktree Initializer: {result.name} ({status_str})"

    info_table = Table(show_header=False, box=None)
    info_table.add_column("Key", style="bold cyan")
    info_table.add_column("Value", style="white")

    info_table.add_row("Worktree Path", result.worktree_path)
    info_table.add_row("Git Branch", f"[green]{result.branch}[/green]")
    info_table.add_row("Base Commit", result.main_commit or "N/A")

    if result.base_ref != "main":
        info_table.add_row(
            "Base Ref", f"[bold yellow]{result.base_ref} (fallback)[/bold yellow]"
        )
    else:
        info_table.add_row("Base Ref", f"[green]{result.base_ref}[/green]")

    if result.main_dirty:
        info_table.add_row(
            "Local main Status",
            "[bold yellow]⚠️ Dirty (Uncommitted changes - skipped)[/bold yellow]",
        )
    elif result.main_updated:
        info_table.add_row("Local main Status", "[bold green]✅ Updated (fast-forwarded)[/bold green]")
    else:
        info_table.add_row("Local main Status", "[dim]Up to date[/dim]")

    info_table.add_row("Created New", "Yes" if result.created else "Updated Existing")
    if result.created:
        info_table.add_row(
            f"Base ({result.base_ref})", f"[green]Branched from {result.base_ref}[/green]"
        )
    else:
        info_table.add_row(
            f"Rebased on {result.base_ref}",
            "[green]Yes[/green]" if result.rebased else "[dim]No (Already up to date)[/dim]",
        )

    console.print(Panel(info_table, title=title, border_style="blue"))

    if result.warning:
        console.print(
            Panel(
                result.warning,
                title="[bold yellow]⚠️ Attention Required / 注意[/bold yellow]",
                border_style="yellow",
            )
        )

    if result.symlinks:
        symlink_table = Table(title="🔗 Shared Configuration Symlinks")
        symlink_table.add_column("Config File", style="cyan")
        symlink_table.add_column("Status", style="magenta")
        symlink_table.add_column("Source Target", style="dim")

        for link in result.symlinks:
            stat = link.get("status", "")
            icon = "✅" if "created" in stat or "already" in stat or "relinked" in stat else "⚠️"
            symlink_table.add_row(
                Path(link.get("target", "")).name,
                f"{icon} {stat}",
                link.get("source", ""),
            )
        console.print(symlink_table)

    console.print(f"\n[bold green]✔[/bold green] {result.message}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize and synchronize current (or named) git worktree with main and shared local configs."
    )
    parser.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Optional name/path of the worktree (defaults to current worktree directory)",
    )
    parser.add_argument(
        "--branch", "-b", help="Git branch name (defaults to current branch)", default=None
    )
    parser.add_argument("--path", "-p", help="Custom worktree destination directory", default=None)
    parser.add_argument(
        "--workspaces-dir", help="Custom parent workspaces directory", default=None
    )
    parser.add_argument(
        "--fetch",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fetch origin/main from remote (default: True)",
    )
    parser.add_argument(
        "--rebase",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Rebase worktree branch onto latest main (default: True)",
    )
    parser.add_argument(
        "--link-config",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Create shared config symlinks (default: True)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Simulate without making changes")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")

    args = parser.parse_args()

    result = init_worktree(
        name=args.name,
        branch=args.branch,
        workspaces_dir=args.workspaces_dir,
        custom_path=args.path,
        fetch=args.fetch,
        rebase=args.rebase,
        link_config=args.link_config,
        dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print_rich_report(result, dry_run=args.dry_run)

    sys.exit(0 if result.success else 1)


if __name__ == "__main__":
    main()
