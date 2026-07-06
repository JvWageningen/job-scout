Remove dead code, unused imports, and duplicate utilities.

Run `uv run vulture src/ --min-confidence 80`. Review findings: remove confirmed unused functions, classes, variables.
Find duplicate utilities across modules; consolidate into one canonical location.
