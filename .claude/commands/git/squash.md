Squash recent commits: $ARGUMENTS (number of commits, e.g. 3)

Run `git log --oneline -$ARGUMENTS` to show commits being squashed.
Run `git reset --soft HEAD~$ARGUMENTS`, then compose one conventional commit message summarizing all. Preserve the highest version flag from the originals (major > minor > patch).
