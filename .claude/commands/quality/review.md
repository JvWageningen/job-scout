Review recent changes.

Run `git diff HEAD~1 --name-only` to get changed files; read only those.
Run diagnostics: `uv run ruff check . --output-format=concise; uv run pytest --tb=line -q; uv run mypy src/ --no-error-summary`
For each changed file: check type hints, docstrings, logic errors, unhandled edge cases. Suggest simplifications.
