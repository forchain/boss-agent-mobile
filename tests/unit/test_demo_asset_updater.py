from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.update_demo_asset import update_demo_asset


def test_update_demo_asset_file_not_found(tmp_path: Path) -> None:
    non_existent = tmp_path / "non_existent.mp4"
    result = update_demo_asset(str(non_existent))
    assert result is False


@patch("shutil.which")
def test_update_demo_asset_missing_tool(mock_which: MagicMock, tmp_path: Path) -> None:
    video_file = tmp_path / "test.mp4"
    video_file.write_bytes(b"dummy")

    mock_which.side_effect = lambda tool: None if tool == "ffmpeg" else "/usr/bin/git"

    result = update_demo_asset(str(video_file))
    assert result is False


@patch("subprocess.run")
@patch("shutil.which")
def test_update_demo_asset_dry_run_success(
    mock_which: MagicMock, mock_run: MagicMock, tmp_path: Path
) -> None:
    video_file = tmp_path / "test.mp4"
    video_file.write_bytes(b"dummy video content")

    mock_which.return_value = "/usr/bin/mock"

    # subprocess mock responses
    mock_run_res = MagicMock()
    mock_run_res.returncode = 0
    mock_run_res.stdout = "https://github.com/forchain/boss-agent-mobile.git\n"
    mock_run.return_value = mock_run_res

    result = update_demo_asset(str(video_file), dry_run=True)
    assert result is True
