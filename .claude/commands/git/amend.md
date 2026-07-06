Amend the last commit: $ARGUMENTS

Run `git log -1 --format='%s'` to show current message and `git diff --cached --stat` for newly staged changes.
If $ARGUMENTS provided, use as new message; otherwise keep existing. Warn if commit is already pushed to remote. Run `git commit --amend`.
