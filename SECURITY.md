# Security Policy

job-scout handles genuinely sensitive material: your CV, your salary expectations, API keys
for third-party services, and a SQLite database recording every vacancy you looked at. This
document covers how to report a vulnerability in the project, and — more usefully for most
readers — how to run it without exposing any of that.

## Supported versions

job-scout is maintained by one person. Only the latest release line receives fixes; there are
no long-term support branches and no backports.

| Version | Supported |
| --- | --- |
| Latest release (2.x) | Yes — security fixes land here |
| 1.x (MIT-licensed) | No — upgrade to the current release |

v2.0.0 is the first release under the PolyForm terms; the whole v1.x line was MIT.

Releases are cut automatically from `main` by [python-semantic-release](https://python-semantic-release.readthedocs.io/),
so a fix merged to `main` normally ships within minutes of being merged. If you are running a
Docker deployment, re-running the installer or `docker compose build && docker compose up -d`
picks up the new code.

Note that versions up to and including v1.17.3 were released under the MIT licence and remain
available under it. That does not mean they are maintained — they are not, and no security fix
will be issued for them.

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Report it privately through GitHub's security advisory route:

**https://github.com/JvWageningen/job-scout/security/advisories/new**

That opens a report visible only to you and the maintainer, and it is the only channel that
should be used for undisclosed vulnerabilities. If you cannot use it for some reason, open a
GitHub Discussion asking for a private contact — without describing the vulnerability itself.

### What to include

The more of this you can provide, the faster the fix:

- **What the problem is** — a clear description of the weakness, not just the symptom.
- **Impact** — what an attacker gains. Reading another user's CV? Reaching the dashboard
  without the token? Executing code on the host?
- **Affected version** — the release tag you installed, or the `version` field in
  `pyproject.toml` / the top entry in `CHANGELOG.md` for a checkout, plus whether you are on
  a Docker or a `uv` install.
- **Configuration that matters** — the LLM provider in use (`local`, `zai`, `claude_cli`,
  `kilo_cli`), whether the dashboard is exposed beyond localhost, whether a
  `dashboard_token` is set.
- **Reproduction steps** — a minimal sequence, a request, or a proof-of-concept script.
- **Suggested remediation**, if you have one.

Please redact your own secrets before sending anything. Logs under `data/logs/` can contain
job descriptions and configuration values; scrub API keys, tokens and personal data from any
excerpt you attach.

### What to expect

This is a solo, unpaid project. Response is **best effort, typically within a few days**.
There is no SLA, no bug bounty and no guaranteed remediation window.

What is promised is process, not timing:

1. Acknowledgement that the report arrived and whether it is accepted as a security issue.
2. If accepted, a fix on `main` and a release, with the advisory published afterwards.
3. Credit in the advisory, unless you ask to stay anonymous.
4. If the report is declined — out of scope, working as designed, or not reproducible — an
   explanation of why rather than silence.

Please give a reasonable window for a fix before disclosing publicly. If a report goes
unanswered for more than 30 days, treat that as a failure of this process and disclose as you
see fit.

### Out of scope

- Vulnerabilities in the job boards job-scout scrapes, or in ntfy, Slack, Discord,
  OpenRouteService, NS, Nominatim, SearXNG or any LLM provider. Report those upstream.
- Findings that require an attacker to already have filesystem access to `data/`, or shell
  access to the host. At that point every secret is already readable.
- The dashboard being reachable without a token when no token is configured. That is the
  documented default behaviour, described below — it is a deployment decision, not a bug.
- Automated scanner output pasted without a demonstrated impact on job-scout itself.

## Security considerations for operators

This is the section worth reading even if you never file a report. job-scout is designed to
run on hardware you control, and most of its security posture is your deployment decision.

### The dashboard token is a gate, not authentication

`job-scout web` starts a FastAPI dashboard that binds `0.0.0.0:8000` by default (the Docker
deployment serves it on `JOB_SCOUT_PORT`, default `24817`). If `dashboard_token` is set — via
`data/secrets.yaml` or the `JOB_SCOUT_DASHBOARD_TOKEN` environment variable — API calls
require it as a bearer token. If it is not set, **the dashboard is completely
unauthenticated**.

Understand what the token is and is not:

- It is a single shared secret. There are no user accounts, no sessions, no rate limiting, no
  lockout and no audit trail.
- Anyone holding it has full control: reading your CV profile, every stored vacancy, all
  configuration, and triggering LLM calls on your behalf.
- It is not a substitute for a real authentication layer.

**Do not expose the dashboard directly to the internet.** Keep it on your LAN, or reach it
over a VPN or a tailnet. If you genuinely need remote access, put it behind a reverse proxy
that terminates TLS and performs its own authentication, and treat the token as defence in
depth rather than the front door. Setting `--host 127.0.0.1` restricts it to the local machine
if you only ever use it there.

### ntfy topics are public by default

With `notification_channel: ntfy`, matches are posted to `ntfy_server` (default
`https://ntfy.sh`) under `ntfy_topic`. On a public ntfy instance, **a topic is a namespace,
not a secret** — anyone who knows or guesses the topic name can subscribe and read every
notification, which includes job titles, employers, salary figures and the reasoning behind
your fit score.

The default topic is `job-scout-alerts`, which is exactly as guessable as it looks. Either:

- set a long, random, unguessable topic (`job-scout` generates one for you during
  `job-scout init`), or
- point `ntfy_server` at a self-hosted ntfy instance with access control, or
- use `email`, `slack` or `discord` instead, all of which authenticate the delivery path.

### Secrets live outside the repository

All credentials — `zai_api_key`, `ors_api_key`, `ns_api_key`, `local_api_key`,
`dashboard_token`, `smtp_username`, `smtp_password`, `brave_api_key` — belong in
`data/secrets.yaml` or in the corresponding `JOB_SCOUT_*` environment variables. They are
never written to `data/config.yaml`, and `config show` masks any field whose name contains
`key` down to its last four characters.

`data/` is gitignored in its entirety, as is `.env`. Keep it that way:

- Never move secrets into `data/config.yaml` or into a tracked file to "make deployment
  easier".
- Check before committing if you have changed `JOB_SCOUT_DATA_DIR` to a path outside `data/`,
  because the gitignore rules will not follow you there.
- On a shared host, tighten the permissions on `data/` yourself — job-scout does not do it
  for you. The Docker image runs as uid 1000 and the installers `chown` `data/` to match.

`brave_api_key` is a special case: it has no environment-variable override and can only be
supplied through `data/secrets.yaml`.

### Your CV and job database are personal data

`data/` accumulates a detailed picture of your working life: the parsed CV profile, tailored
resumes, cover letters, screening answers, STAR stories, and a SQLite database of every job
seen, scored and rejected, with the model's reasoning attached. Per-user installs keep this
under `data/users/<name>/`.

None of it leaves your machine except through the paths you configure. Treat backups of
`data/` with the same care as the CV itself, and remember that `jobs export` writes that data
to wherever you point `--output`.

### Prompts containing your CV go to your configured LLM provider

Evaluation, CV parsing, resume tailoring, cover-letter generation and screening answers all
send substantial excerpts of your CV and the job description to whichever provider
`llm_provider` (or the per-stage `*_provider` overrides) names:

| Provider | Where your CV text goes |
| --- | --- |
| `local` | The OpenAI-compatible server at `local_base_url` — your own hardware or LAN |
| `zai` | Z.AI's hosted API at `zai_base_url` |
| `claude_cli` | Anthropic, via the `claude` CLI |
| `kilo_cli` | The provider Kilo routes the request to |

`local` is the default precisely because it needs no API key and keeps CV text on hardware you
own. **If the confidentiality of your CV matters to you, keep `llm_provider: local` and point
`local_base_url` at a server you control.** You can mix: route cheap screening to a hosted
model and keep `evaluation_provider` and `cv_parsing_provider` local, since those are the
stages that see the most personal detail.

Company research, company review, official-source lookup and person search additionally issue
web searches — through SearXNG (`searxng_url`) or Brave (`brave_api_key`) — containing company
names and, for `profile search-person`, your own name.

### Scraping is your responsibility

job-scout fetches pages from Indeed, LinkedIn, Nationale Vacaturebank and any career pages you
add with `sites add`. Whether that fetching is permitted is governed by each site's terms of
service and by the law where you live — not by this project. The tool is built for one person
running a personal job hunt at modest volume, and `max_jobs_per_source` (default 50) exists
partly to keep it there.

`profile import-linkedin --allow-fetch` deserves a specific warning, and the CLI gives one:
fetching a LinkedIn profile by URL may violate LinkedIn's terms. It is off by default, is
gated behind both the flag and `linkedin_import_allow_url_fetch: true`, and the supported path
is importing the data export LinkedIn gives you on request.

`prune --browser` and per-site `render_js` drive a real browser (Playwright) against pages that
block plain HTTP clients. Point it only at sites you are willing to render.

### Wake-on-LAN and host networking

The Docker deployment runs with `network_mode: host` because Wake-on-LAN magic packets are
layer-2 broadcasts that do not survive bridge NAT. Host networking means the containers share
the host's network stack — the dashboard port is not isolated by Docker, and the
`--host`/firewall decisions above are the only thing standing between it and your LAN. The
desktop override (`docker-compose.desktop.yml`) switches to bridge networking and publishes a
single port; wake-on-LAN does not work there.

## Dependency and code scanning

The project is configured for several security tools, though not all of them run
automatically. Contributors and operators auditing a checkout can run them directly:

```bash
uv run pip-audit                              # known CVEs in the dependency tree
uv run bandit -c pyproject.toml -r src/       # static analysis of the source
```

Current state, stated honestly:

- **`bandit`** runs as a pre-commit hook (`.pre-commit-config.yaml`), configured in
  `pyproject.toml` with `B101` skipped and `tests`/`.venv` excluded. It is **not** part of CI.
- **`pip-audit`** is in the dev dependency group but is wired into neither CI nor pre-commit.
  Run it before a release, and run it on your own checkout periodically.
- **CI** (`.github/workflows/ci.yml`) enforces `ruff check`, `ruff format --check`, `pytest`
  and `mypy --strict` on `src/`. Type-strictness catches a meaningful class of bug, but it is
  not a security gate.
- **Dependabot** is configured in `.github/dependabot.yml` for the `uv` and `github-actions`
  ecosystems on a weekly schedule, so dependency and workflow-action updates arrive as pull
  requests. Those PRs carry a `chore` commit prefix, which python-semantic-release reads as no
  version bump — a dependency update therefore never cuts a release on its own.

If you are deploying job-scout somewhere that matters, pin your own audit step into whatever
build you run, rather than relying on this project's CI to have caught everything.
