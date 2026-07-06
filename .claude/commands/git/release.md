Prepare a release commit: $ARGUMENTS (major, minor, or patch)

Read `VERSION` for current version. Review changes since last tag via `git log --oneline $(git describe --tags --abbrev=0)..HEAD`.
Compose a commit message with the `[$ARGUMENTS]` flag (e.g. `feat: add X [minor]`). Stage and commit. Remind: CI auto-bumps version and creates a GitHub release on push to main.
