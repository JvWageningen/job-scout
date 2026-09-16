# Web dashboard

job-scout ships a single-page FastAPI dashboard that covers the day-to-day work:
starting a run and watching it progress, reading the matches it produced, moving
jobs through the pipeline, and editing almost every configuration value without
touching YAML.

It is a local control room, not a hosted product. It has one optional shared-secret
gate, no user accounts, and no TLS of its own. Read [Security](#security) before you
put it anywhere other than your own machine or a trusted LAN.

---

## Starting it

```bash
uv run job-scout web
```

| Option | Default | Notes |
| --- | --- | --- |
| `--host` | `0.0.0.0` | Binds every interface on the machine. Pass `127.0.0.1` for local-only. |
| `--port` | `8000` | The bare-CLI default. |

On start-up the command prints a banner with the URL and whether token
authentication is active, then hands over to uvicorn.

To keep it to the loopback interface:

```bash
uv run job-scout web --host 127.0.0.1 --port 8080
```

### The Docker port is different

The bare CLI defaults to port **8000**. The shipped container deployment does not
use that default — `docker-compose.yml` passes the port explicitly:

```yaml
command: ["web", "--host", "0.0.0.0", "--port", "${JOB_SCOUT_PORT:-24817}"]
```

So a Docker install serves the dashboard on **24817** unless you set `JOB_SCOUT_PORT`
in `.env`. `JOB_SCOUT_PORT` is read only by Compose; nothing in the application code
looks at it, which is why it has no effect on a bare `uv run job-scout web`.

| Install | URL |
| --- | --- |
| `uv run job-scout web` (no flags) | `http://localhost:8000` |
| Docker / Docker Desktop (default `.env`) | `http://localhost:24817` |
| NAS or LAN host | `http://<host-ip>:24817` |

The default Compose file uses `network_mode: host`, so on Linux and on a NAS the
port is exposed on every interface of the host machine. The Docker Desktop override
(`docker-compose.desktop.yml`) switches to bridge networking and publishes the port
instead. Either way, the port is reachable from your LAN unless a firewall says
otherwise.

Container logs, including the start-up banner:

```bash
docker compose logs -f web
```

See [DEPLOY.md](DEPLOY.md) for the full container setup.

---

## First run and the user picker

Two things sit above the tabs and gate everything below them.

**First-time setup.** If no global config exists yet, the page shows a short form
asking only for the default LLM provider, and writes `data/config.yaml`. Everything
else is configured later from the tabs.

**User picker.** job-scout is multi-user: each user has their own config, database
and logs. Pick a user from the dropdown before anything loads, or press
**Create New User**. The picker also offers **all**, which targets every configured
user — useful for kicking off a combined run, though per-user panels stay empty in
that mode.

---

## Finding a function

Navigation is grouped by purpose:

- **Find & track jobs:** Dashboard, Approvals and Analytics.
- **Prepare applications:** CV Builder, Cover Letter Writer and Document Review.
- **Settings:** Profile & Filters, Keywords, Custom Sites, Notifications, Schedule,
  LLM Settings and Secrets.

The Dashboard starts with four workflow cards: find roles, prepare a CV, generate a
full cover letter, and review the documents. The search controls and vacancy lists
remain below them. **Cover Letter Writer** generates the entire application letter,
including its opening, body and closing; Document Review provides feedback on an
existing document. These are separate actions.

## The tabs

### Dashboard

The working surface. **Run Pipeline** starts a background run for the selected user;
**Dry run** is ticked by default (evaluate, save nothing, notify nobody) and
**Full re-scrape and re-notify** bypasses deduplication and re-sends matches you have
already been told about. While a run is going, the status area shows the live stage
and item counts rather than a bare spinner, and **Stop run** requests a cooperative
stop.

Below that, **Recently Matched Jobs** and **Recently Rejected Jobs** each render as
cards, filterable by minimum score and source and sortable by date or score. A match
card shows the fit score and the model's reasoning, the career track it matched, the
location and salary, the company work-quality review when one was synthesised, a link
to the listing, and a link to the employer's own posting with an `open` /
`may be filled` badge. Each match also carries a status dropdown (new, viewed,
approved, ready, submitted, interviewing, offer, rejected, expired) and a notes box,
saved per job. Rejected cards show why they were dropped — negative match, fit score,
or compensation.

At the bottom, a **Logs** panel lists the per-run log files for the selected user and
prints the one you choose.

### Approvals

The queue of jobs awaiting approval, with a count, the fit score, the listing link and
an optional notes field per job. Approving records the approver and the notes and moves
the job to `approved`.

Note that the dashboard reads the selected user's own database here. The equivalent CLI
commands (`job-scout approval queue` / `approval approve`) currently read the legacy
global `data/jobs.db` instead, so on a multi-user install the dashboard is the reliable
route to the approval queue.

### Profile & Filters

The largest tab, and the one that decides what the pipeline looks for.

- **Profile and negative description** — the free text the model scores each vacancy
  against, and the text that disqualifies one.
- **CV** — the path to your CV PDF, an upload button that puts a PDF into the user's
  data directory for you, and a free-text CV notes field for corrections the parser
  cannot know about.
- **Filters** — minimum and maximum salary, maximum straight-line distance, maximum
  car / public transport / bike commute in minutes, minimum vacation days.
- **Sources** — the JobSpy site checkboxes, plus the per-scraper keyword limits.
- **Parsed CV Profile** — read-only view of what the LLM extracted from the PDF: years
  of experience, skills, education and past roles.
- **Job Coach** — a short guided interview grounded in your CV that proposes concrete
  career directions. Nothing is saved until you accept the proposal.
- **Search Directions** — the career tracks actually searched, marked *standalone*
  (searched on its own) or *blend* (folded into every other direction).
- **Profile Enrichment** — merge a LinkedIn profile PDF, pasted profile text, a data
  export ZIP, or a fetched profile URL into the parsed CV, or search the public web on
  your own name. Both paths show a diff first and apply only when you confirm; the URL
  method additionally requires the explicit allow-fetch opt-in.

### Document Review

An honest second opinion on something you are about to send. Review the configured CV
either in general or as an application for a specific vacancy, or paste a motivational
letter and have it judged against the vacancy it answers. Nothing here is saved or
sent anywhere — the critique is generated and displayed.

### Cover Letter Writer

Draft a Dutch or English motivational letter from a vacancy and a saved CV Builder
profile. Add private example letters and an editable style guide, then review the
wording, save separate language versions, and export PDF or text. Generation does
not submit applications. See [Cover Letter Writer](LETTER_WRITER.md) for the complete
workflow, privacy details and review limitations.

### Keywords

Shows the four generated lists — Dutch keywords, English keywords, title-include and
title-exclude — and regenerates all four from the profile description plus CV text with
**Refresh Keywords**. Same operation as `job-scout keywords refresh`.

### Custom Sites

Any company careers page can become a source: add a URL, optionally name it, and tick
**Render JavaScript** for pages that need a real browser (that requires the Playwright
extra, `uv sync --extra browser`). Existing sites are listed with their enabled state
and can be removed.

### Notifications

Pick the channel (ntfy, email, Slack, Discord) and the mode (one notification per job,
or a single per-run digest), then fill in that channel's settings. Values that belong
to the shared install — the ntfy server, the SMTP host, port and From address — are
shown read-only; the per-user parts (ntfy topic, recipient address, Slack or Discord
webhook URL) are editable here.

The ntfy panel additionally generates a subscribe link and a QR code for your phone,
in app-opening and web-opening variants, and can mint a new hard-to-guess topic with
**New secure topic**.

**Test Channel** sends a real test notification through the configured channel. This is
the only way to test a channel — there is no CLI equivalent.

### LLM Settings

The default provider (`local`, `zai`, `claude_cli`, `kilo_cli`) plus the per-purpose
overrides for evaluation, screening, quick evaluation and keyword generation, so a cheap
model can screen while a strong one makes the final call. For the local provider you can
set the base URL, press **Detect Models** to list what the endpoint actually serves, and
pick an evaluation model and an optional separate screening model.

**Test Connection** fires a single call at a provider, model, base URL and API key you
type in on the spot, without saving any of it — the fastest way to find out whether your
model server is reachable from wherever job-scout is running. Inside Docker, that means
reachable *from the container*, which is not the same as from your desktop.

### Secrets

Write-only fields for the Z.AI, local, OpenRouteService and NS API keys. Leaving a field
blank keeps the stored value; submitting writes to `data/secrets.yaml`. Values are never
returned in full — the config API masks every secret down to its last four characters.

The dashboard token itself is deliberately not editable here; see
[Authentication](#authentication). The SMTP credentials and the Brave Search key are also
absent from this form and must be set in `data/secrets.yaml` or via their environment
variables. See [CONFIGURATION.md](CONFIGURATION.md).

### Schedule

Two independent mechanisms, and the tab keeps them apart.

**Automatic runs** configures the container's own weekly scheduler: an enable switch, one
or more `day:HH:MM` slots, the timezone they are read in, and the wake-on-LAN block (MAC
address, broadcast address, model-server readiness URL, and how long to wait after waking).
**Test wake now** sends a magic packet immediately and reports what happened. The scheduler
re-reads this configuration every cycle, so changes apply within a minute with no restart.

**Host cron** sits in a collapsed panel because it only applies to non-container installs.
It manages the user's crontab entry: hour, minute, cron day-of-week, a pause switch, and a
remove button.

Wake-on-LAN needs layer-2 broadcast, which is why the default Compose file uses host
networking. On a Docker Desktop install (bridge networking) waking will not work — leave
the MAC empty there.

### Analytics

Run history for the selected user: a table of start time, scraped, matched, rejected,
notified, error count and duration, plus a bar chart of the last seven days. The same data
as `job-scout runs history`.

---

## What the dashboard does not cover

Some commands have no dashboard route. Use the CLI for:

`prune`, `find-sources`, `company-review`, `profile tailor-resume`,
`profile get-resume`, `wake` (the Schedule tab only offers a wake *test*), and
`mcp start` (the dashboard can report and toggle the MCP config, but does not run the
server).

Full command reference: [USAGE.md](USAGE.md).

---

## Authentication

Authentication is **optional and off by default**. Configure a token and the dashboard
requires it; leave it unset and every API route is open to anyone who can reach the port.

Set the token in either place — the environment variable wins:

```bash
export JOB_SCOUT_DASHBOARD_TOKEN="$(openssl rand -base64 32)"
```

```yaml
# data/secrets.yaml
dashboard_token: "a-long-random-string"
```

For Docker, put it in `.env`; Compose passes `JOB_SCOUT_DASHBOARD_TOKEN` through to both
services.

### How it works

- A middleware guards every path under `/api/`. Static assets (`/`, `/app.js`,
  `/style.css`, the icons) are served without a check — they contain no data.
- With no token configured the middleware passes every request straight through.
- With a token configured, a request must carry `Authorization: Bearer <token>`. A
  missing or malformed header returns `401 {"detail": "Missing or invalid Authorization
  header"}`; a wrong token returns `401 {"detail": "Invalid token"}`. The comparison is
  constant-time.
- The token is read **once, at start-up**. Changing it means restarting the server
  (`docker compose restart web` on a container install).

### In the browser

The front-end keeps the token in `sessionStorage` under the key `dashboardToken` and
attaches it as `Authorization: Bearer <token>` to every `/api/` request. If a request
comes back `401`, the page prompts for the token, stores it, and retries once.

`sessionStorage` is per browser tab and is cleared when that tab closes — so "logging
out" means closing the tab, or clearing site data. There is no logout button.

### From a script

```bash
curl -H "Authorization: Bearer $JOB_SCOUT_DASHBOARD_TOKEN" \
  http://127.0.0.1:24817/api/users
```

---

## Security

**Read this before exposing the dashboard to anything beyond your own machine.**

### The token is a gate, not a login system

It is a single shared secret, and that is all it is. There are no accounts, no sessions,
no expiry, no password reset, no rate limiting or lockout after failed attempts, and no
audit trail of who did what. Anyone holding the token has the same complete access as you
do, and the only way to revoke it is to change it and restart the server. Do not treat it
as a login.

### There is no TLS

Uvicorn serves plain HTTP. Without a TLS-terminating reverse proxy in front of it,
everything crosses the network in the clear: the Bearer token on every single request,
your parsed CV and job history, the contents of your run logs, and any API key or webhook
URL you type into the Secrets or Notifications tabs. Anyone able to observe the traffic can
read the token and replay it.

### What access to the dashboard actually grants

It is worth being concrete about the blast radius, because it is larger than "someone can
see my job list":

- Read the parsed CV profile, every matched and rejected job, and the full run logs.
- Write API keys into `data/secrets.yaml` and webhook URLs into the user config.
- Trigger pipeline runs, which spend real money on LLM and routing API calls.
- Change the ntfy topic or webhook URL, redirecting your notifications elsewhere.
- Change the schedule, the wake-on-LAN target and the local model base URL — that last one
  makes the host fetch from an arbitrary address.
- Add custom sites, which the pipeline then fetches and may render in a browser.

Secrets are masked to their last four characters when read back, but they are writable in
full.

There is also no CSRF token and no CORS allow-list. Same-origin policy stops most of what a
hostile page in another tab could do to a dashboard bound to localhost, but it is a browser
default, not a control this project implements — do not rely on it.

### Recommendations

1. **Always set a token.** It costs one line in `.env` and it is the difference between
   "needs a secret" and "needs nothing at all".
2. **Bind to loopback when you only use it locally**: `--host 127.0.0.1`. The default
   `0.0.0.0` listens on every interface, and the default Compose file uses host networking,
   so the port is on your LAN the moment the container starts.
3. **Firewall the port.** Allow it only from the addresses that need it.
4. **Reach it over a VPN**, not over the internet. WireGuard or Tailscale to the machine,
   then browse to it as if you were local. This is the recommended remote-access route and
   it solves both the transport encryption and the exposure problem at once.
5. **If it must be reachable directly, put a reverse proxy in front** — Caddy, nginx or
   Traefik — terminating TLS with a real certificate, and preferably adding authentication
   of its own on top of the token.
6. **Never port-forward it to the public internet** and never hand the URL to anyone you
   would not hand the CV and API keys to.
7. **Treat the token like a password**: long, random, unique, kept out of version control
   (`.env` and `data/secrets.yaml` are both gitignored), rotated by editing it and
   restarting.

---

## Related documentation

- [USAGE.md](USAGE.md) — the full CLI reference
- [CONFIGURATION.md](CONFIGURATION.md) — every config key, secret and environment variable
- [NOTIFICATIONS.md](NOTIFICATIONS.md) — channels, modes and delivery
- [LLM_PROVIDERS.md](LLM_PROVIDERS.md) — backends and per-stage routing
- [DEPLOY.md](DEPLOY.md) — Docker, NAS and update procedures
- [SECURITY.md](../SECURITY.md) — reporting a vulnerability
