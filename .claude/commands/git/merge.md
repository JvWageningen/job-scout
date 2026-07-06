Merge branch into current: $ARGUMENTS (branch name)

Run `git log --oneline HEAD..$ARGUMENTS` to preview incoming commits. Check for version flags (`[major]`, `[minor]`, `[patch]`) in those commits.
Run `git merge $ARGUMENTS`. If conflicts arise, list them with `git diff --name-only --diff-filter=U`, show each conflict, and resolve. After merge, verify with `git status` and `uv run pytest -x`.
