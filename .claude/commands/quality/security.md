Audit for security vulnerabilities: $ARGUMENTS (or whole codebase if omitted)

Run `uv run bandit -c pyproject.toml -r src/ -f txt; uv run pip-audit`. Fix Medium+ bandit findings. Upgrade vulnerable packages with `uv add <pkg>@latest`.
Manually check: hardcoded secrets, unsanitized input, path traversal, insecure deserialization, missing auth.
For each issue: vulnerability description, severity, fix.
