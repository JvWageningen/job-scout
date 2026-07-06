Fix everything that fails in the verification suite.

Run all diagnostics: `uv run ruff check . --output-format=concise; uv run pytest --tb=line -q; uv run mypy src/ --no-error-summary`
Apply ruff auto-fix. Fix test failures by reading only failing test files and their source. Fix mypy errors. Repeat until clean.
Do NOT weaken assertions — fix actual bugs.
