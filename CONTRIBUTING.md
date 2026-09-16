# Contributing to job-scout

Thanks for being here. job-scout is a tool for people who are job hunting, built by someone
who was, and it gets better every time somebody else tries it on their own search and reports
what broke.

Contributions of every size are welcome. A typo fix in the docs is a real contribution. So
is a bug report that says "the NS travel times are wrong for my postcode and here is the
log". You do not need to write Python to help, and you do not need to ask permission before
opening an issue.

If you do want to write code, the two easiest places to start are a new job board scraper
and a new notification channel — both are small, well-isolated and covered by existing
tests you can copy. Details below.

- **Code of Conduct:** [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — be decent, and expect the same.
- **Security issues:** do **not** open a public issue. See [SECURITY.md](SECURITY.md).
- **Licensing of contributions:** see [Licensing of contributions](#licensing-of-contributions)
  at the bottom. Please read it before your first pull request — it is short and it matters.

---

## Ways to contribute

### Bug reports

The most useful thing you can send. A good report includes:

- what you ran (the exact `job-scout ...` command, or the dashboard tab and action),
- how you installed it (Docker install script, `docker compose`, or a local `uv sync`),
- which LLM provider you are on (`local`, `zai`, `claude_cli`, `kilo_cli`),
- the relevant lines from the run log (`data/users/<name>/logs/`), and
- `uv run job-scout config show --user <name>` output with anything sensitive removed —
  the command already masks any field whose name contains `key`.

### Feature ideas

Open an issue describing the problem you hit, not just the solution you imagined. "Indeed
keeps showing me recruitment agencies and I cannot filter them out" leads somewhere better
than "add an agency blacklist".

### Documentation

Docs live in [`docs/`](docs/README.md) and are as much a part of the product as the code.
Corrections, clearer wording, worked examples and screenshots are all wanted. Documentation
changes need no test, and `docs:` commits do not cut a release, so they are a safe first PR.

### New job board scrapers

**Where:** `src/job_scout/scraper.py`. **What to implement:** a module-level function
`_scrape_<source>(keyword: str, ...) -> list[JobListing]` that returns
`job_scout.models.JobListing` instances, then register it in `scrape_all_jobs()` by
appending `(func, args)` to the `tasks` list. Everything else is handled for you: the fan-out
runs in a `ThreadPoolExecutor`, and `_deduplicate()` collapses cross-source duplicates before
the pipeline sees them.

Look at `_scrape_nvb()` (a direct JSON API, ~30 lines) as the model to copy, and
`tests/test_scraper.py` for how to test it without hitting the network. Please respect the
board's terms of service and rate limits — `_scrape_nvb_with_rate_limit()` shows the pattern.

Note that a company careers page usually needs no code at all: `job-scout sites add
<url> --name "Example Corp"` lets the LLM extract listings from arbitrary HTML. Write a
scraper when a board has a real API or a stable structure worth exploiting.

### New notification channels

**Where:** `src/job_scout/notify/`. **What to implement:** the `Notifier` protocol in
`src/job_scout/notify/base.py` — three methods, `send(job)`, `send_digest(jobs)` and
`check_available()` — then add your branch to `get_notifier()` and
`build_raw_notifier_for_test()` in `src/job_scout/notify/factory.py`, and add the channel
name to the `notification_channel` `Literal` in `src/job_scout/models.py`. Raise
`NotificationError` on failure; `check_available()` must not make network calls.

`ntfy.py` is the smallest existing implementation; `slack.py` and `discord.py` show
rich-format payloads. Tests go in `tests/test_notify.py`.

### New LLM backends

**Where:** `src/job_scout/llm/`. **What to implement:** the `LLMClient` protocol in
`src/job_scout/llm/base.py` — `complete(prompt, *, purpose, timeout)` and
`check_available()` — where `purpose` is a `CallPurpose` literal (`evaluation`,
`quick_eval`, `screening`, `keywords`, `cv_parsing`, `resume_tailoring`, `cover_letter`,
`screening_questions`, `screening_answers`, `behavioral_questions`) so your client can route
cheap calls to a cheap model. Wire it into `_build_raw_client()` in
`src/job_scout/llm/factory.py` and extend the provider `Literal` in `models.py`; the factory
wraps every client in `RetryingLLMClient` for you, so do not implement your own retry loop.

`local.py` (OpenAI-compatible HTTP) and `kilo_cli.py` (subprocess) are the two shapes a
backend tends to take.

### Translations and language coverage

job-scout is Dutch-market-first and bilingual by design: keyword generation produces Dutch
and English lists, the title filter in `src/job_scout/title_filter.py` is morpheme-aware for
Dutch compounds, and `language_preferences` defaults to `['nl', 'en']`. If you want it to
work for another market, the interesting work is in the prompts, the title filter and a
scraper for a local board. Dashboard UI strings live in `src/job_scout/web/static/`.

---

## Development setup

You need Python 3.12+ (the repo pins 3.12 in `.python-version`) and
[uv](https://docs.astral.sh/uv/). Do not use `pip` — `uv` owns the lockfile.

```bash
git clone https://github.com/JvWageningen/job-scout.git
cd job-scout
uv sync
```

Copy the environment template and fill in what you need. It holds only the deployment
values — the dashboard port, the timezone and the optional dashboard token:

```bash
cp .env.example .env
```

API keys do **not** belong in `.env.example` or any tracked file. They go in
`data/secrets.yaml` (git-ignored) or in the `JOB_SCOUT_*` environment variables documented
in [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

Run the test suite:

```bash
uv run pytest
```

Run a single file or a single test while you work:

```bash
uv run pytest tests/test_scraper.py
uv run pytest tests/test_scraper.py -k deduplicate
```

Optional: browser rendering for JavaScript-heavy career pages and the `--browser` flags on
`prune` and `find-sources` needs the extra:

```bash
uv sync --extra browser
```

Never commit anything from `data/` — it holds live databases, parsed CVs, `config.yaml` and
`secrets.yaml`. It is git-ignored, and it should stay that way.

---

## Quality gates

These are the exact commands CI runs, in CI's order. Run them locally before you push and
your PR will go green first time:

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest -x --tb=short
uv run mypy src/
```

To fix rather than just report, use `uv run ruff check . --fix` and `uv run ruff format .`.

Two further checks are configured in `.pre-commit-config.yaml` and are worth running on
anything non-trivial, even though CI does not currently enforce them:

```bash
uv run bandit -c pyproject.toml -r src/
uv run vulture src/ --min-confidence 80
```

`pre-commit` itself ships in the dev dependency group, so `uv sync` has already put it in the
environment and `uv run pre-commit install` resolves without a separate install. One caveat:
the pinned `ruff-pre-commit` revision is older than the `ruff` in the dev dependency group, so
the two can disagree on formatting. If they do, **the version from `uv run ruff` is the one CI
uses** and therefore the one that wins.

`mypy` runs in strict mode over `src/`. New code must type-check cleanly; `src/job_scout/cli.py`
has a narrow override for untyped Click decorators, and that is the only exemption.

---

## Commit conventions

job-scout uses [Conventional Commits](https://www.conventionalcommits.org/), and this is not
cosmetic: **python-semantic-release cuts a release from every push to `main`**, deriving the
version bump, the tag, the `CHANGELOG.md` entry and the GitHub Release directly from your
commit subjects. A wrong prefix ships a wrong version number.

| Commit subject | Release bump | Example result from 2.0.0 |
| --- | --- | --- |
| `fix: ...` | patch | 2.0.1 |
| `perf: ...` | patch | 2.0.1 |
| `feat: ...` | minor | 2.1.0 |
| `feat!: ...` or a `BREAKING CHANGE:` footer | major | 3.0.0 |
| `docs:`, `chore:`, `refactor:`, `test:`, `style:`, `ci:`, `build:` | none | 2.0.0 |

An optional scope in parentheses is encouraged — usually the module you touched.

Three real examples:

```text
fix(travel): keep the Nominatim throttle when geocoding runs in parallel
```

```text
feat(scraper): add Werkzoeken.nl as a listing source

Direct JSON API, registered in scrape_all_jobs behind nvb_keyword_limit.
```

```text
feat(config)!: rename max_travel_pt to max_travel_transit

BREAKING CHANGE: existing per-user config.yaml files must rename the key;
the old name is no longer read.
```

Keep the subject line in the imperative mood, under ~72 characters, and put the *why* in the
body. If a commit both fixes a bug and adds a feature, it wants to be two commits.

---

## Pull request process

1. **Branch from `main`** using the commit type as the prefix: `feat/werkzoeken-scraper`,
   `fix/ors-fallback`, `docs/configuration-table`.
2. **Keep the PR focused.** One feature or one fix. A drive-by refactor in the same diff
   makes review slower, not faster.
3. **Write a description that answers three questions:** what problem this solves, how you
   solved it, and how a reviewer can verify it. Link the issue if there is one. If the
   behaviour is visible in the dashboard, a screenshot is worth several paragraphs.
4. **Add or update tests.** Bug fixes should include the test that would have caught the bug.
5. **CI must be green.** The `check` job runs ruff, ruff format, pytest and mypy on every
   pull request targeting `main`; nothing is merged red.
6. **Review and merge.** The maintainer reviews, may ask for changes, and then squashes or
   merges. If your commit subjects already follow the convention above, the squash subject
   will too — so it is worth getting them right.

Draft PRs are fine and welcome. Open one early if you want a second opinion on an approach
before you finish it.

---

## Coding standards

The project's conventions, all of which are either enforced by tooling or consistently
followed in `src/`:

- **Type hints on every signature**, parameters and return type. `mypy --strict` will tell
  you if you forgot.
- **Google-style docstrings** on public functions: a one-line summary, then `Args:`,
  `Returns:` and `Raises:` where they apply. Look at `src/job_scout/notify/base.py` for the
  house style.
- **`loguru` for logging, never `print()`.** `from loguru import logger`, then
  `logger.info(...)`. The one exception is CLI output the user is meant to read, which goes
  through `click.echo()`.
- **Absolute imports only:** `from job_scout.models import JobListing`, never `from .models
  import ...`. Deferred imports inside a function are acceptable where they break a cycle or
  keep an optional dependency optional; the existing ones carry a `# noqa: PLC0415`.
- **`ruff` owns formatting.** Line length 88, target `py312`, lint rules `E`, `F`, `I`, `UP`,
  `B`, `SIM`, `N`. Do not hand-format around it.
- **Pydantic models** for anything that is configuration or structured LLM output — see
  `src/job_scout/models.py`. New config keys need a default, a type and a docstring comment,
  and a row in [docs/CONFIGURATION.md](docs/CONFIGURATION.md).
- **Tests live in `tests/` and mirror the module name:** `src/job_scout/salary.py` is tested
  by `tests/test_salary.py`. Shared fixtures are in `tests/conftest.py` and helpers in
  `tests/helpers.py`. Tests must not make network calls or depend on a running LLM — stub the
  client.
- **No personal data in fixtures.** Use neutral names, addresses and MAC addresses in tests
  and examples.

For the shape of the system as a whole — the pipeline stages, where each module sits and why
— read [ARCHITECTURE.md](ARCHITECTURE.md) before a larger change.

---

## Licensing of contributions

Short version: **you keep the copyright in what you write, and you license it to the project
on the same terms the project ships under.** The detail, so that you know exactly what you
are agreeing to when you open a pull request:

1. **Inbound = outbound.** Contributions are accepted under the
   [PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0)
   (SPDX: `PolyForm-Noncommercial-1.0.0`), the same licence the project itself is released
   under from v2.0.0 onward. *Why:* a project whose contributions arrive under a different
   licence than it ships under cannot be distributed at all, so keeping inbound and outbound
   identical is what makes the code usable by anyone.

2. **You confirm you have the right to contribute the code.** By submitting a pull request
   you are stating that the work is yours to give — that you wrote it, or that you have
   permission to contribute it, and that it is not encumbered by an employment agreement or
   an incompatible third-party licence. *Why:* nobody downstream can rely on the licence if
   the contributor was not entitled to grant it in the first place.

3. **You additionally grant the maintainer permission to relicense your contribution under
   different terms in future.** This is a grant to Jeroen van Wageningen as the project
   maintainer, it is not exclusive, and it does not take your own rights away — you remain
   free to use your code however you like, including in commercial work. *Why:* it keeps
   dual-licensing possible (for example, offering a commercial licence to a company that
   wants one) and keeps the door open to moving the project to a more permissive licence
   later, without having to track down every contributor for individual permission.

There is no separate CLA to sign and no bot to click through. Opening a pull request is the
agreement.

### What PolyForm Noncommercial actually permits

Free for **any noncommercial purpose**: your own job hunt, study, research, hobby projects.
Free for charities, schools, public research bodies, public safety and health bodies,
environmental organisations and government institutions. What is **not** granted is
commercial use — nobody may sell it, resell it, offer it as a paid or hosted service, or use
it to run a business.

This makes job-scout **source-available, not OSI-approved open source** — the OSI definition
does not allow use restrictions, and this licence has one. That is a deliberate trade, stated
plainly here rather than hidden behind a label that does not apply.

For commercial licensing enquiries, open a GitHub Discussion or an issue.

### The MIT history

The entire **v1.x** line, up to and including v1.17.3, was released under the MIT licence.
That grant is irrevocable: anyone who obtained those versions keeps their MIT rights to that
code forever, and nothing about the relicense changes it. The PolyForm terms apply to
**v2.0.0 and later**.

One practical note: GitHub's licence detector does not recognise PolyForm, so the repository
sidebar reads "View license" rather than naming it. That is expected, not a misconfiguration.

---

## Questions?

Open a [GitHub Discussion](https://github.com/JvWageningen/job-scout/discussions) for
anything open-ended — "would you take a PR that does X?", "is this the right place to hook
in?", "why does the pipeline do Y before Z?". Open an
[issue](https://github.com/JvWageningen/job-scout/issues) for a specific bug or a concrete
feature request. Security reports go through [SECURITY.md](SECURITY.md) instead.

Asking first is always cheaper than building the wrong thing. Thanks for contributing.
