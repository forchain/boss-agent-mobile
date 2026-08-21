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
class WorktreeInitResult:
    success: bool
    name: str
    worktree_path: str
    branch: str
    main_commit: str | None = None
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

    def sync_main_branch(self, remote: str = "origin", fetch: bool = True) -> str | None:
        """Fetch remote main and fast-forward local main branch safely."""
        if not fetch:
            rev_res = self._run_git(["rev-parse", "refs/heads/main"])
            return rev_res.stdout.strip() if rev_res.returncode == 0 else None

        # Check if remote exists
        remotes_res = self._run_git(["remote"])
        available_remotes = remotes_res.stdout.splitlines() if remotes_res.returncode == 0 else []
        if remote not in available_remotes:
            rev_res = self._run_git(["rev-parse", "refs/heads/main"])
            return rev_res.stdout.strip() if rev_res.returncode == 0 else None

        # 1. Fetch remote main
        fetch_res = self._run_git(["fetch", remote, "main"])
        if fetch_res.returncode != 0:
            pass  # Proceed with local main if fetch fails / offline

        # 2. Update local main ref safely (fast-forward only check)
        remote_ref_res = self._run_git(["rev-parse", f"refs/remotes/{remote}/main"])
        if remote_ref_res.returncode == 0 and remote_ref_res.stdout.strip():
            target_sha = remote_ref_res.stdout.strip()
            local_ref_res = self._run_git(["rev-parse", "--verify", "refs/heads/main"])
            if local_ref_res.returncode == 0:
                ff_check = self._run_git(
                    ["merge-base", "--is-ancestor", "refs/heads/main", target_sha]
                )
                if ff_check.returncode == 0:
                    self._run_git(["update-ref", "refs/heads/main", target_sha])
                    return target_sha
                else:
                    return local_ref_res.stdout.strip()
            else:
                self._run_git(["update-ref", "refs/heads/main", target_sha])
                return target_sha

        local_ref_res = self._run_git(["rev-parse", "refs/heads/main"])
        return local_ref_res.stdout.strip() if local_ref_res.returncode == 0 else None

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
                    rebase_res = self._run_git(["rebase", base_ref], cwd=target_path)
                    if rebase_res.returncode != 0:
                        self._run_git(["rebase", "--abort"], cwd=target_path)
                        raise RuntimeError(
                            f"Rebase on '{base_ref}' failed with conflicts: {rebase_res.stderr or rebase_res.stdout}"
                        )
                    rebased = True
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
                rebase_res = self._run_git(["rebase", base_ref], cwd=target_path)
                if rebase_res.returncode != 0:
                    self._run_git(["rebase", "--abort"], cwd=target_path)
                    raise RuntimeError(
                        f"Rebase on '{base_ref}' failed with conflicts: {rebase_res.stderr or rebase_res.stdout}"
                    )
                rebased = True
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

    def link_shared_configs(
        self, target_worktree: Path, dry_run: bool = False
    ) -> list[SymlinkEntry]:
        """Create relative symlinks for all shared config files in the target worktree."""
        target_worktree = target_worktree.resolve()
        configs = self.discover_shared_configs()
        results: list[SymlinkEntry] = []

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
    main_commit = manager.sync_main_branch(remote=remote, fetch=fetch)

    # 4. Create or update worktree
    try:
        wt_info = manager.create_or_update_worktree(
            target_path=target_path,
            branch_name=branch_name,
            base_ref="main",
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

    return WorktreeInitResult(
        success=True,
        name=ws_name,
        worktree_path=str(target_path),
        branch=branch_name,
        main_commit=main_commit,
        created=wt_info.get("created", False),
        rebased=wt_info.get("rebased", False),
        symlinks=symlinks_data,
        message="Worktree synchronized and configs linked successfully.",
    )


def print_rich_report(result: WorktreeInitResult, dry_run: bool = False) -> None:
    """Render a human-friendly Rich report to terminal."""
    if not HAS_RICH or console is None:
        print(f"=== Worktree Synchronized: {result.name} ===")
        print(f"Path: {result.worktree_path}")
        print(f"Branch: {result.branch}")
        print(f"Main Commit: {result.main_commit}")
        print(f"Rebased: {'Yes' if result.rebased else 'No'}")
        print(f"Symlinks: {len(result.symlinks)} configured")
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
    info_table.add_row("Base (main) Commit", result.main_commit or "N/A")
    info_table.add_row("Created New", "Yes" if result.created else "Updated Existing")
    info_table.add_row("Rebased on main", "Yes" if result.rebased else "No")

    console.print(Panel(info_table, title=title, border_style="blue"))

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
