# Configuration reference

Every setting job-scout reads, where it lives, and what it actually defaults to.
Defaults in this document are taken from the Pydantic `Config` model in
`src/job_scout/models.py`, which is the only authority — the dashboard, the CLI
and the YAML files all resolve to it.

- [How configuration resolves](#how-configuration-resolves)
- [Editing configuration](#editing-configuration)
- [Global settings](#global-settings)
- [Per-user settings](#per-user-settings)
- [Secrets](#secrets)
- [Environment variables](#environment-variables)
- [Worked example](#worked-example)

## How configuration resolves

Configuration is one flat model stored in three files. Which file a key belongs
in is decided by the code, not by you: every field is classified as **global**,
**per-user** or **secret**, and writing it anywhere else has no effect.

| Layer | File | Holds |
|---|---|---|
| Model defaults | `src/job_scout/models.py` | The value used when nothing else sets the key |
| Global | `data/config.yaml` | Settings shared by every user: LLM providers, scraping limits, the container scheduler |
| Per-user | `data/users/<name>/config.yaml` | One person's search: profile, commute, thresholds, keywords, notification target |
| Secrets | `data/secrets.yaml` | API keys and the dashboard token |
| Environment | process environment | Overrides secrets, plus a handful of deployment-only variables |

For a run scoped to a user, the effective configuration is built by merging
those layers in order, last writer winning:

```
model defaults  ->  data/config.yaml  ->  data/users/<name>/config.yaml  ->  data/secrets.yaml  ->  environment
```

Two consequences worth knowing:

- **Secrets always win over config files.** A key written into a tracked YAML
  file is ignored in favour of `data/secrets.yaml`, and an environment variable
  beats both.
- **Commands without `--user` read the global config only.** `job-scout config
  show` prints the raw global file; `job-scout config show --user alex` prints
  the merged, effective result.

### Directory layout

```
data/
├── config.yaml                 # global settings
├── secrets.yaml                # API keys and dashboard token (never commit)
├── jobs.db                     # legacy single-user database
├── logs/                       # legacy single-user run logs
└── users/
    ├── alex/
    │   ├── config.yaml         # alex's search settings
    │   ├── jobs.db             # alex's jobs, runs, caches and generated documents
    │   ├── logs/               # one log file per run
    │   └── cv/                 # alex's CV builder profiles (see below)
    └── sam/
        ├── config.yaml
        ├── jobs.db
        ├── logs/
        └── cv/
```

Each user gets their own SQLite database, so two people on one install never
see each other's matches, evaluations or generated documents.

### CV builder data

The CV builder keeps its documents on disk rather than in the database, one
directory per profile, so a profile can be copied or backed up by moving it:

```
data/users/alex/cv/
└── profiles/
    ├── default/
    │   ├── cv.json             # the CVDocument: identity, theme, sections
    │   └── uploads/
    │       └── portrait.png    # squared and downscaled on upload
    └── default-meridiaan-data/ # a tailored copy, saved under its own slug
        ├── cv.json
        └── uploads/
```

`user_cv_dir(name)` returns `data/users/<name>/cv`, and the profile store
derives the `profiles/` level from it. The root moves with
`JOB_SCOUT_DATA_DIR` like everything else, so nothing extra is needed to keep
CVs on a mounted volume — the Docker deployment picks them up at
`/data/users/<name>/cv/` automatically.

**There are no configuration keys for the CV builder.** It has nothing in
`config.yaml`, nothing in `secrets.yaml`, and no environment variable of its
own: the profile it edits is chosen in the editor, the user is chosen by
`--user` or the dashboard's user picker, and tailoring uses the same LLM
provider and per-stage routing as everything else (the `resume_tailoring`
purpose — see [LLM_PROVIDERS.md](LLM_PROVIDERS.md)). The vendored code still
defines a `CV_BUILDER_DATA` override, but no job-scout entry point reaches it:
both the CLI and the dashboard always pass an explicit per-user root. Setting it
does nothing.

The CV builder is also unrelated to the `cv_path` key below. That points at the
CV **PDF** the pipeline parses to score vacancies; the builder's `cv.json`
documents are never read by the pipeline, and editing one does not invalidate a
single cached evaluation. Full detail in [CV_BUILDER.md](CV_BUILDER.md).

### Relocating the data directory

`JOB_SCOUT_DATA_DIR` moves the whole tree above. It is read once at import and
everything — global config, secrets, per-user directories, databases and logs —
hangs off it:

```bash
JOB_SCOUT_DATA_DIR=/srv/job-scout-data uv run job-scout run --all
```

The Docker image and `docker-compose.yml` set it to `/data` and bind-mount
`./data` there. That matters for any path stored *inside* the config: a
`cv_path` of `/home/alex/CV.pdf` is meaningless inside the container and must be
written as its `/data/...` equivalent. See [DEPLOY.md](DEPLOY.md).

Left unset, the default is the relative path `data`, resolved against the
current working directory — so run the CLI from the repository root, or set the
variable.

## Editing configuration

Three routes, all writing the same files.

### The dashboard

`uv run job-scout web`, then edit under **Profile & Filters**, **Keywords**,
**Custom Sites**, **Notifications**, **LLM Settings**, **Secrets** and
**Schedule**. This is the only route that writes secrets without you touching a
file, and the only one that edits structured fields — career tracks and custom
sites — comfortably. See [WEB_DASHBOARD.md](WEB_DASHBOARD.md).

### `job-scout config set`

```bash
# A per-user field
uv run job-scout config set fit_score_threshold 70 --user alex

# A global field: omit --user
uv run job-scout config set llm_provider zai

# Inspect the result (any field whose name contains "key" is masked)
uv run job-scout config show --user alex
```

Values are strings on the command line and are coerced to the field's declared
type:

| Field type | Accepted input | Notes |
|---|---|---|
| `bool` | `true`, `1`, `yes` | Anything else is false |
| `int` / `float` | `70`, `1.5` | A non-numeric value raises and exits 1 |
| `list[str]` | `remote,hybrid` or `["remote","hybrid"]` | Comma-separated, or JSON when a value contains a comma |
| nullable `str` | empty string | Clears the field back to `null`. A nullable `int` or `float` that already holds a number is not clearable this way: the empty string is parsed as a number first and the command exits 1 |

One trap has no guard: an empty string clears *any* field that reaches that branch, so
`config set smtp_host ""` writes `smtp_host: null` and `config set keywords_dutch ""` writes
`null` rather than `[]`. Neither is nullable, so the next command fails Pydantic validation.
Re-set a real value, or edit the YAML by hand, rather than clearing these.

Two refusals are deliberate:

- Passing `--user` with a **global** key fails with *"is a global field and
  cannot be set per-user"*. Omit `--user`.
- Passing a **secret** key fails outright — secrets never go in a config file.
  The error text suggests an env var name built from the key, which is wrong for
  most secrets; use the exact names in the [Secrets](#secrets) table instead.

Structured fields (`career_tracks`, `custom_sites`) are not settable this way.
Use the dashboard, `job-scout sites add`, or edit the YAML.

### Hand-editing YAML

Plain YAML, loaded with `yaml.safe_load` and validated by Pydantic. A malformed
*value* — text where a number belongs — surfaces as a validation error on the
next command. An unknown *key name* does not: the `Config` model does not forbid
extra fields, so a misspelt key is silently ignored and the setting you meant to
change quietly keeps its old value. A hand-edit is worth confirming with
`job-scout config show --user <name>`, which prints what the model actually
parsed. Nothing watches the files: the CLI reads them at start-up, the dashboard
on request, and the container scheduler re-reads its own schedule keys every
cycle.

## Global settings

Stored in `data/config.yaml`. Set them with `job-scout config set <key> <value>`
without `--user`.

### Provider selection and per-stage routing

`llm_provider` is the default backend; the five `*_provider` keys override it
for one pipeline stage each, so a cheap model can screen while a strong one
decides. All six accept `claude_cli`, `zai`, `kilo_cli` or `local`. See
[LLM_PROVIDERS.md](LLM_PROVIDERS.md).

| Key | Default | Description |
|---|---|---|
| `llm_provider` | `local` | Default LLM backend for every stage without an override. Local by default because it needs no API key |
| `screening_provider` | `null` | Override for the batched title screen |
| `quick_eval_provider` | `null` | Override for the cheap quick-eval scoring pass |
| `evaluation_provider` | `null` | Override for the full fit evaluation |
| `keywords_provider` | `null` | Override for keyword generation |
| `cv_parsing_provider` | `null` | Override for CV parsing |

### Local provider (OpenAI-compatible)

| Key | Default | Description |
|---|---|---|
| `local_base_url` | `http://localhost:11434/v1` | Primary OpenAI-compatible endpoint (Ollama, LM Studio, vLLM, llama.cpp) |
| `local_fallback_base_urls` | `[]` | Endpoints tried in order when the primary is unreachable — e.g. a LAN address plus a VPN/tailnet address |
| `local_connect_timeout` | `15.0` | Seconds to establish the TCP connection. Separate from generation timeouts, which legitimately take minutes |
| `local_probe_timeout` | `10.0` | Seconds allowed when probing an endpoint for reachability |
| `local_model` | `llama3.1` | Model for full evaluation and keyword generation |
| `local_screening_model` | `null` | Model for title screening; falls back to `local_model` |
| `local_quick_eval_model` | `null` | Model for quick evaluation; falls back to `local_screening_model`, and then to `local_model` |
| `local_keywords_model` | `null` | Model for keyword generation; falls back to `local_model` |
| `local_evaluation_timeout` | `120.0` | Seconds allowed for one full-evaluation completion |
| `local_screening_timeout` | `90.0` | Seconds allowed for one screening completion |

### Z.AI

| Key | Default | Description |
|---|---|---|
| `zai_base_url` | `https://api.z.ai/api/coding/paas/v4` | OpenAI-compatible API endpoint |
| `zai_model` | `glm-5.1` | Model for full evaluation and keyword generation |
| `zai_screening_model` | `glm-4.5-air` | Model for title screening |
| `zai_quick_eval_model` | `glm-4.5-air` | Model for quick evaluation |
| `zai_screening_batch_size` | `20` | Titles batched into a single screening call (also used by the Kilo provider) |

### Claude CLI and Kilo CLI

| Key | Default | Description |
|---|---|---|
| `claude_evaluation_model` | `null` | Model for full evaluation; `null` lets the `claude` CLI pick its own default |
| `claude_screening_model` | `haiku` | Model for the cheap title-screening pass |
| `kilo_evaluation_model` | `zai/glm-5.1` | Kilo-routed model for full evaluation |
| `kilo_screening_model` | `zai/glm-4.5-air` | Kilo-routed model for title screening |
| `kilo_quick_eval_model` | `zai/glm-4.5-air` | Kilo-routed model for quick evaluation |

### LLM reliability

| Key | Default | Description |
|---|---|---|
| `llm_max_attempts` | `3` | Attempts per LLM call before the retry wrapper gives up |
| `llm_retry_base_delay` | `1.0` | Base backoff in seconds between retries; doubles each attempt |

### Scraping and web search

| Key | Default | Description |
|---|---|---|
| `max_jobs_per_source` | `50` | Upper bound on jobs fetched from each source per run |
| `linkedin_fetch_description` | `true` | Fetch each LinkedIn job's full description with an extra request. Costs one request per job, but title-only evaluation is where the model guesses worst |
| `searxng_url` | `null` | SearXNG instance used for company research, company review, official-source lookup and person search. The keyless alternative to `brave_api_key` |

### Notification transport

Per-user notification settings — which channel, which topic, which address —
live in the [per-user table](#notifications). These four are global because
they describe shared infrastructure — one ntfy server, one SMTP relay — rather
than one person's search.

| Key | Default | Description |
|---|---|---|
| `ntfy_server` | `https://ntfy.sh` | ntfy server base URL that push notifications are posted to |
| `smtp_host` | `""` | SMTP relay hostname. Empty string, not null |
| `smtp_port` | `587` | SMTP relay port |
| `smtp_from` | `""` | Envelope/From sender address. Empty string, not null |

### Container scheduler and wake-on-LAN

These drive `job-scout schedule loop`, the entrypoint the Docker `scheduler`
service runs. They are re-read on every cycle, so a change from the dashboard
applies without a restart. They are distinct from the per-user
`schedule_hour`/`schedule_minute`/`schedule_days` keys, which drive a host
crontab instead.

| Key | Default | Description |
|---|---|---|
| `schedule_enabled` | `true` | Master switch for the scheduler loop |
| `schedule_slots` | `tue:17:00,sat:03:00` | Comma-separated `day:HH:MM` entries to fire on. An unparseable value pauses the loop rather than crashing it |
| `schedule_timezone` | `Europe/Amsterdam` | IANA timezone the slots are interpreted in, DST included |
| `wake_mac` | `null` | MAC address of the machine hosting the model server, woken before each scheduled run. Empty disables waking |
| `wake_broadcast` | `255.255.255.255` | Broadcast address for the magic packet |
| `llm_health_url` | `null` | Readiness URL polled after the wake packet. Waking is attempted only when both this and `wake_mac` are set |
| `wake_timeout_seconds` | `300.0` | Seconds to wait for the readiness URL before running anyway, with a warning |

Wake-on-LAN needs host networking — a magic packet is a layer-2 broadcast and
does not survive Docker's bridge NAT. On a Docker Desktop install it cannot
work; leave `wake_mac` empty there.

## Per-user settings

Stored in `data/users/<name>/config.yaml`. Set them with `job-scout config set
<key> <value> --user <name>`.

### Identity and profile

| Key | Default | Description |
|---|---|---|
| `name` | `""` | The user's name. Overwritten with the directory name when the user is created |
| `profile_description` | `""` | Free-text professional profile the LLM scores every vacancy against |
| `negative_description` | `""` | Free-text description of roles or skills that should disqualify a vacancy |
| `career_tracks` | `[]` | Optional multi-track search. Empty means single-profile mode, using the two fields above unchanged |
| `cv_path` | `null` | Path to the CV PDF parsed into a structured profile. Must be container-visible when running under Docker |
| `cv_notes` | `""` | Free-text corrections and additions layered on top of the parsed CV |
| `memory_auto_capture` | `true` | After a letter or interview set is written, turn the notes you typed for it into [memories](MEMORIES.md). One model call per new notes text; the switch in the dashboard's Memories tab and `job-scout memory auto-capture off` do the same as setting it to `false` |
| `language_preferences` | `["nl", "en"]` | Languages the user will accept listings in |
| `linkedin_profile_url` | `null` | Used to detect a stale open-ended "current" role in the CV |
| `linkedin_import_allow_url_fetch` | `false` | Permit the LinkedIn importer to fetch a profile URL rather than only reading a supplied export. `job-scout profile import-linkedin --url` also requires `--allow-fetch` |

A `career_tracks` entry has `id`, `name`, `description`, `negative_description`,
`keywords_dutch`, `keywords_english`, `mode` (`standalone` or `blend`),
`required` and `enabled`. Standalone tracks are searched as their own job;
blend tracks are never searched alone but folded into every standalone track —
the right shape for "some coding and AI-tool building" which is a flavour you
want inside a role rather than a role in itself.

### Commute and travel

| Key | Default | Description |
|---|---|---|
| `home_address` | `""` | Address geocoded as the origin for every commute calculation |
| `max_travel_car` | `30` | Maximum acceptable car commute, minutes |
| `max_travel_pt` | `60` | Maximum acceptable public-transport commute, minutes. Needs `ns_api_key` |
| `max_travel_bike` | `45` | Maximum acceptable bike commute, minutes. Estimated from distance unless `ors_api_key` is set |
| `max_distance_km` | `null` | Straight-line distance ceiling in km. Needs no routing API key |
| `allow_unknown_location` | `false` | Whether a job whose location cannot be resolved still passes the travel filter. `false` honours the commute limits even for unlocatable listings |
| `geocode_cache_days` | `90` | How long a geocoded address stays valid before re-lookup |
| `travel_cache_days` | `14` | How long a computed travel time stays valid before re-lookup |

Only public transport strictly needs a key. Car times come from the free public
OSRM server, falling back to OpenRouteService when `ors_api_key` is set and OSRM
fails. Bike times use OpenRouteService cycling routing when that key is set and
are otherwise estimated from straight-line distance, because the free OSRM bike
profile returns car times. `max_distance_km` needs nothing at all, which makes
it the sensible first filter on a keyless install.

### Scoring and evaluation

| Key | Default | Description |
|---|---|---|
| `fit_score_threshold` | `60` | Minimum full-evaluation fit score (0-100) for a job to count as a match |
| `quick_eval_threshold` | `40` | Minimum quick-eval score needed to proceed to full evaluation |
| `title_include_keywords` | `[]` | A title must contain at least one of these to survive the cheap title filter |
| `title_exclude_keywords` | `[]` | Any title containing one of these is dropped before the LLM sees it |
| `max_parallel_evaluations` | `5` | Maximum concurrent worker threads for quick-eval, evaluation and travel lookups |
| `local_reasoning_purposes` | `["evaluation", "cv_parsing", "resume_tailoring", "cover_letter"]` | Call purposes that keep a reasoning model's thinking block on. Every other purpose runs with it off |
| `local_max_tokens_reasoning` | `8000` | Completion-token ceiling for calls that keep reasoning on (minimum 256) |
| `local_max_tokens_direct` | `1200` | Completion-token ceiling for calls with reasoning off (minimum 64) |

Both keyword lists are normally generated for you by `job-scout keywords
refresh`, which writes all four keyword fields at once.

### Compensation

| Key | Default | Description |
|---|---|---|
| `min_salary` | `null` | Minimum gross salary a listing must offer to be kept |
| `max_salary` | `null` | Maximum gross salary above which a listing is filtered out |
| `min_vacation_days` | `null` | Minimum annual vacation days required |

The model extracts these from the posting and a conservative Dutch-aware regex
re-scans the full text as a backstop. A listing whose pay is genuinely unknown
is not rejected — only one that states a figure outside your range.

### Notifications

| Key | Default | Description |
|---|---|---|
| `notification_channel` | `ntfy` | Delivery backend: `ntfy`, `email`, `slack` or `discord` |
| `notification_mode` | `per_job` | `per_job` sends one notification per match; `digest` sends a single per-run summary |
| `ntfy_topic` | `job-scout-alerts` | Topic the user's alerts are published to |
| `slack_webhook_url` | `""` | Slack incoming-webhook URL, used when the channel is `slack` |
| `discord_webhook_url` | `""` | Discord webhook URL, used when the channel is `discord` |
| `smtp_to` | `""` | Recipient address for email notifications. Per-user, unlike the shared relay settings |

An ntfy topic is a public namespace on a public server: anyone who guesses it
reads your matches. Pick something unguessable. Details and per-channel
formatting are in [NOTIFICATIONS.md](NOTIFICATIONS.md).

### Sources and keywords

| Key | Default | Description |
|---|---|---|
| `keywords_dutch` | `[]` | Dutch search keywords fed to the scrapers. Normally LLM-generated |
| `keywords_english` | `[]` | English search keywords fed to the scrapers. Normally LLM-generated |
| `jobspy_sites` | `["indeed", "linkedin"]` | Boards scraped via jobspy. Validated against `linkedin`, `indeed`, `zip_recruiter`, `glassdoor`, `google`, `bayt`, `naukri`, `bdjobs` |
| `jobspy_keyword_limit` | `5` | How many keywords jobspy searches, taken from the Dutch and English lists interleaved (1-20) |
| `nvb_keyword_limit` | `3` | How many keywords Nationale Vacaturebank searches, Dutch only (1-20) |
| `custom_sites` | `[]` | Extra listing URLs scraped and LLM-extracted |

Nationalevacaturebank is always scraped and has no on/off key — only a keyword
budget. With several career tracks, keywords are unioned and interleaved so the
per-source budgets still reach every track rather than being consumed by the
first one.

Each `custom_sites` entry is `{name, url, enabled, render_js}`; `render_js`
renders the page with Playwright first, which needs the `browser` extra. Manage
them with `job-scout sites add`, `job-scout sites list` and `job-scout sites
remove` rather than by hand.

### Listing freshness and enrichment

| Key | Default | Description |
|---|---|---|
| `verify_matches_open` | `true` | Re-check a matched vacancy is still open before notifying about it |
| `prune_enabled` | `false` | Auto-mark vacancies detected as filled or gone as `EXPIRED` at the start of a run |
| `prune_use_browser` | `false` | Let the pruner render pages in a browser when checking. Also set by `job-scout prune --browser` |
| `prune_use_llm` | `false` | Let the LLM judge whether an ambiguous page still shows an open vacancy |
| `find_official_sources` | `true` | Look up the employer's own posting URL for an aggregator listing, filling `official_url` and `official_available` |
| `company_review_enabled` | `true` | Synthesise a cached work-quality review from public signals for matched companies |

### Scheduling (host crontab)

These drive the crontab entry written by `job-scout schedule install`. If you
run the Docker deployment, ignore them and use the global `schedule_slots`
instead.

| Key | Default | Description |
|---|---|---|
| `schedule_hour` | `8` | Hour of the cron entry (0-23) |
| `schedule_minute` | `0` | Minute of the cron entry (0-59) |
| `schedule_days` | `1-5` | Cron day-of-week. Validated against `*`, `0-6`, `1-5`, `0,6` and single digits `0`-`6` |

`schedule_paused` (default `false`) is the exception to the paragraph above: it
is not crontab-only. It is checked at the top of every run for that user, so it
suspends the host cron entry, the container `schedule loop` and a manual
`job-scout run --user <name>` alike — the run logs that the schedule is paused
and exits before anything is scraped. Clear it to resume; nothing else is
removed while it is set.

### MCP

| Key | Default | Description |
|---|---|---|
| `mcp_enabled` | `false` | Read and written only by the dashboard's MCP endpoint |
| `mcp_port` | `5000` | Read and written only by the dashboard's MCP endpoint |

Both keys are inert for the server that actually runs: `job-scout mcp start`
takes its own `--host` and `--port` and never consults them.

## Secrets

> **Never put a secret in `data/config.yaml` or `data/users/<name>/config.yaml`.**
> Secrets belong in `data/secrets.yaml` or the environment, and `job-scout
> config set` refuses secret keys outright to keep them out of tracked files.
> `data/` is gitignored in full, but add `secrets.yaml` to your own ignore rules
> if you ever relocate it with `JOB_SCOUT_DATA_DIR`.

| Key | Environment variable | Purpose | Where to get it |
|---|---|---|---|
| `zai_api_key` | `JOB_SCOUT_ZAI_API_KEY` | Authenticates the Z.AI (GLM) hosted provider | Your Z.AI account |
| `ors_api_key` | `JOB_SCOUT_ORS_API_KEY` | Real cycling routing, and a car-routing fallback, via OpenRouteService | openrouteservice.org, free developer tier |
| `ns_api_key` | `JOB_SCOUT_NS_API_KEY` | Public-transport travel times via the NS Journey Planner | The NS API portal |
| `local_api_key` | `JOB_SCOUT_LOCAL_API_KEY` | Bearer token for a local or LAN OpenAI-compatible server | Your own server; usually not required |
| `dashboard_token` | `JOB_SCOUT_DASHBOARD_TOKEN` | Shared secret required as a Bearer token on dashboard API calls | Invent one; generate a long random string |
| `smtp_username` | `JOB_SCOUT_SMTP_USERNAME` | Username for an SMTP relay that requires authentication | Your mail provider |
| `smtp_password` | `JOB_SCOUT_SMTP_PASSWORD` | Password for that relay | Your mail provider |
| `brave_api_key` | *(none)* | Brave Search API key for company research, company review, official-source lookup and person search | A Brave Search API account |

Two caveats:

- **`brave_api_key` has no environment variable.** It is the one secret missing
  from the env overlay, so it can only be supplied through
  `data/secrets.yaml`. `JOB_SCOUT_BRAVE_API_KEY` does nothing. Setting
  `searxng_url` instead gives the same web-search capability with no key at all.
- **Leaving `dashboard_token` unset leaves the dashboard unauthenticated.**
  There is no login page and no default password. Never expose it to the
  internet without a token; see [WEB_DASHBOARD.md](WEB_DASHBOARD.md).

`data/secrets.yaml` is plain YAML:

```yaml
ors_api_key: "your-openrouteservice-key"
ns_api_key: "your-ns-key"
dashboard_token: "a-long-random-string"
```

## Environment variables

| Variable | Overrides | Notes |
|---|---|---|
| `JOB_SCOUT_DATA_DIR` | the `data/` root | Relocates config, secrets, per-user directories, databases and logs. Default `data`; the Docker image sets `/data` |
| `JOB_SCOUT_ZAI_API_KEY` | `zai_api_key` | |
| `JOB_SCOUT_ORS_API_KEY` | `ors_api_key` | |
| `JOB_SCOUT_NS_API_KEY` | `ns_api_key` | |
| `JOB_SCOUT_LOCAL_API_KEY` | `local_api_key` | |
| `JOB_SCOUT_DASHBOARD_TOKEN` | `dashboard_token` | Passed through by `docker-compose.yml` from `.env` |
| `JOB_SCOUT_SMTP_USERNAME` | `smtp_username` | |
| `JOB_SCOUT_SMTP_PASSWORD` | `smtp_password` | |
| `JOB_SCOUT_WAKE_MAC` | `wake_mac` | Default for `job-scout wake --mac` and `schedule loop --wake-mac` |
| `JOB_SCOUT_WAKE_BROADCAST` | `wake_broadcast` | Default for `job-scout wake --broadcast` and `schedule loop --wake-broadcast` |
| `JOB_SCOUT_LLM_HEALTH_URL` | `llm_health_url` | Default for `job-scout wake --url` and `schedule loop --llm-health-url` |
| `JOB_SCOUT_SCHEDULE` | `schedule_slots` | Default for `schedule loop --slots` |
| `JOB_SCOUT_TIMEZONE` | `schedule_timezone` | Default for `schedule loop --timezone` |
| `JOB_SCOUT_PORT` | *(deployment only)* | Read by `docker-compose.yml` to pass `web --port`. Default `24817`. Not read by the application — `job-scout web` itself defaults to port `8000` |
| `JOB_SCOUT_TZ` | *(deployment only)* | Read by `docker-compose.yml` to set the container's `TZ`. Default `Europe/Amsterdam` |

The five scheduler and wake variables are worth leaving unset on a container
deployment. They take precedence over `data/config.yaml`, which means the
dashboard's Schedule tab can no longer change those values once they are set.

## Worked example

A realistic `data/users/alex/config.yaml` for someone hunting quality
engineering roles around Utrecht, running two career tracks and a blend:

```yaml
name: alex
profile_description: >
  Quality engineer with eight years in regulated manufacturing. Strong on
  measurement systems, root-cause analysis and supplier audits. Comfortable
  leading improvement projects across production and R&D.
negative_description: >
  Not interested in pure sales, call-centre QA, software-only test automation,
  or roles that are mainly ISO paperwork with no shop-floor involvement.

career_tracks:
  - id: quality
    name: Quality engineering
    description: Quality or reliability engineering in manufacturing.
    negative_description: Not software QA.
    keywords_dutch: [kwaliteitsingenieur, quality engineer, kwaliteitsmanager]
    keywords_english: [quality engineer, reliability engineer]
    mode: standalone
    enabled: true
  - id: rnd
    name: R&D and process development
    description: Process development or industrialisation in a technical company.
    keywords_dutch: [procesontwikkelaar, R&D engineer]
    keywords_english: [process engineer, development engineer]
    mode: standalone
    enabled: true
  - id: data
    name: Data and tooling
    description: Roles with room to build data tooling and automation.
    mode: blend
    required: false
    enabled: true

cv_path: /data/users/alex/cv.pdf
cv_notes: |
  Left the 2019-2021 contract role off the CV; mention it if relevant.
  Dutch is native, German is conversational.
language_preferences: [nl, en]

home_address: Oudegracht 1, Utrecht
max_travel_car: 45
max_travel_pt: 60
max_travel_bike: 30
max_distance_km: 60
allow_unknown_location: false

fit_score_threshold: 65
quick_eval_threshold: 45
title_exclude_keywords: [stage, werkstudent, uitzend, traineeship]
max_parallel_evaluations: 4

min_salary: 65000
min_vacation_days: 25

notification_channel: ntfy
notification_mode: digest
ntfy_topic: job-scout-alex-7f3c91d2

jobspy_sites: [indeed, linkedin]
jobspy_keyword_limit: 6
custom_sites:
  - name: ASML careers
    url: https://www.asml.com/en/careers/find-your-job
    enabled: true
    render_js: true

verify_matches_open: true
prune_enabled: true
find_official_sources: true
company_review_enabled: true
```

The matching `data/config.yaml` stays short, because global settings describe
the machine rather than the search:

```yaml
llm_provider: local
local_base_url: http://192.168.1.50:11434/v1
local_model: qwen3:8b
local_fallback_base_urls:
  - http://100.84.12.9:11434/v1
screening_provider: local
local_screening_model: qwen3:4b
max_jobs_per_source: 60
searxng_url: http://192.168.1.50:8888
schedule_slots: tue:17:00,sat:03:00
schedule_timezone: Europe/Amsterdam
wake_mac: 00:00:5E:00:53:00
llm_health_url: http://192.168.1.50:11434/v1/models
```

## See also

- [USAGE.md](USAGE.md) — the CLI reference these settings drive
- [LLM_PROVIDERS.md](LLM_PROVIDERS.md) — choosing and routing backends
- [NOTIFICATIONS.md](NOTIFICATIONS.md) — channels, modes and retry
- [WEB_DASHBOARD.md](WEB_DASHBOARD.md) — editing all of this in a browser
- [CV_BUILDER.md](CV_BUILDER.md) — the CV profiles stored beside these files
- [DEPLOY.md](DEPLOY.md) — how configuration is split under Docker
