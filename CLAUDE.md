# job-scout

## Project
Automated daily job search tool for the Dutch job market

## Tech Stack
- Python 3.12, managed by `uv`
- Ruff for linting and formatting
- pytest for testing
- mypy for type checking

## Directory Map
- `src/job_scout/` - main package
- `tests/` - test suite (mirrors src structure)

## Commands
- `uv run pytest` - run tests
- `uv run ruff check . --fix` - lint and fix
- `uv run ruff format .` - format
- `uv run mypy src/` - type check
- `uv run bandit -r src/` - security check
- `uv run pip-audit` - dependency vulnerability check
- `uv run radon cc src/ -mi C` - complexity report
- `uv run vulture src/` - unused code detection
- `cymbal index .` - (re)index codebase for cymbal

## Code Style Rules
- Always add type hints to function signatures
- Use Google-style docstrings on every public function and class
- Use loguru for logging, never print()
- Use Pydantic models for structured data
- Prefer early returns over deep nesting
- Keep functions under 30 lines; extract helpers if longer
- Use absolute imports: `from job_scout.module import ...`
- snake_case for files, modules, functions, variables
- PascalCase for classes
- UPPER_SNAKE_CASE for constants

## Efficiency
- Read specific line ranges (offset/limit), not whole files. Use Grep before Read.
- Batch independent tool calls into single messages.
- Use /compact at logical breakpoints. Never let context exceed ~200K.

## Code Exploration Policy
Use `cymbal` CLI for code navigation — prefer it over Read, Grep, Glob, or Bash for code exploration.
- **New to a repo?**: `cymbal structure` — entry points, hotspots, central packages. Start here.
- **To understand a symbol**: `cymbal investigate <symbol>` — returns source, callers, impact, or members based on what the symbol is.
- **To understand multiple symbols**: `cymbal investigate Foo Bar Baz` — batch mode, one invocation.
- **To trace an execution path**: `cymbal trace <symbol>` — follows the call graph downward.
- **To assess change risk**: `cymbal impact <symbol>` — follows the call graph upward.
- Before reading a file: `cymbal outline <file>` or `cymbal show <file:L1-L2>`
- Before searching: `cymbal search <query>` (symbols) or `cymbal search <query> --text` (grep)
- Before exploring structure: `cymbal ls` (tree) or `cymbal ls --stats` (overview)
- To disambiguate: `cymbal show path/to/file.py:SymbolName` or `cymbal investigate file.py:Symbol`
- First run: `cymbal index .` to build the initial index (<1s). After that, queries auto-refresh.
- All commands support `--json` for structured output.

## Verification
After any code change, always:
1. Run: `uv run ruff check . --fix` and `uv run ruff format .`
2. Run: `uv run pytest -x`
3. Run: `uv run mypy src/`

## Things to Avoid
- Never use pip install directly; always `uv add`
- Never use bare `except:` - catch specific exceptions
- Never use mutable default arguments
- No `print()` statements - use loguru
