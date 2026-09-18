# Usage

job-scout is driven entirely from one console script, `job-scout`, installed by the
project's entry point. Locally you invoke it through uv (`uv run job-scout ...`); inside
the container the image's entrypoint is already `job-scout`, so you pass bare arguments
(`docker compose exec scheduler job-scout run --all`). The surface is one root group, eleven
subgroups and 42 leaf commands: a handful you will use every week (`run`, `jobs list`,
`config set`) and a long tail that exists for the moments you need it — pruning dead
vacancies, tailoring a resume to a specific posting, waking a sleeping GPU host before a
scheduled sweep. This page documents all of them.

Every command that touches per-user state accepts `--user NAME`. Read
[Multi-user management](#multi-user-management) once before you rely on it, because the
flag does not behave identically everywhere.

- Config keys and defaults referenced here are described in full in [CONFIGURATION.md](CONFIGURATION.md).
- Provider and per-stage model routing: [LLM_PROVIDERS.md](LLM_PROVIDERS.md).
- Notification channels and modes: [NOTIFICATIONS.md](NOTIFICATIONS.md).
- The CV editor behind the `cv` commands, and which PDF to send where: [CV_BUILDER.md](CV_BUILDER.md).
- The dashboard, which exposes much (not all) of this surface as a UI: [WEB_DASHBOARD.md](WEB_DASHBOARD.md).
- Running it as a container: [DEPLOY.md](DEPLOY.md).

## Root options

```bash
uv run job-scout --verbose run --user alex
```

The root group's only job is to configure logging and dispatch.

| Option | Default | Description |
|---|---|---|
| `-v`, `--verbose` | off | Set the loguru console sink to `DEBUG` instead of `INFO` |

The flag goes **before** the subcommand. Per-run log files are written under the user's
logs directory regardless of this setting; `-v` only affects what reaches your terminal.

---

## Command index

| Command | Purpose |
|---|---|
| [`init`](#init) | Interactive first-time setup for a user |
| [`keywords refresh`](#keywords-refresh) | Regenerate search and title keywords from the profile and CV |
| [`config show`](#config-show) | Print the effective configuration |
| [`config set`](#config-set) | Change one configuration key |
| [`sites add`](#sites-add) | Track a company careers page as an extra source |
| [`sites list`](#sites-list) | List tracked custom sites |
| [`sites remove`](#sites-remove) | Stop tracking a custom site |
| [`run`](#run) | Execute the full pipeline |
| [`jobs list`](#jobs-list) | Show recent matches with scores and commute times |
| [`jobs rejected`](#jobs-rejected) | Show recent rejections and why they were rejected |
| [`jobs update-status`](#jobs-update-status) | Move a job through its lifecycle |
| [`jobs export`](#jobs-export) | Dump jobs to CSV or JSON |
| [`runs history`](#runs-history) | Table of past runs and their counts |
| [`approval queue`](#approval-queue) | List jobs awaiting approval |
| [`approval approve`](#approval-approve) | Approve a job for application |
| [`prune`](#prune) | Mark filled or closed vacancies as expired |
| [`find-sources`](#find-sources) | Find the employer's own posting for each match |
| [`company-review`](#company-review) | Work-quality review of a named company |
| [`company research`](#company-research) | Research a job's employer and suggest hiring managers |
| [`company view`](#company-view) | Re-print saved company research |
| [`profile cv-summary`](#profile-cv-summary) | Show the parsed structured CV profile |
| [`profile import-linkedin`](#profile-import-linkedin) | Merge LinkedIn data into the CV profile |
| [`profile search-person`](#profile-search-person) | Search the public web to fill profile gaps |
| [`profile tailor-resume`](#profile-tailor-resume) | Rewrite the resume against one job |
| [`profile get-resume`](#profile-get-resume) | Retrieve or render a stored tailored resume |
| [`profile generate-cover-letter`](#profile-generate-cover-letter) | Draft a cover letter for one job |
| [`profile get-cover-letter`](#profile-get-cover-letter) | Print a stored cover letter |
| [`profile answer-screening`](#profile-answer-screening) | Pre-answer a posting's screening questions |
| [`profile get-answers`](#profile-get-answers) | Print stored screening answers |
| [`profile star-story`](#profile-star-story) | Manage the STAR story bank |
| [`profile interview-prep`](#profile-interview-prep) | Match likely questions to your STAR stories |
| [`interview questions`](#interview-questions) | Write the questions to ask the employer |
| [`interview answers`](#interview-answers) | Predict their questions and draft your answers |
| [`interview export`](#interview-export) | Write a saved interview set to a Word or text file |
| [`cv list`](#cv-list) | List the stored CV profiles |
| [`cv serve`](#cv-serve) | Run the CV editor on its own |
| [`cv render`](#cv-render) | Render a stored CV profile to PDF |
| [`cv tailor`](#cv-tailor) | Tailor a stored CV to one vacancy |
| [`schedule loop`](#schedule-loop) | Container-native weekly scheduler |
| [`schedule install`](#schedule-install) | Install a host crontab entry |
| [`schedule status`](#schedule-status) | Show the installed crontab entry |
| [`schedule remove`](#schedule-remove) | Remove the crontab entry |
| [`wake`](#wake) | Wake the LLM host by Wake-on-LAN and wait for it |
| [`web`](#web) | Start the dashboard |
| [`mcp start`](#mcp-start) | Start the MCP server |

---

## Setup

### `init`

```bash
uv run job-scout init --user alex
```

Walks you through first-time setup interactively: profile description, negative
description, CV path, CV notes, maximum distance, minimum and maximum salary, minimum
vacation days, ntfy topic, and the OpenRouteService and NS API keys. Configuration is
written to that user's `config.yaml`; the two API keys go to `data/secrets.yaml`, which is
never committed. It then offers to generate keywords for you, which runs the same work as
[`keywords refresh`](#keywords-refresh).

| Option | Default | Description |
|---|---|---|
| `--user TEXT` | none | User name to initialise |

Without `--user` it writes the legacy global `data/config.yaml` instead of a per-user
config. On a multi-user install that is almost never what you want. The literal name
`all` is rejected as reserved, because `--all` already means "every user".

### `keywords refresh`

```bash
uv run job-scout keywords refresh --user alex
```

Regenerates four lists from the profile description plus the CV text using the configured
LLM — Dutch keywords, English keywords, title-include keywords and title-exclude keywords
— saves them to the user's config (or the global config without `--user`) and echoes all
four so you can see what changed.

| Option | Default | Description |
|---|---|---|
| `--user TEXT` | none | User to refresh keywords for |

Run this whenever `profile_description`, `negative_description`, `career_tracks` or the CV
itself changes. The keyword lists are what the scrapers actually search for, so a stale
set quietly narrows every subsequent run.

### `config show`

```bash
uv run job-scout config show --user alex
```

Dumps every configuration field as `key: value`. Any field whose name contains `key` is
masked down to `***` plus its last four characters, so the output is safe to paste into an
issue. With `--user` you get the merged effective configuration (global overlaid with that
user's); without it, the raw global configuration.

| Option | Default | Description |
|---|---|---|
| `--user TEXT` | none | Show the effective config for this user |

### `config set`

```bash
uv run job-scout config set fit_score_threshold 60 --user alex
```

Writes one key. With `--user` the value lands in that user's config; without it, in the
global config. Values are validated, and a `ValueError`/`TypeError` from validation exits
1 with the message.

| Option | Default | Description |
|---|---|---|
| `--user TEXT` | none | User to apply the change to |

Two things worth knowing. Secret fields are refused here by design — put API keys in
`data/secrets.yaml` or, where one exists, their environment variable; `brave_api_key` has
none and can only come from the file, as the [Secrets table](CONFIGURATION.md#secrets)
notes. And the two ways of misrouting a key fail differently: a global-only key set with
`--user` is refused outright and exits 1 with a named error, while a per-user key set
without `--user` is written silently to `data/config.yaml`, where it becomes a default for
every user instead of a setting for one. The scope of every key is listed in
[CONFIGURATION.md](CONFIGURATION.md).

### `sites add`

```bash
uv run job-scout sites add https://careers.example.com/jobs --name "Example Corp" --user alex --render-js
```

Appends a custom listing page to the user's `custom_sites`. The page is fetched on every
run and the listings are extracted by the LLM, so any careers page becomes a source
without writing a scraper. The name defaults to the URL's hostname, and adding a URL that
is already tracked is a silent no-op.

| Option | Default | Description |
|---|---|---|
| `--name TEXT` | URL hostname | Label for the site |
| `--user TEXT` | none | User to configure |
| `--render-js` | off | Render the page with a browser before extracting |

Use `--render-js` only when the plain HTML comes back empty — it is considerably slower
and needs the optional `browser` extra installed.

### `sites list`

```bash
uv run job-scout sites list --user alex
```

Lists the user's custom sites as `[enabled|disabled] name: url`, with a trailing `[JS]`
marker on sites configured for JavaScript rendering.

| Option | Default | Description |
|---|---|---|
| `--user TEXT` | none | User to show |

### `sites remove`

```bash
uv run job-scout sites remove "Example Corp" --user alex
```

Removes every custom site whose **URL or name** equals the identifier, then saves the list
back. Prints a not-found message when nothing matched.

| Option | Default | Description |
|---|---|---|
| `--user TEXT` | none | User to configure |

---

## Running searches

### `run`

```bash
uv run job-scout run --user alex
```

The main event. One invocation executes the whole pipeline, in order: invalidate cached
evaluations whose search profile fingerprint changed, auto-prune active vacancies (only if
`prune_enabled`), scrape every source in parallel, deduplicate, apply the rule-based title
filter, run the batched LLM title screen, filter by commute, quick-score the survivors,
fully evaluate what is left, apply the compensation filter, verify each match is still
open, enrich matches with the employer's own posting URL and a company review, and notify.
Run statistics and per-stage timings are saved to run history at the end.

A log file is written under the user's logs directory for every run, whether or not it is a
dry run.

| Option | Default | Description |
|---|---|---|
| `--user TEXT` | none | Run for one specific user |
| `--all` | off | Run for every configured user |
| `--dry-run` | off | Evaluate without saving results or sending notifications |
| `--full` | off | Re-scrape and re-notify everything, bypassing deduplication |

**`--dry-run`** still scrapes and still calls the LLM — it is not free, it simply does not
persist or notify. Use it to sanity-check a changed profile before it starts pushing
alerts at you.

**`--full`** skips the database duplicate check, upserts rows rather than inserting, and
re-notifies every match. It is the right tool after a significant profile change, and the
wrong tool on a normal schedule: expect a burst of notifications for jobs you have already
seen.

```bash
# Nightly-style sweep for everyone (this is what the container scheduler invokes)
uv run job-scout run --all

# Rebuild one user's results from scratch after changing their profile
uv run job-scout run --user alex --full
```

Cost control lives in configuration, not flags: `max_jobs_per_source` (default `50`) caps
the scrape, `quick_eval_threshold` (default `40`) decides how much reaches full
evaluation, `fit_score_threshold` (default `60`) decides what counts as a match, and
`max_parallel_evaluations` (default `5`, per-user) sets the worker count.

---

## Results and history

### `jobs list`

```bash
uv run job-scout jobs list --limit 50 --user alex
```

Prints recent matched jobs: title, company, fit score and the model's reasoning, salary,
compensation notes, vacation days, location and distance, travel minutes per mode, the URL
and when it was first seen.

| Option | Default | Description |
|---|---|---|
| `--limit INTEGER` | `20` | Maximum results to show |
| `--user TEXT` | none | User whose jobs to show |

### `jobs rejected`

```bash
uv run job-scout jobs rejected --limit 20 --user alex
```

The other half of the picture: recently rejected jobs, each with a derived reason —
negative-description match, fit score below threshold, compensation, or a generic
travel/salary/vacation fallback. This is the fastest way to diagnose a profile that is too
narrow or a `title_exclude_keywords` entry that is catching more than you meant.

| Option | Default | Description |
|---|---|---|
| `--limit INTEGER` | `20` | Maximum results to show |
| `--user TEXT` | none | User whose jobs to show |

### `jobs update-status`

```bash
uv run job-scout jobs update-status 42 approved --notes "phone screen booked" --user alex
```

Moves one job to a new lifecycle status through the database's transition validation,
optionally attaching a note.

| Argument / option | Default | Description |
|---|---|---|
| `JOB_ID` | required | Numeric job id, as shown by `jobs list` |
| `STATUS` | required | One of `new`, `viewed`, `approved`, `ready`, `submitted`, `interviewing`, `offer`, `rejected`, `expired`, `matched` |
| `--notes TEXT` | none | Notes to record with the status change |
| `--user TEXT` | none | User whose job to update |

`matched` is the legacy status and `expired` is what auto-pruning sets; both are accepted
here because the choices are taken live from the status enum. Note that an invalid
transition or a missing job prints a failure message but still exits 0 — check the output
rather than the exit code when scripting this.

### `jobs export`

```bash
uv run job-scout jobs export --format csv --status matched --output jobs.csv --user alex
```

Dumps jobs through the exporter to CSV or JSON, either to a file or to stdout.

| Option | Default | Description |
|---|---|---|
| `--format [csv\|json]` | `json` | Output format |
| `--status [all\|matched\|rejected]` | `all` | Which jobs to include |
| `--output PATH` | stdout | File to write to |
| `--user TEXT` | none | User whose jobs to export |

The filtered variants (`matched`, `rejected`) are capped at 10 000 rows.

### `runs history`

```bash
uv run job-scout runs history --user alex --limit 50
```

A fixed-width table of past runs: start timestamp, jobs scraped, matched, rejected,
notified, error count and duration in seconds. Useful for spotting the run where the
scrape count collapsed — usually a keyword list or a source that broke.

| Option | Default | Description |
|---|---|---|
| `--limit INTEGER` | `30` | Maximum runs to show |
| `--user TEXT` | none | User whose runs to show |

### `approval queue`

```bash
uv run job-scout approval queue --user alex
```

Lists the jobs awaiting approval with an index, `title @ company`, status, fit score and
URL.

| Option | Default | Description |
|---|---|---|
| `--user TEXT` | none | User whose queue to show (see the caveat below) |

> **Known limitation.** `approval queue`, `approval approve`, `company research` and
> `company view` validate `--user` but then read the legacy global `data/jobs.db` rather
> than that user's database. On a multi-user install they will not see the jobs the
> per-user pipeline wrote. Until that is fixed, use **Your progress** on a vacancy in the dashboard, or
> `jobs update-status`, to track progress in a per-user database.

### `approval approve`

```bash
uv run job-scout approval approve 42 --notes "looks strong" --user alex
```

Transitions a job to `APPROVED` and records the approver and notes.

| Argument / option | Default | Description |
|---|---|---|
| `JOB_ID` | required | Numeric job id |
| `--notes TEXT` | none | Approval notes |
| `--user TEXT` | none | User approving; also stored as the approver |

An invalid transition prints an error without a non-zero exit. The same global-database
caveat as [`approval queue`](#approval-queue) applies.

---

## Maintenance

### `prune`

```bash
uv run job-scout prune --user alex --dry-run --browser --llm
```

Fetches the live page of every still-active job — statuses `MATCHED`, `NEW`, `VIEWED`,
`APPROVED` and `READY` — and marks the ones that have been filled or withdrawn as
`EXPIRED`. Prints checked, pruned, still-open and unknown counts.

| Option | Default | Description |
|---|---|---|
| `--user TEXT` | none | User whose jobs to prune |
| `--dry-run` | off | Report without marking anything expired |
| `--browser` | off | Render blocked pages (Indeed, for example) with Playwright |
| `--llm` | off | Ask the LLM to judge ambiguous pages |

Start with `--dry-run` on a new setup: the heuristics are conservative, but you want to see
what they would have expired before they do. The same behaviour runs automatically at the
start of every pipeline run when `prune_enabled` is true (default `false`); the
`prune_use_browser` and `prune_use_llm` keys are the persistent equivalents of the two
flags above.

### `find-sources`

```bash
uv run job-scout find-sources --user alex --browser
```

For every matched job, searches the web for the employer's own posting — their careers site
or ATS rather than a job board — re-checks availability there, persists `official_url` and
`official_available`, and prints an OPEN / FILLED / ? table. Applying at the source usually
beats applying through an aggregator.

| Option | Default | Description |
|---|---|---|
| `--user TEXT` | none | User whose matches to enrich |
| `--browser` | off | Render blocked pages with Playwright during checks |

The pipeline does this automatically while `find_official_sources` is true (the default);
this command is for re-running it on demand.

### `company-review`

```bash
uv run job-scout company-review "ASML" --user alex --refresh
```

Returns a work-quality review for a named company: a summary, pros, cons, sentiment,
financial health, growth and founding year, and a 0–100 work score when the search
results report how employees rate it. Only results that name the company are used, and
the LLM may summarise those and nothing else. The confidence level is counted from the
number of distinct web sources: fewer than three is `low`, six or more is `high`. The
result is cached in the user's database, so the second call is free. When no result
names the company, or the model call fails, the command says so, exits 1 and stores
nothing.

| Argument / option | Default | Description |
|---|---|---|
| `COMPANY` | required | Company name |
| `--user TEXT` | none | User whose LLM configuration to use |
| `--refresh` | off | Ignore any cached review and re-synthesise |

Matches are enriched with this automatically while `company_review_enabled` is true (the
default). Reach for the command when you want to check a company before an interview, or
when you suspect a cached review is stale.

### `company research`

```bash
uv run job-scout company research 42 --user alex
```

Different from `company-review`: this one is about a specific job's employer and how to get
in. It researches industry, size, culture indicators, tech-stack hints, growth signals and
notes from web search results that name the company, saves them with the pages they came
from, and prints the sources. A size or growth figure no result prints is dropped. It then
lists hiring managers — only people a search result actually names, each with the page
that names them; an email or LinkedIn URL is shown only when that page prints it. When no
result names the company it says no public web information was found and stores nothing.

| Argument / option | Default | Description |
|---|---|---|
| `JOB_ID` | required | Numeric job id |
| `--user TEXT` | none | User researching (see the global-database caveat) |

Failures are reported without a non-zero exit, and the global-database caveat from
[`approval queue`](#approval-queue) applies here too.

### `company view`

```bash
uv run job-scout company view 42 --user alex
```

Re-prints previously saved company research without calling the LLM again.

| Argument / option | Default | Description |
|---|---|---|
| `JOB_ID` | required | Numeric job id |
| `--user TEXT` | none | User viewing (see the global-database caveat) |

---

## Application support

These commands build on the structured CV profile, so start with `profile cv-summary` and
make sure it looks right before generating anything from it. The document-review and
guided career-coach features are dashboard-only; they have no CLI command.

### `profile cv-summary`

```bash
uv run job-scout profile cv-summary --user alex
```

Parses the configured CV PDF, extracts a structured profile with the LLM (cached in the
database, keyed on a hash of the CV text) and prints years of experience, skills, education
and past roles with their date ranges.

| Option | Default | Description |
|---|---|---|
| `--user TEXT` | none | User to view |

### `profile import-linkedin`

```bash
uv run job-scout profile import-linkedin --user alex --file linkedin-export.zip
```

Merges LinkedIn data into the cached CV profile from one of three sources: an official data
export ZIP, text pasted on stdin, or a fetched profile URL. It shows a diff of the new
skills, education and roles, and writes nothing until you confirm.

| Option | Default | Description |
|---|---|---|
| `--user TEXT` | none | User to import for |
| `--file PATH` | none | LinkedIn data export ZIP |
| `--paste` | off | Read profile text from stdin |
| `--url TEXT` | `linkedin_profile_url` | Profile URL to fetch (requires `--allow-fetch`) |
| `--allow-fetch` | off | Permit fetching from the URL |

The export ZIP is the recommended route. URL fetching is deliberately double-gated: it
needs both `--allow-fetch` and `linkedin_import_allow_url_fetch: true` in the config,
because it may violate LinkedIn's terms of service. That is your call to make, not the
tool's.

### `profile search-person`

```bash
uv run job-scout profile search-person --user alex --name "Alex Smith" --context "Amsterdam" --refresh
```

Searches the public web for a named person and has the LLM propose extra skills, education
and roles that a CV or LinkedIn import may have missed. Results are explicitly
low-confidence, cached, shown as a diff against the CV profile, and applied only on
confirmation.

| Option | Default | Description |
|---|---|---|
| `--user TEXT` | none | User to search for |
| `--name TEXT` | configured profile name | Full name to search |
| `--context TEXT` | none | Disambiguating context, e.g. current employer or city |
| `--refresh` | off | Ignore any cached result |

Supply `--context` for any common name; without it the search will happily merge two
strangers into one profile.

### `profile tailor-resume`

```bash
uv run job-scout profile tailor-resume 42 --user alex --output ~/tailored_resume.pdf
```

Rewrites the CV against one job's description, foregrounding the keywords that posting
actually uses, and stores the result. The job must be in `APPROVED`, `READY`, `SUBMITTED`,
`INTERVIEWING` or `OFFER` — tailoring is for jobs you have decided to apply to.

| Argument / option | Default | Description |
|---|---|---|
| `JOB_ID` | required | Numeric job id |
| `--user TEXT` | none | User to tailor for |
| `--output PATH` | none | Render the result to a PDF at this path |

Without `--output` it prints only the first 500 characters as a preview; use
[`profile get-resume`](#profile-get-resume) to retrieve the full text later. If a tailored
resume already exists, the command offers an interactive "Regenerate?" confirm — despite
the message mentioning `--force`, this command defines no such option.

### `profile get-resume`

```bash
uv run job-scout profile get-resume 42 --user alex --output ~/resume.pdf
```

Reads a previously stored tailored resume and either prints it or renders it to a PDF.
Exits 1 when none exists.

| Argument / option | Default | Description |
|---|---|---|
| `JOB_ID` | required | Numeric job id |
| `--user TEXT` | none | User to retrieve for |
| `--output PATH` | none | Save as PDF at this path |

### `profile generate-cover-letter`

```bash
uv run job-scout profile generate-cover-letter 42 --user alex --force
```

Drafts a cover letter from the CV profile plus the job description. The job must be
`APPROVED` or later. If a letter already exists and `--force` is not given, it prints the
existing one and asks before regenerating.

| Argument / option | Default | Description |
|---|---|---|
| `JOB_ID` | required | Numeric job id |
| `--user TEXT` | none | User to generate for |
| `--force` | off | Regenerate without asking |

### `profile get-cover-letter`

```bash
uv run job-scout profile get-cover-letter 42 --user alex
```

Prints the stored cover letter for a job. Exits 1 when none exists.

| Argument / option | Default | Description |
|---|---|---|
| `JOB_ID` | required | Numeric job id |
| `--user TEXT` | none | User to retrieve for |

### `profile answer-screening`

```bash
uv run job-scout profile answer-screening 42 --user alex --force
```

Extracts the screening questions an `APPROVED`-or-later posting implies, answers them from
the CV profile in your own voice, saves the question/answer pairs and prints them. These
are drafts for the application form, not submissions — read them before you paste them.

| Argument / option | Default | Description |
|---|---|---|
| `JOB_ID` | required | Numeric job id |
| `--user TEXT` | none | User to answer for |
| `--force` | off | Regenerate even if answers exist |

### `profile get-answers`

```bash
uv run job-scout profile get-answers 42 --user alex
```

Prints the stored screening questions and answers for a job. Exits 1 when none exists.

| Argument / option | Default | Description |
|---|---|---|
| `JOB_ID` | required | Numeric job id |
| `--user TEXT` | none | User to retrieve for |

### `profile star-story`

```bash
uv run job-scout profile star-story add \
  --situation "Release pipeline failed every Friday" \
  --task "Find the cause and stop the weekend firefighting" \
  --action "Instrumented the build, traced it to a flaky integration test" \
  --result "Failures dropped from weekly to none in six months" \
  --keywords "ownership,debugging,process" \
  --user alex
```

One command with four modes, chosen by the positional `ACTION` argument.

| Mode | Requires |
|---|---|
| `add` | all four STAR fields |
| `list` | nothing; prints every stored story |
| `delete` | `--id` |
| `update` | `--id` plus all four STAR fields |

| Option | Default | Description |
|---|---|---|
| `--user TEXT` | none | User |
| `--situation TEXT` | none | Situation / context |
| `--task TEXT` | none | Task / challenge |
| `--action TEXT` | none | Action taken |
| `--result TEXT` | none | Result achieved |
| `--keywords TEXT` | none | Comma-separated keywords used for matching |
| `--id INTEGER` | none | Story id, for `delete` and `update` |

Note the collision worth remembering: the positional `ACTION` selects the mode, while the
`--action` **option** carries the "action taken" text of the story. `star-story add
--action "..."` is correct; `star-story --action add` is not.

```bash
uv run job-scout profile star-story list --user alex
uv run job-scout profile star-story delete --id 3 --user alex
```

### `profile interview-prep`

```bash
uv run job-scout profile interview-prep 42 --user alex
```

Derives the behavioural questions a specific job is likely to ask, matches each against
your stored STAR stories using their keywords, and prints every question with the two
stories that best answer it. Exits 1 when no STAR stories exist, so build the bank first.

This is the small version of [`interview answers`](#interview-answers): the job
description alone, no company research, no CV, no draft answers. Use it to see which of
your stories a posting calls for; use `interview answers` when you have a real interview.

| Argument / option | Default | Description |
|---|---|---|
| `JOB_ID` | required | Numeric job id |
| `--user TEXT` | none | User preparing |

### `interview questions`

```bash
uv run job-scout interview questions 42 --user alex
uv run job-scout interview questions 42 --user alex --language en --cv default
uv run job-scout interview questions 42 --user alex --notes "Second round with the team lead."
```

The inverse of [`interview answers`](#interview-answers): instead of preparing what the
employer will ask you, this writes the questions *you* ask *them*. It reads the vacancy,
the company research and review and your CV facts from every source — your own CV,
CV Builder, the parsed profile with any LinkedIn import and your profile — and prints the questions grouped by theme, each with
one line on why it matters for you and one naming the source it came from. How many are
asked for follows the grounding: 8 to 12 with both research and a review, 6 to 9 with
one of them, 4 to 6 with neither.

When the company has not been researched yet, or its review is missing or rests on fewer
than three web sources, it is looked up on the web first and the result is stored (see
[How the company is looked up](INTERVIEW_QUESTIONS.md#how-the-company-is-looked-up)).
Whatever grounding is still absent is listed at the end under *Not seen, so nothing above
is based on it* rather than guessed at. Salary, holiday and benefit questions are omitted
unless your `--notes` raise them. The set is saved, so the dashboard shows it and
[`interview export`](#interview-export) can write it to a file.

| Argument / option | Default | Description |
|---|---|---|
| `JOB_ID` | required | Numeric job id |
| `--user TEXT` | the only user | User preparing |
| `--language [auto\|nl\|en]` | `auto` | Language to write the questions in; `auto` reads it from the vacancy |
| `--cv TEXT` | matched to the language | CV Builder profile to prefer; your own CV and profile are always used |
| `--notes TEXT` | empty | Context only you know, e.g. what you want to raise |

Exits 1 when the vacancy, the user or a usable CV profile is missing, or when the model
returns nothing usable. The full feature guide is
[INTERVIEW_QUESTIONS.md](INTERVIEW_QUESTIONS.md).

### `interview answers`

```bash
uv run job-scout interview answers 42 --user alex
uv run job-scout interview answers 42 --user alex --language en --cv default
uv run job-scout interview answers 42 --user alex --notes "Leaving because the team was cut."
```

The mirror of [`interview questions`](#interview-questions): instead of what you ask
them, this predicts what the interviewer is likely to ask *you* and drafts the answer you
could give. It reads the same material — the vacancy, the company research and
review, and your CV facts from every source — plus your [STAR story
bank](#profile-star-story), and prints each question with the kind it belongs to, why it
is likely to come up, how much real evidence stands behind the answer, what the answer
draws on, and the draft itself.

An answer may use only your CV, your STAR stories and your `--notes`; nothing is
invented. Where the evidence is not there the draft says so plainly and the question is
marked `!! GAP`, with a count at the end — those are the ones to rehearse. An empty story
bank is allowed, unlike [`profile interview-prep`](#profile-interview-prep): the answers
are then built from CV facts alone, and the missing bank is reported.

The company is looked up first in the same way as for
[`interview questions`](#interview-questions), and the set is saved in the same way.
Absent grounding is listed at the end under *Notes — not seen, so nothing above is based
on it*.

When answers in that language are already saved for the vacancy, they may be answers
you rewrote in the dashboard, so the command asks before it replaces them, the way the
dashboard does. Answer no and the saved answers stay; the new set is printed but not
saved. Without a terminal to answer on, the saved answers are kept as well. `--yes`
replaces them without asking, for scripts.

| Argument / option | Default | Description |
|---|---|---|
| `JOB_ID` | required | Numeric job id |
| `--user TEXT` | the only user | User preparing |
| `--language [auto\|nl\|en]` | `auto` | Language for both the questions and the answers; `auto` reads it from the vacancy |
| `--cv TEXT` | matched to the language | CV Builder profile to prefer; your own CV and profile are always used |
| `--notes TEXT` | empty | Context only you know, e.g. why you are leaving |
| `--yes`, `-y` | off | Replace answers already saved for this vacancy and language without asking |

Exits 1 when the vacancy, the user or a usable CV profile is missing, or when the model
returns nothing usable. The full feature guide is
[INTERVIEW_QUESTIONS.md](INTERVIEW_QUESTIONS.md).

### `interview export`

```bash
uv run job-scout interview export 42 --user alex
uv run job-scout interview export 42 --user alex --mode answer --format txt
uv run job-scout interview export 42 --user alex --mode answer --output ~/Documents
```

Writes a saved interview set to a file you can edit, without generating anything. The
set is the one [`interview questions`](#interview-questions),
[`interview answers`](#interview-answers) or the dashboard saved for that vacancy, with
any answers you rewrote in the dashboard. The Word file opens in Word, LibreOffice and
Google Docs; every question and answer in it is a plain paragraph. The text file has
the same content in the same order.

| Argument / option | Default | Description |
|---|---|---|
| `JOB_ID` | required | Numeric job id |
| `--user TEXT` | the only user | User whose set to export |
| `--mode [ask\|answer]` | `ask` | `ask`: the questions you ask them; `answer`: the questions they may ask you, with your answers |
| `--language [auto\|nl\|en]` | `auto` | Which saved language to export; `auto` takes the newest |
| `--format [docx\|txt]` | `docx` | Word or plain text |
| `--output PATH` | the current folder | A file, or an existing folder to write into |

Without `--output` the file is named like the dashboard's download, for example
`20260918 Interviewvragen Findwhere.docx`. Exits 1 when nothing is saved for that
vacancy yet. See [Saving and downloading](INTERVIEW_QUESTIONS.md#saving-and-downloading).

---

## CV builder

These four commands drive the designed CV: a structured document you edit in a browser and
render to a two-column PDF with a coloured sidebar. They are independent of the CV PDF
configured under `cv_path` — that file feeds the pipeline's scoring and the `profile`
commands, while these read and write CV profiles under
`data/users/<name>/cv/profiles/<slug>/`.

`--user` behaves as it does elsewhere, with one difference worth knowing: with a single user
configured it is optional, and with several configured every `cv` command refuses to guess
and tells you to pass it. The full feature guide, including when a two-column CV is the
wrong file to upload, is [CV_BUILDER.md](CV_BUILDER.md).

### `cv list`

```bash
uv run job-scout cv list --user alex
```

Prints one profile slug per line. On an empty store it prints the directory it looked in
rather than seeding anything — open the editor to get the starter profiles.

| Option | Default | Description |
|---|---|---|
| `--user TEXT` | the only user | User whose CV profiles to list |

### `cv serve`

```bash
uv run job-scout cv serve --user alex --host 127.0.0.1 --port 38271
```

Runs the CV editor as its own web server: the section editor on the left, a live PDF preview
on the right. Seeds the two starter profiles (`default` in English, `nederlands` in Dutch)
if the user has none yet, and logs the data directory and URL on start-up.

| Option | Default | Description |
|---|---|---|
| `--user TEXT` | the only user | User whose CV profiles to edit |
| `--host TEXT` | `127.0.0.1` | Interface to bind |
| `--port INTEGER` | `38271` | Port to listen on |

The port is checked before uvicorn starts, so a clash is a one-line message suggesting the
next port up rather than a traceback. You do not need this command if the dashboard is
already running: the same editor is the **CV Builder** tab, at `/cv/`, where it inherits the
dashboard's token.

### `cv render`

```bash
uv run job-scout cv render default --user alex -o ~/applications/cv.pdf
```

Renders one stored profile straight to a PDF, creating parent directories as needed, and
prints the path and page count. No LLM, no network.

| Argument / option | Default | Description |
|---|---|---|
| `SLUG` | required | Profile slug, as printed by `cv list` |
| `--user TEXT` | the only user | User the profile belongs to |
| `-o`, `--output PATH` | `cv.pdf` | File to write |

### `cv tailor`

```bash
uv run job-scout cv tailor 42 --user alex --slug default -o ~/applications/meridiaan.pdf
```

Tailors a stored CV to one vacancy from the user's database and saves it as a **new**
profile named after the company — `default` plus `meridiaan-data` — then renders that
profile to PDF. The source profile is read, never written, so the same base CV can be
tailored again for the next vacancy.

| Argument / option | Default | Description |
|---|---|---|
| `JOB_ID` | required | Numeric job id, from `jobs list` |
| `--user TEXT` | the only user | User whose CV and database to use |
| `--slug TEXT` | `default` | Profile to tailor. It is read, never written |
| `-o`, `--output PATH` | `<tailored slug>.pdf` | File to write, in the current directory by default |

The model may only reorder sections, entries and items and reword prose. Employers, job
titles, schools, degrees, dates and skill names are frozen, and the finished document is
re-checked against the original: a CV that gained an employer, grew a bullet list or changed
its theme is refused with a message, and nothing is written. Unlike
[`profile tailor-resume`](#profile-tailor-resume) there is no job-status requirement — any
job id in the database will do.

---

## Multi-user management

There is no `user add` command — a user is created by running [`init`](#init) with a name:

```bash
uv run job-scout init --user alex
```

That writes `data/users/alex/config.yaml` and gives the user their own database, logs
directory and notification settings. The root of all of this is the `data` directory,
overridable with the `JOB_SCOUT_DATA_DIR` environment variable (the container sets it to
`/data`).

The conventions across the CLI:

| Form | Meaning |
|---|---|
| `--user alex` | Operate on that user's config and database |
| `--all` (on `run` and `schedule loop`) | Iterate over every configured user |
| Neither | Fall back to the legacy global `data/config.yaml` and `data/jobs.db` |

`all` is rejected as a user name for exactly this reason. Global keys — LLM provider and
model routing, SMTP relay, the container schedule, wake-on-LAN — live in the global config
and are shared; per-user keys such as `profile_description`, `fit_score_threshold`,
`ntfy_topic` and the commute limits are per user. Which is which is listed in
[CONFIGURATION.md](CONFIGURATION.md); `config show --user alex` prints the merged result.

Remember that `approval queue`, `approval approve`, `company research` and `company view`
accept `--user` but read the global database anyway. Every other user-scoped command
resolves the correct per-user database.

---

## Scheduling

There are two independent schedulers, and picking the right one matters.

| | [`schedule loop`](#schedule-loop) | [`schedule install`](#schedule-install) |
|---|---|---|
| Where it runs | Inside the container, as a long-running process | On the host, via crontab |
| Configured by | `schedule_slots`, `schedule_timezone` (global) | `schedule_hour`, `schedule_minute`, `schedule_days` (per user) |
| Granularity | Weekly `day:HH:MM` slots | Daily at one time, on selected weekdays |
| Wakes the LLM host | Yes | No |

If you deployed with Docker, you are already using `schedule loop` and should leave the
crontab alone. Use `schedule install` only for a bare-metal install on a host with a usable
crontab.

### `schedule loop`

```bash
uv run job-scout schedule loop --all --run-now --slots "tue:17:00,sat:03:00" --timezone Europe/Amsterdam
```

The container entrypoint. It re-reads the weekly slot schedule from the configuration every
cycle — so a change made in the dashboard applies without a restart — optionally wakes the
LLM host first, and invokes the pipeline at each occurrence. This is what
`docker-compose.yml` runs as `["schedule", "loop", "--all"]`.

| Option | Default | Environment variable | Description |
|---|---|---|---|
| `--slots TEXT` | `schedule_slots` | `JOB_SCOUT_SCHEDULE` | Override the configured `day:HH:MM` entries |
| `--timezone TEXT` | `schedule_timezone` | `JOB_SCOUT_TIMEZONE` | Override the configured timezone |
| `--user TEXT` | none | — | Run for one specific user |
| `--all` | off | — | Run for every user |
| `--wake-mac TEXT` | `wake_mac` | `JOB_SCOUT_WAKE_MAC` | Override the configured MAC |
| `--wake-broadcast TEXT` | `wake_broadcast` | `JOB_SCOUT_WAKE_BROADCAST` | Override the broadcast address |
| `--llm-health-url TEXT` | `llm_health_url` | `JOB_SCOUT_LLM_HEALTH_URL` | Override the readiness URL |
| `--run-now` | off | — | Run once at start-up, then follow the schedule |

Slots default to `tue:17:00,sat:03:00` in `Europe/Amsterdam` and are DST-correct. The
master switch is `schedule_enabled` (default `true`). An unparseable slot string pauses the
loop with a warning rather than crashing the container — check the logs if runs stop
happening after an edit.

Setting the environment variables pins those values and takes precedence over the config
file, which means the dashboard can no longer change them. Leave them unset unless you
want that.

The command is reachable only as `job-scout schedule loop`, never as `job-scout loop`.

### `schedule install`

```bash
uv run job-scout schedule install --user alice --hour 7 --minute 30 --days 0,6
```

Writes a marker-tagged crontab entry that runs `job-scout run --user <name>` — or
`job-scout run --all` when `--user` is omitted — at the given time. Exits 1 if the crontab
cannot be updated.

| Option | Default | Description |
|---|---|---|
| `--hour INTEGER` | `8` | Hour to run, 0–23 |
| `--minute INTEGER` | `0` | Minute to run, 0–59 |
| `--days TEXT` | `1-5` | Day-of-week in cron syntax |

Accepted `--days` values:

| Value | Meaning |
|---|---|
| `*` | Every day |
| `1-5` | Monday to Friday |
| `0-6` | Every day, written out |
| `0,6` | Weekends |
| `0`–`6` | A single day; `0` is Sunday |

To suspend a user without removing the crontab entry, set `schedule_paused: true`
(`config set schedule_paused true --user alice`) rather than deleting and reinstalling it.
The flag gates the pipeline rather than cron, so it stops every run for that user — the
host schedule, the container `schedule loop` and a manual `job-scout run` alike, each
logging that the run was skipped.

### `schedule status`

```bash
uv run job-scout schedule status --user alice
```

Prints a one-line description of the installed crontab entry for that user — or the global
entry — or says that none is installed.

| Option | Default | Description |
|---|---|---|
| `--user TEXT` | none | User to check |

### `schedule remove`

```bash
uv run job-scout schedule remove --user alice
```

Strips the marker-tagged crontab lines for that user (or the global entry) and confirms.
Exits 1 on crontab failure.

| Option | Default | Description |
|---|---|---|
| `--user TEXT` | none | User to remove |

---

## Infrastructure

### `wake`

```bash
uv run job-scout wake --mac AA:BB:CC:DD:EE:FF --url http://192.168.1.50:11434/v1/models --timeout 300
```

Wakes the machine hosting the model server over Wake-on-LAN. With no `--url` it fires a
magic packet and returns immediately. With `--url` it probes that readiness URL first and
skips the packet entirely if the host already answers; otherwise it wakes the host and polls
until it responds, exiting 1 if it never does within the timeout. This is what makes a
03:00 scheduled run viable against a GPU box that sleeps the rest of the week.

| Option | Default | Environment variable | Description |
|---|---|---|---|
| `--mac TEXT` | — | `JOB_SCOUT_WAKE_MAC` | MAC of the LLM host; exits 1 if absent |
| `--broadcast TEXT` | `255.255.255.255` | `JOB_SCOUT_WAKE_BROADCAST` | Broadcast address for the magic packet |
| `--url TEXT` | — | `JOB_SCOUT_LLM_HEALTH_URL` | Readiness URL to poll |
| `--timeout FLOAT` | `300.0` | — | Seconds to wait for the readiness URL |

A magic packet is a layer-2 broadcast and does not survive Docker's bridge NAT, which is
why the compose file uses host networking. On a desktop install using the bridge override,
waking does not work — leave the MAC empty. Note that `wake` is never poll-only: it needs a MAC
(`--mac` or `JOB_SCOUT_WAKE_MAC`) and exits 1 without one, so there is no mode that checks
readiness without being prepared to wake. The compose file keeps the wake settings out of the environment on
purpose (they live in `data/config.yaml`), so running `wake` inside the container means
passing `--mac` yourself.

### `web`

```bash
uv run job-scout web --host 127.0.0.1 --port 8080
```

Starts the FastAPI dashboard.

| Option | Default | Description |
|---|---|---|
| `--host TEXT` | `0.0.0.0` | Host to bind to |
| `--port INTEGER` | `8000` | Port to bind to |

The CLI defaults to `0.0.0.0:8000`; the container deployment always passes `--port`
explicitly and serves on `JOB_SCOUT_PORT` (default `24817`), so the two numbers you will
see in the wild are 8000 for a local run and 24817 for Docker.

The dashboard is **unauthenticated by default**. It exposes your CV, your matches and your
configuration, so bind it to `127.0.0.1` or set a token — `JOB_SCOUT_DASHBOARD_TOKEN`, or
`dashboard_token` in `data/secrets.yaml` — before putting it on a network anyone else can
reach. See [WEB_DASHBOARD.md](WEB_DASHBOARD.md).

Some functionality exists only here: the guided career-coach intake, document review, and
testing a notification channel all live in the dashboard and have no CLI equivalent.

### `mcp start`

```bash
uv run job-scout mcp start --host 127.0.0.1 --port 5000
```

Runs the MCP server against the global database, so an MCP-capable client can query your
pipeline. Logs and exits 1 on startup failure; exits quietly on Ctrl-C.

| Option | Default | Description |
|---|---|---|
| `--host TEXT` | `127.0.0.1` | Host to bind to |
| `--port INTEGER` | `5000` | Port to bind to |

The `mcp_enabled` and `mcp_port` configuration keys are read and written by the dashboard's
MCP endpoint only — this command takes its own `--host` and `--port` and does not consult
them.

---

## Typical workflows

### First-time solo user

```bash
uv run job-scout init --user alex
uv run job-scout config show --user alex
uv run job-scout run --user alex --dry-run
```

`init` collects your profile, CV path, commute limits and ntfy topic, then offers to
generate keywords — accept. Read back the merged config, then do one dry run: it scrapes
and evaluates for real but saves nothing and notifies nobody, so you can look at what the
model thought before it starts pushing alerts.

```bash
uv run job-scout jobs rejected --limit 30 --user alex
uv run job-scout config set fit_score_threshold 55 --user alex
```

The rejection list is the tuning instrument. Too few matches usually means
`fit_score_threshold` (default `60`) is too high, `title_exclude_keywords` is too greedy, or
your commute limits are tighter than you meant. Adjust, dry-run again, and when the output
looks right, drop `--dry-run` and schedule it:

```bash
uv run job-scout run --user alex
uv run job-scout schedule install --user alex --hour 8 --days 1-5
```

Then work the results: `jobs list` to review, `jobs update-status 42 approved` when you
decide to apply, and the `profile` commands — `tailor-resume`, `generate-cover-letter`,
`answer-screening` — to build the application itself.

### Adding a second user

```bash
uv run job-scout init --user sam
uv run job-scout keywords refresh --user sam
uv run job-scout run --user sam --dry-run
```

Sam gets their own config, database, logs and notification target; nothing is shared except
the global settings — LLM provider and models, the SMTP relay, the container schedule.
Give them a distinct `ntfy_topic` (or a different channel entirely) or you will both get
each other's alerts:

```bash
uv run job-scout config set ntfy_topic job-scout-sam-7f3a --user sam
```

Once both users are configured, switch your scheduling to cover everyone at once:

```bash
uv run job-scout run --all
```

On Docker that is already the case — the scheduler service runs `schedule loop --all` and
picks up the new user automatically. On a host crontab, reinstall the schedule without
`--user` so one entry covers both, and remove the per-user entries you no longer need with
`schedule remove --user alex`.

### Recovering after a significant profile change

You have rewritten `profile_description`, added a career track, or replaced your CV. Three
things are now stale: the keyword lists, the cached CV profile, and every cached
evaluation.

```bash
uv run job-scout keywords refresh --user alex
uv run job-scout profile cv-summary --user alex
uv run job-scout run --user alex --full
```

Refresh the keywords first — they are what the scrapers search for, and a stale list
silently limits everything downstream. Check the parsed CV summary looks right if the PDF
changed. Then run with `--full`, which bypasses deduplication, re-scrapes and re-notifies;
the pipeline also invalidates cached evaluations on its own when the search profile
fingerprint changes, so previously rejected jobs get a genuine second look.

Expect a burst of notifications from that run. If you would rather see the new results
before they arrive on your phone, insert a `--dry-run --full` pass first, check
`jobs list` and `jobs rejected`, and only then run it for real.

```bash
uv run job-scout prune --user alex --dry-run
```

Worth finishing here if the database has been running a while: your pipeline is probably
holding vacancies that were filled weeks ago. Check what `prune` would expire, then run it
without `--dry-run`, and consider setting `prune_enabled: true` so every run keeps itself
tidy.

---

Related reading: [CONFIGURATION.md](CONFIGURATION.md) ·
[LLM_PROVIDERS.md](LLM_PROVIDERS.md) · [NOTIFICATIONS.md](NOTIFICATIONS.md) ·
[WEB_DASHBOARD.md](WEB_DASHBOARD.md) · [DEPLOY.md](DEPLOY.md) ·
[Architecture](../ARCHITECTURE.md) · [Contributing](../CONTRIBUTING.md)

## Motivational letters (`letter`)

The `letter` group drafts from every applicant source (your own CV, CV Builder,
a LinkedIn import, your profile and STAR stories), imports private style
examples, learns a style guide and exports letters. See the [Letter Writer CLI
guide](LETTER_WRITER.md#cli) for commands and saving behaviour.
