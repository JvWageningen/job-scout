# Architecture

## System Overview

job-scout is a CLI pipeline that automates the daily job search workflow: scrape, evaluate, filter, and notify. It runs as a single-threaded Python process orchestrated by a Click CLI, with all state persisted to a local SQLite database and YAML config file.

```text
┌─────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────┐
│  Scraper    │────▶│  Database    │────▶│  Evaluator   │────▶│ Notifier │
│ (Indeed,    │     │ (dedup +     │     │ (Claude CLI  │     │ (ntfy.sh │
│  LinkedIn,  │     │  persist)    │     │  fit + neg)  │     │  push)   │
│  NVB)       │     │              │     │              │     │          │
└─────────────┘     └──────────────┘     └──────┬───────┘     └──────────┘
                                                │
                                         ┌──────▼───────┐
                                         │ Travel Filter│
                                         │ (ORS, NS,    │
                                         │  Nominatim)  │
                                         └──────────────┘
```

## Data Flow

A single `job-scout run` executes these stages in order:

1. **Scrape** — `scraper.py` fetches jobs from Indeed.nl/LinkedIn (via `python-jobspy` library) and Nationalevacaturebank.nl (via direct HTML scraping with BeautifulSoup). Results are deduplicated within the batch by URL.

2. **Deduplicate** — `database.py` checks each job against the SQLite store by URL or normalized title+company pair. Previously seen jobs are skipped.

3. **Evaluate** — `evaluator.py` shells out to the Claude Code CLI (`claude --print`) with structured prompts. Each new job gets two evaluations:
   - **Fit evaluation**: scores 0-100 how well the job matches the user's profile and CV
   - **Negative evaluation**: checks if the job matches exclusion criteria

4. **Travel filter** — `travel.py` geocodes the job location via Nominatim OSM, then queries OpenRouteService (car/bike) and the NS Journey Planner API (public transport) for travel times from the user's home address. Jobs exceeding all configured travel limits are rejected. Remote/vague locations bypass this filter.

5. **Notify** — `notifier.py` sends HTTP POST requests to ntfy.sh for each matched job. Failed notifications are marked as pending for retry on the next run.

6. **Persist** — All jobs (matched and rejected) are saved to SQLite with their scores, reasoning, and travel data.

## Module Descriptions

### cli.py — Orchestration

The Click-based CLI is the sole entry point. It defines commands (`init`, `run`, `keywords refresh`, `jobs list/rejected`, `config show/set`, `schedule install/status`) and wires together all other modules. The `run` command implements the full pipeline described above. Helper functions handle logging setup, job processing, and summary output.

### config.py — Configuration

Manages a YAML config file at `~/.local/share/job-scout/config.yaml`. The data directory is overridable via `JOB_SCOUT_DATA_DIR`. Provides type coercion for CLI `config set` commands (strings to int, bool, or comma-separated lists).

### models.py — Data Models

Pydantic models define the schema for all structured data. `JobListing` is the central model, carrying a job through every pipeline stage. `Config` holds all user preferences. Evaluation results (`FitEvaluation`, `NegativeEvaluation`) and `TravelTime` are value objects attached to jobs during processing.

### database.py — Storage

A single `jobs` table in SQLite stores all seen jobs. Deduplication uses a unique constraint on URL and a secondary check on normalized title+company. Travel times are serialized as JSON. The database supports notification retry tracking via `notification_pending` status.

### evaluator.py — AI Evaluation

Invokes `claude --print` as a subprocess with structured prompts that include the user's profile, CV text, and negative description. Output is parsed as JSON (with markdown fence stripping). A 90-second timeout prevents hangs. The evaluator produces two independent assessments per job.

### scraper.py — Job Sources

Two scraping strategies:
- **python-jobspy**: library-based scraping for Indeed.nl and LinkedIn with built-in pagination
- **Direct HTML**: BeautifulSoup parsing of Nationalevacaturebank.nl search results

Random delays (2-5s) between requests reduce rate-limiting risk. Each source returns normalized `JobListing` objects.

### travel.py — Travel Calculation

Three external APIs:
- **Nominatim** (OpenStreetMap): geocoding addresses to coordinates
- **OpenRouteService**: driving and cycling route durations
- **NS Journey Planner**: Dutch public transport journey times

Remote/vague locations (containing "remote", "thuiswerken", "nederland", etc.) skip geocoding entirely and always pass filters.

### notifier.py — Notifications

Sends push notifications via ntfy.sh HTTP API. Notifications include job title, company, source, fit score, and a concise travel summary. The travel summary prefers public transport, then bike, then car. Failed sends are tracked for retry.

### cv_parser.py — CV Extraction

Extracts text from PDF files using PyPDF2. Multi-page PDFs are joined with newlines. Returns empty string on parse errors rather than crashing.

### scheduler.py — Cron Management

Installs/removes cron entries for daily automated runs. Managed entries are tagged with a `# job-scout-managed` marker comment. Existing unrelated cron entries are preserved.

## Key Design Decisions

- **Claude CLI over API**: Uses the local `claude` CLI binary rather than the Anthropic API directly. This avoids API key management and leverages the user's existing Claude Code authentication.

- **SQLite for persistence**: A single-file database requires no external services and supports the deduplication and notification retry requirements without complexity.

- **Fail-open travel filter**: When travel APIs are unavailable or locations can't be geocoded, jobs pass through rather than being silently rejected. This prevents false negatives.

- **Per-job evaluation**: Each job is evaluated independently rather than in batches. This keeps prompts focused and avoids context-length issues with large job batches.

- **Notification retry**: Failed ntfy.sh deliveries are marked as pending and retried on the next run, ensuring transient network issues don't cause missed notifications.
