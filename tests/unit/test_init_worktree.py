"""
tests/unit/test_init_worktree.py
================================
Unit tests for git worktree initialization, main sync/rebase,
and shared config symlink manager.
"""

from unittest.mock import MagicMock, patch

import pytest
from scripts.init_worktree import (
    ConfigSymlinkManager,
    GitWorktreeManager,
    WorktreeInitResult,
    init_worktree,
    print_rich_report,
)


def test_worktree_manager_resolve_paths(tmp_path):
    """Test resolution of main repo root and workspace directories."""
    main_repo = tmp_path / "main_repo"
    main_repo.mkdir()
    (main_repo / ".git").mkdir()

    manager = GitWorktreeManager(cwd=str(main_repo))
    with patch.object(manager, "_run_git") as mock_git:
        mock_git.return_value = MagicMock(returncode=0, stdout=str(main_repo / ".git") + "\n", stderr="")
        root = manager.get_main_repo_root()
        assert root == main_repo.resolve()


def test_worktree_manager_resolve_paths_fallback_show_toplevel(tmp_path):
    """Test fallback to show-toplevel when common-dir fails."""
    main_repo = tmp_path / "main_repo"
    main_repo.mkdir()

    manager = GitWorktreeManager(cwd=str(main_repo))
    with patch.object(manager, "_run_git") as mock_git:
        def mock_side_effect(args, **kwargs):
            if "--git-common-dir" in args:
                return MagicMock(returncode=1, stdout="", stderr="error")
            if "--show-toplevel" in args:
                return MagicMock(returncode=0, stdout=str(main_repo) + "\n", stderr="")
            return MagicMock(returncode=1, stdout="", stderr="")

        mock_git.side_effect = mock_side_effect
        root = manager.get_main_repo_root()
        assert root == main_repo.resolve()


def test_worktree_manager_get_default_workspaces_dir(tmp_path):
    """Test default workspaces directory resolution."""
    ws_dir = tmp_path / "workspaces" / "boss-agent-mobile"
    current_wt = ws_dir / "redfish"
    current_wt.mkdir(parents=True)

    manager = GitWorktreeManager(cwd=str(current_wt))
    main_root = tmp_path / "github" / "boss-agent-mobile"
    main_root.mkdir(parents=True)

    resolved_ws = manager.get_default_workspaces_dir(main_repo_root=main_root)
    assert resolved_ws == ws_dir.resolve()


def test_worktree_manager_sync_main_fast_forward(tmp_path):
    """Test fetch and update main branch with fast forward."""
    manager = GitWorktreeManager(cwd=str(tmp_path))
    with patch.object(manager, "_run_git") as mock_git:
        def mock_git_side_effect(args, **kwargs):
            if "remote" in args:
                return MagicMock(returncode=0, stdout="origin\n", stderr="")
            if "fetch" in args:
                return MagicMock(returncode=0, stdout="Fetched", stderr="")
            if "rev-parse" in args and "refs/remotes/origin/main" in args:
                return MagicMock(returncode=0, stdout="sha_remote_main\n", stderr="")
            if "merge-base" in args:
                return MagicMock(returncode=0, stdout="is ancestor", stderr="")
            if "update-ref" in args:
                return MagicMock(returncode=0, stdout="", stderr="")
            if "rev-parse" in args and "refs/heads/main" in args:
                return MagicMock(returncode=0, stdout="sha_remote_main\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_git.side_effect = mock_git_side_effect
        commit = manager.sync_main_branch(remote="origin", fetch=True)
        assert commit == "sha_remote_main"


def test_worktree_manager_sync_main_no_fetch(tmp_path):
    """Test sync main with fetch=False."""
    manager = GitWorktreeManager(cwd=str(tmp_path))
    with patch.object(manager, "_run_git") as mock_git:
        mock_git.return_value = MagicMock(returncode=0, stdout="local_commit_sha\n", stderr="")
        commit = manager.sync_main_branch(fetch=False)
        assert commit == "local_commit_sha"


def test_worktree_manager_create_worktree_new_branch(tmp_path):
    """Test worktree creation with new branch."""
    manager = GitWorktreeManager(cwd=str(tmp_path))
    target = tmp_path / "target_wt"

    with patch.object(manager, "_run_git") as mock_git:
        def mock_git_side_effect(args, **kwargs):
            if "branch" in args and "--list" in args:
                return MagicMock(returncode=0, stdout="", stderr="")  # branch does not exist
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_git.side_effect = mock_git_side_effect
        res = manager.create_or_update_worktree(
            target_path=target,
            branch_name="feat/new-feature",
            base_ref="main",
            rebase=False,
            dry_run=False,
        )
        assert res["created"] is True
        assert res["branch"] == "feat/new-feature"


def test_worktree_manager_create_worktree_existing_branch(tmp_path):
    """Test worktree creation when git branch already exists."""
    manager = GitWorktreeManager(cwd=str(tmp_path))
    target = tmp_path / "target_wt"

    with patch.object(manager, "_run_git") as mock_git:
        def mock_git_side_effect(args, **kwargs):
            if "branch" in args and "--list" in args:
                return MagicMock(returncode=0, stdout="feat/existing-branch\n", stderr="")
            if "worktree" in args and "add" in args:
                return MagicMock(returncode=0, stdout="", stderr="")
            if "rebase" in args:
                return MagicMock(returncode=0, stdout="Rebased", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_git.side_effect = mock_git_side_effect
        res = manager.create_or_update_worktree(
            target_path=target,
            branch_name="feat/existing-branch",
            base_ref="main",
            rebase=True,
            dry_run=False,
        )
        assert res["created"] is True
        assert res["branch"] == "feat/existing-branch"
        assert res["rebased"] is True


def test_worktree_manager_update_existing_worktree(tmp_path):
    """Test updating an already existing worktree folder."""
    target = tmp_path / "existing_wt"
    target.mkdir()
    (target / ".git").write_text("gitdir: ...")

    manager = GitWorktreeManager(cwd=str(tmp_path))
    with patch.object(manager, "_run_git") as mock_git:
        mock_git.return_value = MagicMock(returncode=0, stdout="Rebased", stderr="")
        res = manager.create_or_update_worktree(
            target_path=target,
            branch_name="feat/existing",
            base_ref="main",
            rebase=True,
            dry_run=False,
        )
        assert res["created"] is False
        assert res["updated"] is True
        assert res["rebased"] is True


def test_worktree_manager_rebase_conflict_abort(tmp_path):
    """Test rebase conflict triggers abort and raises RuntimeError."""
    target = tmp_path / "existing_wt"
    target.mkdir()
    (target / ".git").write_text("gitdir: ...")

    manager = GitWorktreeManager(cwd=str(tmp_path))
    with patch.object(manager, "_run_git") as mock_git:
        def mock_git_side_effect(args, **kwargs):
            if "rebase" in args and "--abort" not in args:
                return MagicMock(returncode=1, stdout="", stderr="CONFLICT (content): Merge conflict")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_git.side_effect = mock_git_side_effect
        with pytest.raises(RuntimeError, match="failed with conflicts"):
            manager.create_or_update_worktree(
                target_path=target,
                branch_name="feat/conflict",
                base_ref="main",
                rebase=True,
                dry_run=False,
            )


def test_worktree_manager_non_worktree_collision(tmp_path):
    """Test non-worktree non-empty directory raises RuntimeError."""
    target = tmp_path / "non_wt_dir"
    target.mkdir()
    (target / "some_random_file.txt").write_text("hello")

    manager = GitWorktreeManager(cwd=str(tmp_path))
    with pytest.raises(RuntimeError, match="is not a valid git worktree"):
        manager.create_or_update_worktree(
            target_path=target,
            branch_name="feat/test",
            base_ref="main",
            rebase=False,
            dry_run=False,
        )


def test_config_symlink_manager_discovery(tmp_path):
    """Test discovery of non-git-tracked local configs."""
    main_repo = tmp_path / "main_repo"
    config_dir = main_repo / "config"
    config_dir.mkdir(parents=True)

    # Tracked files
    (config_dir / "locators.yaml").write_text("tracked: true")
    (config_dir / "searches.yaml").write_text("tracked: true")

    # Local untracked files
    (config_dir / "candidate.local.yaml").write_text("name: Test")
    (config_dir / "llm.local.yaml").write_text("api_key: secret")
    (config_dir / "settings.local.yaml").write_text("debug: true")
    (config_dir / "candidate_memory.json").write_text("{}")
    (main_repo / ".env").write_text("FOO=BAR")

    manager = ConfigSymlinkManager(main_repo_root=main_repo)

    with patch.object(manager, "get_git_tracked_files") as mock_tracked:
        mock_tracked.return_value = {"config/locators.yaml", "config/searches.yaml"}
        discovered = manager.discover_shared_configs()

        discovered_names = {f.name for f in discovered}
        assert "candidate.local.yaml" in discovered_names
        assert "llm.local.yaml" in discovered_names
        assert "settings.local.yaml" in discovered_names
        assert "candidate_memory.json" in discovered_names
        assert ".env" in discovered_names
        # Must not contain tracked files
        assert "locators.yaml" not in discovered_names
        assert "searches.yaml" not in discovered_names


def test_config_symlink_manager_linking_and_idempotency(tmp_path):
    """Test symlink creation, idempotency, broken symlink recovery, and skipped files."""
    main_repo = tmp_path / "main_repo"
    src_config = main_repo / "config"
    src_config.mkdir(parents=True)
    src_file = src_config / "candidate.local.yaml"
    src_file.write_text("name: Tony")

    target_wt = tmp_path / "worktree_a"
    target_config = target_wt / "config"
    target_config.mkdir(parents=True)

    manager = ConfigSymlinkManager(main_repo_root=main_repo)
    with patch.object(manager, "discover_shared_configs") as mock_disc:
        mock_disc.return_value = [src_file]

        # 1. First run: created
        links1 = manager.link_shared_configs(target_worktree=target_wt)
        assert len(links1) == 1
        assert links1[0].status == "created"
        link_target = target_config / "candidate.local.yaml"
        assert link_target.is_symlink()
        assert link_target.read_text() == "name: Tony"

        # 2. Second run: already_linked
        links2 = manager.link_shared_configs(target_worktree=target_wt)
        assert len(links2) == 1
        assert links2[0].status == "already_linked"

        # 3. Third run: broken link re-linked
        link_target.unlink()
        link_target.symlink_to(tmp_path / "non_existent_file.yaml")
        links3 = manager.link_shared_configs(target_worktree=target_wt)
        assert len(links3) == 1
        assert links3[0].status == "relinked"
        assert link_target.read_text() == "name: Tony"

        # 4. Fourth run: regular file skipped
        link_target.unlink()
        link_target.write_text("custom user copy")
        links4 = manager.link_shared_configs(target_worktree=target_wt)
        assert len(links4) == 1
        assert links4[0].status == "skipped"


def test_init_worktree_e2e_dry_run(tmp_path):
    """Test end-to-end init_worktree workflow with dry_run."""
    main_repo = tmp_path / "main_repo"
    main_repo.mkdir()
    (main_repo / ".git").mkdir()
    (main_repo / "config").mkdir()
    (main_repo / "config" / "settings.local.yaml").write_text("k: v")

    result = init_worktree(
        name="test-wt",
        branch="feat/test-wt",
        workspaces_dir=tmp_path / "workspaces",
        cwd=main_repo,
        dry_run=True,
    )

    assert isinstance(result, WorktreeInitResult)
    assert result.success is True
    assert result.branch == "feat/test-wt"
    assert result.created is True
    assert len(result.symlinks) >= 1
    assert "dry-run" in result.symlinks[0]["status"]

    # Test report printing doesn't raise errors
    print_rich_report(result, dry_run=True)
