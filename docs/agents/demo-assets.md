# Demo and Multimedia Assets Management

Guidelines and hard rules for handling demo videos, animations, and heavy multimedia assets.

## Core Rule: Zero Git History Bloat

**NEVER commit binary multimedia files directly into code branches or git history.**
Committing `.mp4`, `.mov`, high-resolution `.png`, or large `.gif` files into feature branches or `main` bloats repository clone sizes and slows down workspace sync across agents and contributors.

## Asset Storage & Hosting Options

All demo and preview media must use one of the following approaches:

1. **Orphan `assets` Branch (Preferred for repo-hosted demo assets)**:
   - Use `uv run python scripts/update_demo_asset.py /path/to/recording.mp4` to automatically generate optimized preview GIF / poster and force-push to the isolated `assets` branch (`origin/assets`).
   - Reference via raw GitHub URL: `https://raw.githubusercontent.com/forchain/boss-agent-mobile/assets/demo-preview.gif` and blob link: `https://github.com/forchain/boss-agent-mobile/blob/assets/demo.mp4`.
2. **GitHub Attachments / Releases (Alternative)**:
   - Upload media as a GitHub Issue / PR attachment (drag & drop to obtain `https://github.com/user-attachments/assets/...`) or attach to a GitHub Release.
   - Reference the external attachment URL in `README.md` or PR description.

## Agent Checklist Before Committing

- [ ] Check `git status` and `git diff --stat` to ensure NO binary files (`.mp4`, `.avi`, `.mov`, large `.png`, `.gif`) are staged in the working tree.
- [ ] Ensure all media URLs in `README.md` point to external attachment URLs or `origin/assets` URLs.
