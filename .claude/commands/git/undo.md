Safely undo: $ARGUMENTS (e.g. "last commit", "staged changes", "unstaged changes")

For last commit: `git reset --soft HEAD~1` (keeps changes staged). For staged: `git restore --staged .`. For unstaged: `git stash`.
Show `git status` and `git log -3 --oneline` before and after. Never use `--hard` without explicit confirmation.
