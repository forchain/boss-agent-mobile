# Git Workflow & Pull Request Rules

Ironclad operational rules for Git branch management and Pull Requests in this repository.

## 1. Branch Naming Prior to PR Creation

Before creating or pushing a Pull Request, verify the current Git branch name:
- Branches must follow descriptive conventions: `forchain/<feature-name>` or `feat/<feature-name>` (or `fix/<bug-name>`).
- If currently on an auto-generated, temporary, or non-descriptive branch (e.g. `forchain/i-will-read-the-2` or similar defaults), **rename and normalize** the branch locally and remotely before creating the PR:
  ```bash
  git branch -m <normalized-branch-name>
  git push -u origin <normalized-branch-name>
  ```

## 2. Single PR Per Worktree Discipline

Unless explicitly instructed by the user:
- **Never stack or create multiple Pull Requests within the same worktree.**
- If new changes deviate from the original PR title/scope, amend and iterate on the existing PR's title and description to maintain single-PR progression.
- If a divergence is significant and genuinely warrants a separate PR, **always confirm with the user first**: whether to open a new PR within this worktree or create an isolated worktree.

## 3. Explicit Issue Closing Keywords

When creating a Pull Request linked to one or more GitHub issues:
- Always use standard GitHub closing keywords in the PR body (`Closes #123`, `Fixes #123`, or `Resolves #123`).
- **Explicit multi-issue declaration**: Each issue must have its own keyword explicitly declared:
  - ✅ `Closes #123, Closes #124` or separate lines (`Closes #123\nCloses #124`).
  - ❌ `Closes #123, #124` (GitHub will only auto-close `#123` and leave `#124` open).

## 4. PR Body Format

Align with the `/pr` skill template:
- **Summary**: Concise visual (diagram, call tree, diff sketch, or shallow file tree) showing the key change using domain terms from `GLOSSARY.md`.
- **Evidence**: Concrete before/after evidence (passing tests, output, or screenshots).
- **Merge Danger**: Door type (one-way vs two-way) and blast radius.
