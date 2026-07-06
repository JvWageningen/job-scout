Prepare code for commit.

Run all checks: `uv run ruff check . --output-format=concise; uv run pytest --tb=line -q; uv run mypy src/ --no-error-summary`
Apply ruff auto-fix. Fix any test/type failures.
Run `git diff --stat && git diff` to review; suggest a concise commit message. Do NOT commit.
