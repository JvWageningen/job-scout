Audit project dependencies.

Run `uv run pip-audit` for CVEs. Run `uv tree` for dependency tree.
For vulnerable packages: `uv add <package>@latest`. Read pyproject.toml; identify unpinned packages, suggest pins.
Flag abandoned packages (no releases 2+ years, archived repo).
