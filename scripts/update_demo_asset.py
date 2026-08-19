#!/usr/bin/env python3
"""Script to update the demo video asset on the orphan 'assets' branch.

This script ensures that large binary demo assets (video, GIF preview, poster)
are kept strictly on an isolated 'assets' branch without git history bloat,
allowing the main branch to remain lightweight.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def update_demo_asset(
    video_path: str,
    remote: str = "origin",
    branch: str = "assets",
    gif_duration: int = 6,
    gif_start_time: str = "00:00:10",
    poster_time: str = "00:00:15",
    dry_run: bool = False,
) -> bool:
    video_file = Path(video_path).expanduser().resolve()
    if not video_file.is_file():
        print(f"Error: Video file not found: {video_file}", file=sys.stderr)
        return False

    # Check dependencies
    for tool in ("git", "ffmpeg"):
        if not shutil.which(tool):
            print(f"Error: Required tool '{tool}' is not installed or not in PATH.", file=sys.stderr)
            return False

    # Get remote URL from git
    try:
        remote_url_res = subprocess.run(
            ["git", "remote", "get-url", remote],
            capture_output=True,
            text=True,
            check=True,
        )
        remote_url = remote_url_res.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error getting remote URL for '{remote}': {e}", file=sys.stderr)
        return False

    print(f"📦 Updating demo asset from: {video_file}")
    print(f"🌐 Remote: {remote} ({remote_url}) -> Branch: {branch}")

    temp_dir = tempfile.mkdtemp(prefix="boss_demo_asset_")
    try:
        temp_path = Path(temp_dir)

        # 1. Initialize clean git repo
        subprocess.run(["git", "init"], cwd=temp_path, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "--orphan", branch], cwd=temp_path, check=True, capture_output=True)

        # 2. Copy and standardize video name
        target_video = temp_path / "demo.mp4"
        shutil.copyfile(video_file, target_video)

        # 3. Generate full-workflow fast-forward preview gif
        target_gif = temp_path / "demo-preview.gif"
        print("🎬 Generating optimized fast-forward preview GIF...")
        subprocess.run(
            [
                "ffmpeg",
                "-i",
                str(target_video),
                "-vf",
                "setpts=0.5*PTS,fps=10,scale=320:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
                "-loop",
                "0",
                str(target_gif),
                "-y",
            ],
            check=True,
            capture_output=True,
        )

        # 4. Generate poster image
        target_poster = temp_path / "demo-poster.png"
        print("🖼️  Generating poster image...")
        subprocess.run(
            [
                "ffmpeg",
                "-ss",
                poster_time,
                "-i",
                str(target_video),
                "-vframes",
                "1",
                str(target_poster),
                "-y",
            ],
            check=True,
            capture_output=True,
        )

        # 5. Commit
        subprocess.run(
            ["git", "add", "demo.mp4", "demo-preview.gif", "demo-poster.png"],
            cwd=temp_path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "chore(assets): update demo video and preview assets"],
            cwd=temp_path,
            check=True,
            capture_output=True,
        )

        # 6. Push to remote
        if dry_run:
            print("🔍 Dry run enabled: skipping push.")
            return True

        subprocess.run(
            ["git", "remote", "add", remote, remote_url],
            cwd=temp_path,
            check=True,
            capture_output=True,
        )
        print(f"🚀 Force-pushing to {remote}/{branch}...")
        push_res = subprocess.run(
            ["git", "push", "-u", remote, branch, "-f"],
            cwd=temp_path,
            capture_output=True,
            text=True,
        )
        if push_res.returncode != 0:
            print(f"Error during push: {push_res.stderr}", file=sys.stderr)
            return False

        print("✅ Demo assets successfully updated on isolated orphan branch!")
        return True

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update demo video asset on orphan assets branch without git history bloat."
    )
    parser.add_argument("video_path", help="Path to the new demo video MP4 file")
    parser.add_argument("--remote", default="origin", help="Git remote name (default: origin)")
    parser.add_argument("--branch", default="assets", help="Target orphan branch (default: assets)")
    parser.add_argument("--dry-run", action="store_true", help="Perform asset generation without pushing")

    args = parser.parse_args()
    success = update_demo_asset(
        video_path=args.video_path,
        remote=args.remote,
        branch=args.branch,
        dry_run=args.dry_run,
    )
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
