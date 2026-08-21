# Demo and Multimedia Assets Management

Guidelines and hard rules for handling demo videos, animations, and heavy multimedia assets.

## Core Rule: Zero Git History Bloat

**NEVER commit binary multimedia files directly into code branches or git history.**
Committing `.mp4`, `.mov`, high-resolution `.png`, or large `.gif` files into feature branches or `main` bloats repository clone sizes and slows down workspace sync across agents and contributors.

## Asset Storage & Hosting Standards

All demo and preview media must be hosted as **GitHub Attachments** (`user-attachments`), keeping the entire git repository and branch tree 100% free of binary media files:

1. **CLI Upload via `gh-attach` (Recommended for Agents)**:
   ```bash
   # Upload video directly to GitHub user-attachments CDN
   gh attach /path/to/demo.mp4 -R forchain/boss-agent-mobile
   ```
   This outputs a URL in the form `https://github.com/user-attachments/assets/<uuid>`.

2. **Web UI Drag-and-Drop (Alternative for Humans)**:
   - Drag and drop the MP4 video into an Issue/PR comment or description box on GitHub.
   - Copy the generated `https://github.com/user-attachments/assets/<uuid>` link.

3. **Markdown Presentation in `README.md`**:
   - Embed the video using standard centered markdown:
     ```markdown
     <div align="center">

     https://github.com/user-attachments/assets/<uuid>

     *Demo: Boss 直聘 Android 移动端自动化求职交互全流程*

     </div>
     ```
   - GitHub automatically parses and renders standalone `user-attachments` video links as native HTML5 video players in the README.

## Agent Checklist Before Committing

- [ ] Check `git status` and `git diff --stat` to ensure NO binary files (`.mp4`, `.mov`, `.png`, `.gif`, etc.) are added or modified in the working tree.
- [ ] Ensure all demo media URLs in `README.md` use external `https://github.com/user-attachments/assets/...` links.
