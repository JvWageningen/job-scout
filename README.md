# job-scout

[![CI](https://github.com/JvWageningen/job-scout/actions/workflows/ci.yml/badge.svg)](https://github.com/JvWageningen/job-scout/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An automated daily job search tool for the Dutch job market. It scrapes positions from Indeed.nl, Nationalevacaturebank.nl, and LinkedIn, uses an LLM to evaluate listings against your profile and CV, calculates travel times via car, public transport, and bike, filters by configurable travel limits, and sends push notifications for matching positions via [ntfy.sh](https://ntfy.sh). Results are deduplicated across runs using a local SQLite database. Supports multiple independent users, each with their own profile, database, and notification topic, plus an optional web dashboard for managing all of it without the CLI.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- One of the supported LLM backends (see [Configure LLM provider](#configure-llm-provider))
- (Optional) [OpenRouteService API key](https://openrouteservice.org/) for car/bike travel times
- (Optional) [NS API key](https://apiportal.ns.nl/) for Dutch public transport travel times
- (Optional) [ntfy.sh](https://ntfy.sh) topic for push notifications

## Installation

```bash
git clone https://github.com/JvWageningen/job-scout
cd job-scout
uv sync
```

## Quick Start

```bash
# 1. Interactive setup — prompts for profile, CV path, salary, vacation, API keys
uv run job-scout init --user alex

# 2. Generate search keywords and title filters from your profile and CV
uv run job-scout keywords refresh --user alex

# 3. Run a full search cycle
uv run job-scout run --user alex

# 4. Preview without sending notifications
uv run job-scout run --user alex --dry-run
```

Or skip the CLI entirely and use the [web dashboard](#web-dashboard) once a user is set up.

## How It Works

Each `job-scout run` executes a multi-stage filtering pipeline, with LLM calls and I/O parallelized within each stage:

```text
Scrape (Indeed, LinkedIn, NVB, custom sites) — parallel across sources
  → Deduplicate (skip previously seen jobs, by URL and by normalized title+company)
    → Keyword title filter (fast local include/exclude matching)
      → LLM title screening (batched calls review all remaining titles)
        → Quick LLM fit score (cheap first-pass filter, parallelized)
          → Full LLM evaluation (fit score, negative match, salary, vacation, parallelized)
            → Travel time filter (car, bike, public transport, parallelized)
              → Salary & vacation filter
                → Notify via ntfy.sh
```

The `keywords refresh` command generates all filter keywords automatically from your profile:
- **Search keywords** (Dutch + English) for job board queries
- **Title include keywords** (e.g. "CRO", "conversie", "analyst") — titles must contain at least one
- **Title exclude keywords** (e.g. "SAP", "payroll") — titles containing these are skipped instantly

Previously seen evaluations are cached by normalized title+company, so re-evaluating a cross-posted duplicate never costs a second LLM call.

## Usage

### Multi-user

Each user gets their own directory under `data/users/<name>/` containing their `config.yaml`, `jobs.db`, and `logs/` — none of it is tracked in git. Global settings (LLM provider, model names, server URLs) live in `data/config.yaml`, created automatically by `job-scout init`, and are shared across all users.

```bash
# Add a new user
uv run job-scout init --user bob

# Run for a specific user
uv run job-scout run --user alex

# Run for all users
uv run job-scout run --all

# Show a user's effective config (global + user settings merged)
uv run job-scout config show --user alex

# Set a user-scoped value
uv run job-scout config set ntfy_topic "alex-alerts" --user alex

# Set a global value (shared across all users)
uv run job-scout config set max_jobs_per_source 75
```

### Configuration

```bash
uv run job-scout config show                                    # global config
uv run job-scout config show --user alex                        # effective config for a user
uv run job-scout config set home_address "Amsterdam" --user alex
uv run job-scout config set max_travel_car 45 --user alex
uv run job-scout config set fit_score_threshold 60 --user alex
uv run job-scout config set min_salary 3000 --user alex
```

#### Global settings (`data/config.yaml`)

| Key | Default | Description |
|-----|---------|-------------|
| `llm_provider` | `claude_cli` | Default LLM backend: `claude_cli`, `zai`, `kilo_cli`, or `local` |
| `quick_eval_provider` | — | Override provider for quick-eval only (falls back to `llm_provider`) |
| `screening_provider` | — | Override provider for title screening only |
| `evaluation_provider` | — | Override provider for full evaluation only |
| `keywords_provider` | — | Override provider for keyword generation only |
| `max_parallel_evaluations` | `5` | Max concurrent LLM calls during quick-eval/evaluation/screening/travel lookups |
| `max_jobs_per_source` | `50` | Max jobs fetched per source |
| `ntfy_server` | `https://ntfy.sh` | ntfy.sh server URL |
| `llm_max_attempts` | `3` | Retry attempts for LLM calls |
| `llm_retry_base_delay` | `1.0` | Base backoff delay in seconds (doubles each retry) |
| `claude_evaluation_model` | — | Claude model for evaluation (default: CLI default) |
| `claude_screening_model` | `haiku` | Claude model for title screening |
| `zai_base_url` | `https://api.z.ai/api/coding/paas/v4` | Z AI endpoint |
| `zai_model` | `glm-5.1` | Z AI model for evaluation and keywords |
| `zai_screening_model` | `glm-4.5-air` | Z AI model for title screening |
| `zai_screening_batch_size` | `20` | Batch size for Z AI/Kilo screening calls |
| `zai_quick_eval_model` | — | Z AI model for quick evaluation |
| `kilo_evaluation_model` | `zai/glm-5.1` | Kilo CLI model for evaluation |
| `kilo_screening_model` | `zai/glm-4.5-air` | Kilo CLI model for title screening |
| `kilo_quick_eval_model` | — | Kilo CLI model for quick evaluation |
| `local_base_url` | `http://localhost:11434/v1` | OpenAI-compatible endpoint for the `local` provider (Ollama, LM Studio, vLLM, etc. — same machine or LAN) |
| `local_model` | `llama3.1` | Local model for evaluation and keywords |
| `local_screening_model` | — | Local model for title screening |
| `local_quick_eval_model` | — | Local model for quick evaluation |

#### Per-user settings (`data/users/<name>/config.yaml`)

| Key | Default | Description |
|-----|---------|-------------|
| `name` | — | User name |
| `profile_description` | — | Your professional profile and desired roles |
| `negative_description` | — | Roles/skills to exclude |
| `cv_path` | — | Path to your CV PDF |
| `home_address` | — | Home address for travel calculations |
| `max_travel_car` | `30` | Max car travel time (minutes; requires ORS key) |
| `max_travel_pt` | `60` | Max public transport time (minutes; requires NS key) |
| `max_travel_bike` | `45` | Max bike travel time (minutes; requires ORS key) |
| `max_distance_km` | — | Max straight-line distance in km (no API key needed) |
| `geocode_cache_days` | `90` | Cache validity for geocoded addresses (days) |
| `travel_cache_days` | `14` | Cache validity for travel time results (days) |
| `fit_score_threshold` | `60` | Minimum fit score (0–100) |
| `quick_eval_threshold` | `40` | Quick evaluation minimum score |
| `min_salary` | — | Minimum gross monthly salary (EUR) |
| `max_salary` | — | Maximum gross monthly salary (EUR) |
| `min_vacation_days` | — | Minimum annual vacation days |
| `ntfy_topic` | `job-scout-alerts` | ntfy.sh topic for push notifications |
| `language_preferences` | `["nl","en"]` | Language filter for job boards |
| `keywords_dutch` | `[]` | Dutch job search keywords (auto-generated) |
| `keywords_english` | `[]` | English job search keywords (auto-generated) |
| `title_include_keywords` | `[]` | Title must contain at least one (auto-generated) |
| `title_exclude_keywords` | `[]` | Title containing any is skipped (auto-generated) |
| `jobspy_keyword_limit` | `5` | Max keywords to use per scrape for jobspy |
| `jobspy_sites` | `["indeed","linkedin"]` | Job sources to scrape: `indeed`, `linkedin`, `glassdoor`, `zip_recruiter`, `google`, `bayt`, `naukri`, `bdjobs` |
| `nvb_keyword_limit` | `3` | Max keywords to use per scrape for Nationalevacaturebank |
| `custom_sites` | `[]` | Custom site URLs to scrape (see [Custom sites](#custom-sites)) |

#### Secrets (`data/secrets.yaml` or environment variables)

API keys are **never stored in tracked YAML files**. Set them via environment variables or the gitignored `data/secrets.yaml`:

| Secret | Env var | Description |
| --- | --- | --- |
| `zai_api_key` | `JOB_SCOUT_ZAI_API_KEY` | Z AI API key |
| `local_api_key` | `JOB_SCOUT_LOCAL_API_KEY` | API key for the local/LAN LLM server (usually not required) |
| `ors_api_key` | `JOB_SCOUT_ORS_API_KEY` | OpenRouteService API key |
| `ns_api_key` | `JOB_SCOUT_NS_API_KEY` | NS Journey Planner API key |

Environment variables take precedence over `data/secrets.yaml`.

> Set `JOB_SCOUT_DATA_DIR` to override the default `./data/` directory.

### Configure LLM provider

job-scout supports four LLM backends, selectable globally and overridable per pipeline stage (quick-eval, title screening, full evaluation, keyword generation):

- **`claude_cli`** (default) — shells out to the local Claude Code CLI (`claude`)
- **`zai`** — Z AI's GLM models via their OpenAI-compatible REST API
- **`kilo_cli`** — the Kilo Code CLI, routing to Z AI or other providers
- **`local`** — any OpenAI-compatible server on your own machine or local network: [Ollama](https://ollama.com), [LM Studio](https://lmstudio.ai), vLLM, llama.cpp server, text-generation-webui, LocalAI, etc.

```bash
# Switch the default provider
uv run job-scout config set llm_provider local
uv run job-scout config set local_base_url http://192.168.1.50:11434/v1
uv run job-scout config set local_model llama3.1

# Or mix providers per stage — e.g. cheap local model for quick-eval,
# a stronger hosted model for the final evaluation
uv run job-scout config set quick_eval_provider local
uv run job-scout config set evaluation_provider zai
```

All of this is also available in the [web dashboard](#web-dashboard)'s LLM Settings tab, including a "Test Connection" button that verifies a candidate provider/URL/key before you save it.

## Web dashboard

```bash
uv run job-scout web                       # binds 0.0.0.0:8000 by default
uv run job-scout web --host 127.0.0.1 --port 8080
```

A single-page dashboard (plain HTML/JS, no build step) covering every CLI function: user management, profile & filters, keywords, custom sites, LLM provider settings (including local/LAN testing), secrets, schedule management, run triggering with live status, and a log viewer.

### Optional Token Authentication

By default, the dashboard has no authentication — anyone who can reach the host and port can view your data and trigger runs. To enable optional shared-token authentication, set the `JOB_SCOUT_DASHBOARD_TOKEN` environment variable or add `dashboard_token` to `data/secrets.yaml`:

```bash
# Via environment variable
JOB_SCOUT_DASHBOARD_TOKEN="my-secret-token" uv run job-scout web

# Or in data/secrets.yaml
dashboard_token: my-secret-token
```

When a token is configured, the frontend will prompt for it on first use, store it in sessionStorage, and attach it to all subsequent API requests via the `Authorization: Bearer <token>` header. Static files (HTML/CSS/JS) remain unauthenticated so the page can load.

> **Important:** This is a simple shared-secret gate, not a multi-user login/authorization system. The token is sent in plaintext over HTTP unless you use HTTPS (reverse proxy/firewall). Never run the dashboard on an untrusted network without additional security (firewall rules, VPN, HTTPS/TLS) — treat it like an internal tool only.

The dashboard prints a startup banner showing whether authentication is enabled. If you don't use a token, restrict access with firewall rules or a VPN.

### Custom sites

Add arbitrary company career pages or job boards. Each page is fetched and the LLM extracts job postings — no per-site parser needed.

```bash
# Add a site for a specific user
uv run job-scout sites add https://careers.example.com/jobs --name "Example Corp" --user alex

# List configured sites
uv run job-scout sites list --user alex

# Remove a site
uv run job-scout sites remove "Example Corp" --user alex
```

Custom sites are scraped on every `job-scout run` alongside the standard sources. Extraction failures (unreachable pages, unparseable HTML) log a warning and contribute zero jobs — they never abort a run. JS-rendered pages (no server-side HTML) may yield no results.

### Full rerun

Re-scrape, re-evaluate, and re-notify all matches — useful after fixing evaluation issues or changing your profile significantly:

```bash
uv run job-scout run --user alex --full
uv run job-scout run --all --full   # all users
```

A full rerun bypasses the deduplication gate, overwrites stored fit scores and statuses, and re-sends notifications for all matched jobs.

### Viewing Results

```bash
uv run job-scout jobs list                     # recent matched jobs (default 20)
uv run job-scout jobs list --limit 50 --user alex
uv run job-scout jobs rejected --user alex     # rejected jobs with reasons
```

### Run History & Analytics

Each run (except dry runs) is automatically recorded in the user's database with statistics including scraped count, matched, rejected, notified, errors, and duration. View the history via CLI or the web dashboard:

```bash
uv run job-scout runs history --user alex      # show last 30 runs
uv run job-scout runs history --user alex --limit 50
```

The web dashboard includes an **Analytics** tab displaying recent runs in a table and a lightweight trend chart showing matched jobs over time.

**Note:** Dry-run executions (`--dry-run`) are not recorded in run history, as they are not actual searches and do not persist data.

### Scheduling

```bash
uv run job-scout schedule install           # daily cron at 08:00
uv run job-scout schedule install --hour 7  # daily cron at 07:00
uv run job-scout schedule status            # check if cron is installed
uv run job-scout schedule remove            # remove the cron job
```

The cron job runs `job-scout run --all`, picking up all configured users automatically. Secrets are read from `data/secrets.yaml` so no environment configuration is needed for cron.

## Project Structure

```
src/job_scout/
├── cli.py             # Click CLI entry point and pipeline orchestration
├── config.py          # YAML configuration, multi-user path helpers, secret loading
├── models.py          # Pydantic data models (JobListing, Config, CustomSite, …)
├── database.py        # SQLite persistence, deduplication, and evaluation cache
├── evaluator.py        # LLM integration (fit score, negative match, compensation)
├── scraper.py         # Job scraping: jobspy (Indeed, LinkedIn, NVB) + custom sites
├── cv_parser.py       # PDF CV text extraction (PyPDF2)
├── travel.py          # Travel times via Nominatim, OpenRouteService, NS API
├── notifier.py        # ntfy.sh push notifications with retry support
├── scheduler.py       # Cron job install/remove
├── title_filter.py    # Fast keyword-based title pre-filter
├── title_screener.py  # Batch LLM title screening
├── llm/
│   ├── base.py        # LLMClient protocol and LLMError
│   ├── factory.py     # Provider selection, per-purpose routing, RetryingLLMClient wrapping
│   ├── retry.py       # RetryingLLMClient (exponential backoff)
│   ├── claude_cli.py  # Claude Code CLI backend
│   ├── zai.py         # Z AI REST backend
│   ├── kilo_cli.py    # Kilo CLI backend
│   └── local.py       # Local/LAN OpenAI-compatible backend (Ollama, LM Studio, vLLM, …)
└── web/
    ├── app.py         # FastAPI app: every CLI function exposed as a REST endpoint
    └── static/         # Dashboard frontend (plain HTML/CSS/JS, no build step)
```

## Development

```bash
uv run pytest                  # run tests
uv run pytest -x               # stop on first failure
uv run ruff check . --fix      # lint and auto-fix
uv run ruff format .           # format code
uv run mypy src/               # type check
uv run bandit -r src/          # security audit
uv run pip-audit               # dependency vulnerability check
uv run vulture src/            # unused code detection
uv run radon cc src/ -mi C     # complexity report
```

## Releases & Versioning

Versioning is fully automated with [python-semantic-release](https://python-semantic-release.readthedocs.io/), driven by [Conventional Commits](https://www.conventionalcommits.org/) on `main`:

- `fix: ...` → patch release
- `feat: ...` → minor release
- `feat!: ...` or a `BREAKING CHANGE:` footer → major release

On every push to `main`, CI determines whether a release is warranted, bumps the version in `pyproject.toml`, updates `CHANGELOG.md`, tags the commit, and publishes a GitHub Release with the built package and a `SHA256SUMS` checksum file attached. Commits that don't match the convention (docs, chores, etc.) don't trigger a release.

## License

MIT — see [LICENSE](LICENSE).
