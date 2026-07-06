Refactor the entire repository for consistency and maintainability.

Run diagnostics: `uv run ruff check . --output-format=concise; uv run radon cc src/ -mi C; uv run vulture src/ --min-confidence 80; uv run mypy src/ --no-error-summary`
Apply ruff auto-fix. Work through findings by priority: high-complexity functions (extract helpers, early returns), dead code (remove), type errors (fix), inconsistent naming/duplication (consolidate).
Update tests for changed interfaces.
