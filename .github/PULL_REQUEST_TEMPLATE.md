<!--
Thanks for contributing to job-scout.

Releases are cut automatically by python-semantic-release from the commit history on `main`, so the
Conventional Commit prefix on your commits (and on the squash-merge title) decides the version bump.
Get that right and everything downstream — version, tag, CHANGELOG entry, GitHub Release — follows.
-->

## Summary

<!-- What changes, and why. One or two paragraphs. Describe the behaviour, not the diff. -->

## Related issue

<!-- e.g. Closes #123. Write "None" if this stands alone. -->

## Type of change

Tick the prefix your commits use. The right-hand note is the release effect.

- [ ] `feat:` — new capability (minor version bump)
- [ ] `fix:` — bug fix (patch version bump)
- [ ] `perf:` — performance improvement (patch version bump)
- [ ] `docs:` — documentation only (no release)
- [ ] `refactor:` — behaviour-preserving restructure (no release)
- [ ] `test:` — tests only (no release)
- [ ] `chore:` / `ci:` / `build:` / `style:` — tooling, workflows, packaging, formatting (no release)
- [ ] Breaking change — `!` after the type, or a `BREAKING CHANGE:` footer (major version bump)

If you ticked breaking change, describe the migration path here:

<!-- What breaks, and what a user must do about it. -->

## How this was tested

<!--
Be specific. "Ran the tests" is not useful on its own; name the suite or the scenario.
Examples:
  - Added tests/test_pruner.py::test_expired_listing_is_marked_expired, covering the 404 branch.
  - Ran `uv run job-scout prune --user alex --dry-run` against a local database with 40 active jobs.
  - Verified the Schedule tab still renders the slot editor after the config change.
-->

## Checklist

Run the same gates CI runs, from the repository root:

- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run pytest -x --tb=short` passes
- [ ] `uv run mypy src/` passes
- [ ] New and changed functions have type hints on their signatures and Google-style docstrings
- [ ] Documentation is updated where behaviour changed — new or changed config keys in
      `docs/CONFIGURATION.md`, new or changed commands in `docs/USAGE.md`
- [ ] No secrets, API keys, personal data or CV content in the diff, the tests or the fixtures
- [ ] Commits follow Conventional Commits, matching the type ticked above
- [ ] I agree that my contribution is licensed under the PolyForm Noncommercial License 1.0.0, on the
      terms set out in [CONTRIBUTING.md](https://github.com/JvWageningen/job-scout/blob/main/CONTRIBUTING.md)
