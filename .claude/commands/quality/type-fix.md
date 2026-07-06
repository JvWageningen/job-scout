Fix all mypy type errors.

Run `uv run mypy src/ --show-error-codes`. Read each failing file; fix type errors: add missing annotations, correct wrong types. Use `type: ignore` only as last resort with explaining comment.
Re-run mypy to confirm all errors resolved.
