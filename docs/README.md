# job-scout documentation

Everything beyond the [project landing page](../README.md) lives here. Each document
answers one question and links to the others rather than repeating them, so you should
never have to read two files to get one answer.

If a document and the code disagree, the code wins — please
[open an issue](https://github.com/JvWageningen/job-scout/issues) so the document gets
fixed.

## All documents

| Document | The question it answers |
| --- | --- |
| [USAGE.md](USAGE.md) | What can I actually run? Every CLI command and flag, plus the day-to-day workflows they combine into. |
| [CONFIGURATION.md](CONFIGURATION.md) | What can I change, and where does it live? Every global key, per-user key, secret and environment variable, with its real default. |
| [LLM_PROVIDERS.md](LLM_PROVIDERS.md) | Which model runs my search? The four backends (local, Z.AI, Claude CLI, Kilo CLI) and how to route each pipeline stage to a different one. |
| [NOTIFICATIONS.md](NOTIFICATIONS.md) | How do matches reach me? ntfy, email, Slack and Discord, per-job versus digest mode, and how failed sends are retried. |
| [WEB_DASHBOARD.md](WEB_DASHBOARD.md) | What does the dashboard do? The eleven tabs, the optional bearer token, and the security posture you are accepting by exposing it. |
| [DEPLOY.md](DEPLOY.md) | How do I run this permanently? Docker Compose on a desktop or a NAS, the data volume, ports, and migrating an existing install into a container. |
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | How is it built? Module map, pipeline data flow, persistence model, concurrency, and the provider and notifier abstractions. |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | How do I change it? Dev setup, the checks CI enforces, Conventional Commits, and the licence your contribution lands under. |
| [../CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) | What behaviour is expected in issues, pull requests and discussions? |
| [../SECURITY.md](../SECURITY.md) | I found a vulnerability — now what? Supported versions, how to report privately, and what to expect afterwards. |
| [../CHANGELOG.md](../CHANGELOG.md) | What changed between releases? Generated from Conventional Commits on every release. |
| [../LICENSE](../LICENSE) | What may I use it for? PolyForm Noncommercial 1.0.0 — noncommercial use only, free for your own job hunt, study, charities, schools and public bodies; commercial use is not granted. |

## Start here

Three routes through the documentation, depending on why you are here.

### I just want it running

1. [Quick start](../README.md#quick-start) — install with Docker or `uv`, then
   `uv run job-scout init --user <name>` to create your profile.
2. [WEB_DASHBOARD.md](WEB_DASHBOARD.md) — open the dashboard, fill in your profile and
   filters, upload your CV and press Run. Everything the first run needs is reachable
   from there without touching a config file.

Once it is producing matches you like, [DEPLOY.md](DEPLOY.md) turns it into a scheduled
service that runs without you.

### I want to tune my matches

1. [CONFIGURATION.md](CONFIGURATION.md) — the knobs that decide what survives:
   `fit_score_threshold`, `quick_eval_threshold`, the title include/exclude lists, the
   commute ceilings, and the salary and vacation floors.
2. [LLM_PROVIDERS.md](LLM_PROVIDERS.md) — the knobs that decide how well those judgements
   are made. Per-stage routing lets a cheap model do the screening while a stronger one
   makes the final call.

[USAGE.md](USAGE.md) covers the commands that show you the effect —
`jobs list`, `jobs rejected` and `runs history`.

### I want to contribute

1. [../ARCHITECTURE.md](../ARCHITECTURE.md) — read this first. Ten minutes gets you the
   module map and the pipeline order, which is enough to find the file you need.
2. [../CONTRIBUTING.md](../CONTRIBUTING.md) — dev setup, the four checks CI runs, the
   commit message format that drives releases, and the licensing terms.

[../CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) and [../SECURITY.md](../SECURITY.md)
apply to everyone taking part.
