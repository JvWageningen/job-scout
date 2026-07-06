Show what breaks if this changes: $ARGUMENTS

Run `cymbal impact $ARGUMENTS` for transitive callers. Fallback: list exported names, find all import sites.
Assess: direct callers (signature changes), indirect callers (type errors), test coverage.
Output a prioritized list of files needing updates.
