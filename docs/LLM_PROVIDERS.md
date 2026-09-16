# LLM Providers

job-scout is model-agnostic. Every LLM call in the pipeline — title screening, quick
scoring, full evaluation, keyword generation, CV parsing, resume tailoring, cover
letters, interview prep — goes through one small interface, so you can point the tool at
a model running on your own hardware, at a hosted API, or at a coding CLI you already
have installed, and change your mind later without touching any code.

There are four backends:

| Provider | What it is | Needs |
| --- | --- | --- |
| `local` | Any OpenAI-compatible server on localhost or your LAN | A running model server |
| `zai` | Z.AI GLM models over the OpenAI-compatible REST API | `zai_api_key` |
| `claude_cli` | The Claude Code CLI, driven as a subprocess | `claude` on `PATH` |
| `kilo_cli` | The Kilo Code CLI, driven as a subprocess | `kilo` on `PATH` |

`local` is the default, deliberately: it is the only option that works with no API key
and no account, and it is the only one where nothing about your CV or your job hunt
leaves your network.

- [How it fits together](#how-it-fits-together)
- [Call purposes](#call-purposes)
- [Choosing a provider](#choosing-a-provider)
- [`local` — your own hardware](#local--your-own-hardware)
- [`zai` — Z.AI GLM](#zai--zai-glm)
- [`claude_cli` — Claude Code CLI](#claude_cli--claude-code-cli)
- [`kilo_cli` — Kilo Code CLI](#kilo_cli--kilo-code-cli)
- [Per-stage routing](#per-stage-routing)
- [Which model key drives which stage](#which-model-key-drives-which-stage)
- [Privacy](#privacy)
- [Retries, backoff and timeouts](#retries-backoff-and-timeouts)
- [Troubleshooting](#troubleshooting)

## How it fits together

Three pieces, each doing one job.

**The protocol.** `job_scout.llm.base.LLMClient` is a two-method `Protocol`:

```python
def complete(self, prompt: str, *, purpose: CallPurpose, timeout: float | None = None) -> str
def check_available(self) -> tuple[bool, str | None]
```

`complete` sends a prompt and returns raw text (which may arrive fenced in markdown —
callers strip that). `check_available` reports readiness without making a network call,
so the pipeline can fail fast with an actionable message rather than mid-run. Failures
are raised as `LLMError`, and every provider normalises its own errors into that one
type. Nothing in the pipeline imports a provider directly.

**The factory.** `job_scout.llm.factory.get_llm_client(config)` reads your configuration
and returns the client the rest of the code uses. It builds the default provider, builds
a second client for each configured per-stage override, wraps the result in a purpose
router when any override is present, and finally wraps everything in the retry client.
Misconfiguration is caught here — asking for `zai` without a key raises `LLMError`
immediately instead of failing on the first vacancy.

**The retry wrapper.** `RetryingLLMClient` retries any `LLMError` with exponential
backoff — `llm_retry_base_delay * 2 ** attempt` seconds between attempts, up to
`llm_max_attempts` — and re-raises the last error when the attempts run out. It is
always the outermost layer, so a transient hiccup on any provider, including a stage
override, is retried the same way.

The whole chain is assembled per run:

```
pipeline → RetryingLLMClient → [_PurposeRoutingClient] → ZaiClient / LocalLLMClient /
                                                          ClaudeCliClient / KiloCliClient
```

## Call purposes

Every call carries a `purpose`. Providers use it to pick a model and a timeout; the
factory uses it to route across providers. Five of the ten purposes can be routed to a
different provider; the other five always run on the default provider.

| Purpose | Raised by | Routable | Override key |
| --- | --- | --- | --- |
| `screening` | Batched LLM title screen | yes | `screening_provider` |
| `quick_eval` | Quick per-track scoring, pruner page judgement | yes | `quick_eval_provider` |
| `evaluation` | Full fit evaluation, custom-site extraction, company research, company review, person search, document feedback, career coach | yes | `evaluation_provider` |
| `keywords` | `keywords refresh` generation | yes | `keywords_provider` |
| `cv_parsing` | CV PDF parsing, LinkedIn PDF import | yes | `cv_parsing_provider` |
| `resume_tailoring` | `profile tailor-resume` | no | — |
| `cover_letter` | `profile generate-cover-letter` | no | — |
| `screening_questions` | Extracting a posting's screening questions | no | — |
| `screening_answers` | `profile answer-screening` | no | — |
| `behavioral_questions` | `profile interview-prep` | no | — |

Note that `evaluation` is the busiest purpose by far: it covers the final fit judgement
but also every research-shaped call in the project. Routing `evaluation` moves all of
them together.

## Choosing a provider

| If you want… | Use | Why |
| --- | --- | --- |
| Nothing to leave your network | `local` | No CV text, job description or profile crosses your firewall |
| To run with no API key or account | `local` | The default; needs only a model server |
| To run on a NAS or mini-PC with no GPU | `zai` | Hosted inference; the container only issues HTTP calls |
| The cheapest sensible hosted setup | `zai` with `glm-4.5-air` for screening and quick-eval | Both cheap stages already default to the small model |
| The strongest judgement on final matches | `claude_cli` or `zai` for `evaluation` only | Route one stage; leave the volume stages local |
| To reuse a Claude Code subscription you already pay for | `claude_cli` | No separate API key, no separate billing |
| To reach several providers through one CLI | `kilo_cli` | Models are addressed as `provider/model` |
| Local privacy *and* a strong final verdict | `local` default + `evaluation_provider` override | See the [worked example](#worked-example-cheap-locally-strong-at-the-end) |
| Resilience when one network route drops | `local` with `local_fallback_base_urls` | LAN address first, VPN/tailnet address as fallback |
| Unattended scheduled runs on a sleeping GPU box | `local` plus wake-on-LAN | See [DEPLOY.md](DEPLOY.md) and the `wake_*` keys in [CONFIGURATION.md](CONFIGURATION.md) |

## `local` — your own hardware

Any server that implements the OpenAI chat-completions API works. Confirmed shapes
include Ollama, LM Studio, llama-swap, vLLM, llama.cpp's server, text-generation-webui
and LocalAI.

### Setup

Start your server, then point job-scout at it. The base URL must include the API
version path — `/v1` — because the client probes `{base_url}/models` to check health.

```bash
uv run job-scout config set llm_provider local
uv run job-scout config set local_base_url http://192.168.1.50:11434/v1
uv run job-scout config set local_model qwen3:14b
uv run job-scout config set local_screening_model qwen3:4b
uv run job-scout config set local_quick_eval_model qwen3:4b
```

These are global keys, so they take no `--user` flag. If your server requires a bearer
token, put it in `data/secrets.yaml` as `local_api_key` or export
`JOB_SCOUT_LOCAL_API_KEY`; most local servers need neither.

Add fallback endpoints when the same machine is reachable by more than one route — a LAN
address at home and a VPN or tailnet address from elsewhere. They are tried in priority
order, the first that answers becomes active, and a connection-level failure mid-run
demotes it and moves to the next, so losing one route no longer takes the run down. An
error from a server that *did* answer (a bad model id, say) is raised immediately rather
than retried elsewhere, because repeating it would only repeat the mistake.

### Reasoning control

Reasoning models are worth their thinking budget on a judgement and wasteful on a sieve.
`local_reasoning_purposes` (per-user, default `["evaluation", "cv_parsing",
"resume_tailoring", "cover_letter"]`) lists the purposes that keep reasoning on. Every
other purpose is sent with `chat_template_kwargs.enable_thinking = false` — the
Qwen-family convention — and a tighter output ceiling. If a chat template rejects that
switch, the client logs a warning, disables it for the rest of the run and retries
immediately rather than failing every screening call.

Two ceilings apply to the completion, both per-user:

| Key | Default | Applies to |
| --- | --- | --- |
| `local_max_tokens_reasoning` | `8000` | Purposes listed in `local_reasoning_purposes` |
| `local_max_tokens_direct` | `1200` | Everything else |

A completion that stops on the token ceiling is treated as a failure, not an answer. This
matters more than it sounds: a truncated reply parses as unusable JSON, and a caller that
read it as "the model judged this job and scored it zero" would cache that verdict and
reject the vacancy for good. Raise `local_max_tokens_reasoning` if you see that error
against a verbose model.

### Config keys

| Key | Default | Purpose |
| --- | --- | --- |
| `local_base_url` | `http://localhost:11434/v1` | Primary endpoint |
| `local_fallback_base_urls` | `[]` | Alternate endpoints, tried in order |
| `local_model` | `llama3.1` | Evaluation and everything without its own key |
| `local_screening_model` | `None` | Title screening; falls back to `local_model` |
| `local_quick_eval_model` | `None` | Quick eval; falls back to the screening model |
| `local_keywords_model` | `None` | Keyword generation; falls back to `local_model` |
| `local_evaluation_timeout` | `120.0` | Read timeout for evaluation and keyword calls |
| `local_screening_timeout` | `90.0` | Read timeout for screening and quick-eval calls |
| `local_connect_timeout` | `15.0` | TCP connect budget, kept separate from generation |
| `local_probe_timeout` | `10.0` | Health-probe budget per endpoint |
| `local_api_key` | secret | Bearer token, if your server wants one |

### Trade-offs

Free to run, private by construction, and as fast as your hardware. In exchange you own
the operations: the model server has to be up when a scheduled run fires (hence
`job-scout wake` and the `wake_*` keys), quality depends entirely on the model you
loaded, and small models are noticeably worse at producing clean JSON on the first
attempt — which the retry wrapper absorbs, at the cost of time. JSON mode is not
requested, because too many local servers do not implement it; the prompts ask for JSON
and the callers strip markdown fences.

## `zai` — Z.AI GLM

Hosted GLM models over an OpenAI-compatible endpoint. The natural choice when the
machine running job-scout has no GPU — a NAS, a VPS, a mini-PC.

### Setup

The API key is a secret, so it is **not** settable with `config set`. Put it in
`data/secrets.yaml` (never a tracked config file) or supply it through the environment:

```yaml
# data/secrets.yaml
zai_api_key: your-key-here
```

```bash
export JOB_SCOUT_ZAI_API_KEY=your-key-here
uv run job-scout config set llm_provider zai
```

The default models are already the sensible split — a strong model for evaluation, a
small one for screening and quick-eval:

| Key | Default | Purpose |
| --- | --- | --- |
| `zai_base_url` | `https://api.z.ai/api/coding/paas/v4` | API endpoint |
| `zai_model` | `glm-5.1` | Evaluation, keywords and all unrouted purposes |
| `zai_screening_model` | `glm-4.5-air` | Title screening |
| `zai_quick_eval_model` | `glm-4.5-air` | Quick eval |
| `zai_screening_batch_size` | `20` | Titles per screening call |

`zai_screening_batch_size` exists because hosted context windows are the binding
constraint on batching; the local path batches 80 titles per call instead. The smaller
batch size is applied when `llm_provider` itself is `zai` or `kilo_cli`, so if you keep a
local default and route only `screening_provider: zai`, screening still batches at 80 —
lower `zai_screening_batch_size` will not take effect in that arrangement.

### Trade-offs

Strong, consistent JSON (the client asks for `response_format: json_object`), no
hardware to own, and it works from a container with nothing but outbound HTTPS. In
exchange, every job description, your profile text and — for the application tooling —
your CV are sent to a third party, and you pay per token. The SDK's own retries are
disabled (`max_retries=0`) so that all retry behaviour lives in one place, under
`llm_max_attempts`.

## `claude_cli` — Claude Code CLI

Shells out to the `claude` binary with `--print`. Useful if you already have Claude Code
installed and would rather not manage another API key.

```bash
npm install -g @anthropic-ai/claude-code
uv run job-scout config set llm_provider claude_cli
uv run job-scout config set claude_screening_model haiku
```

| Key | Default | Purpose |
| --- | --- | --- |
| `claude_evaluation_model` | `None` | `--model` for evaluation and everything else; `None` omits the flag and lets the CLI pick |
| `claude_screening_model` | `haiku` | `--model` for screening and quick-eval |

Screening and quick-eval calls are also sent with `--tools ""`, since neither needs tool
access.

### Trade-offs

No API key to manage in job-scout, and the strongest evaluation quality of the four on
the default model. The cost is throughput: one subprocess per call, with a 90-second
evaluation timeout and a 60-second screening timeout, makes it the slowest option for
the high-volume stages. It also only distinguishes two model tiers — cheap
(screening, quick-eval) and everything else — so there is no separate keyword model.
Availability is checked by looking for `claude` on `PATH`, which is worth knowing for
container deployments: the shipped image does not include it.

## `kilo_cli` — Kilo Code CLI

Shells out to `kilo run --auto --format json --model <provider>/<model>` and parses the
NDJSON event stream. Model names **must** carry the provider prefix.

```bash
npm install -g kilo-code
uv run job-scout config set llm_provider kilo_cli
uv run job-scout config set kilo_evaluation_model zai/glm-5.1
uv run job-scout config set kilo_screening_model zai/glm-4.5-air
```

| Key | Default | Purpose |
| --- | --- | --- |
| `kilo_evaluation_model` | `zai/glm-5.1` | Evaluation, keywords and all unrouted purposes |
| `kilo_screening_model` | `zai/glm-4.5-air` | Title screening |
| `kilo_quick_eval_model` | `zai/glm-4.5-air` | Quick eval |

Each call is a fresh one-shot prompt — no sessions are created — and `--auto`
auto-approves any incidental tool call so the subprocess never blocks waiting for input.

### Trade-offs

One CLI, many upstream providers, with routing expressed in the model name. The costs
are the same subprocess overhead as `claude_cli` plus a dependency on Kilo's NDJSON
output shape; a run that emits no text event is reported as `Kilo CLI returned no text
content`.

## Per-stage routing

The five routable purposes each have a provider override. Set one and job-scout builds a
second client for that purpose only; leave it unset (or equal to `llm_provider`) and
everything runs on the default.

| Key | Routes |
| --- | --- |
| `screening_provider` | Batched title screening |
| `quick_eval_provider` | Quick scoring and pruner page judgements |
| `evaluation_provider` | Full evaluation, custom-site extraction, company/person research, feedback, coach |
| `keywords_provider` | Keyword generation |
| `cv_parsing_provider` | CV parsing and LinkedIn PDF import |

All five are global keys — set them without `--user`. Each override uses that provider's
own model keys, so routing `evaluation_provider: zai` means the evaluation stage uses
`zai_model`, not `local_model`.

Readiness is checked across the whole set before a run: `check_available()` on the router
probes the default client and every override, and reports which one failed by name
(`Provider for screening: …`). A misconfigured override fails the run early rather than
halfway through.

### Worked example: cheap locally, strong at the end

The funnel is shaped so that the expensive stage sees the fewest jobs. A typical run
screens hundreds of titles, quick-scores dozens and fully evaluates a handful. That makes
one arrangement obviously right for most people: run the volume stages on a small local
model where they cost nothing, and spend a hosted model only on the final judgement.

```bash
# Default everything to the local server
uv run job-scout config set llm_provider local
uv run job-scout config set local_base_url http://192.168.1.50:11434/v1
uv run job-scout config set local_model qwen3:14b
uv run job-scout config set local_screening_model qwen3:4b
uv run job-scout config set local_quick_eval_model qwen3:4b

# Send only the final fit judgement to a hosted model
uv run job-scout config set evaluation_provider zai
uv run job-scout config set zai_model glm-5.1
```

With the key in `data/secrets.yaml`, that yields:

| Stage | Provider | Model |
| --- | --- | --- |
| Title screening | local | `qwen3:4b` |
| Quick eval | local | `qwen3:4b` |
| Full evaluation | zai | `glm-5.1` |
| Keyword generation | local | `qwen3:14b` |
| CV parsing | local | `qwen3:14b` |
| Resume / cover letter / interview prep | local | `qwen3:14b` |

Two things follow from that table and are worth stating plainly. The hosted provider sees
only the descriptions of jobs that survived three local filters, not your whole scrape.
But it *does* see your profile text as part of the evaluation prompt — if that is not
acceptable, leave `evaluation_provider` unset and keep the strong model local too.

The inverse arrangement also works: default to `zai` for quality, then set
`screening_provider: local` to keep the highest-volume stage off your bill.

## Which model key drives which stage

The purpose-to-model mapping is per-provider. Where a key is unset, the fallback column
says what is used instead.

| Purpose | `local` | `zai` | `claude_cli` | `kilo_cli` |
| --- | --- | --- | --- | --- |
| `screening` | `local_screening_model` → `local_model` | `zai_screening_model` → `zai_model` | `claude_screening_model` | `kilo_screening_model` |
| `quick_eval` | `local_quick_eval_model` → screening → `local_model` | `zai_quick_eval_model` → screening model | `claude_screening_model` | `kilo_quick_eval_model` |
| `evaluation` | `local_model` | `zai_model` | `claude_evaluation_model` | `kilo_evaluation_model` |
| `keywords` | `local_keywords_model` → `local_model` | `zai_model` | `claude_evaluation_model` | `kilo_evaluation_model` |
| `cv_parsing` | `local_model` | `zai_model` | `claude_evaluation_model` | `kilo_evaluation_model` |
| `resume_tailoring` | `local_model` | `zai_model` | `claude_evaluation_model` | `kilo_evaluation_model` |
| `cover_letter` | `local_model` | `zai_model` | `claude_evaluation_model` | `kilo_evaluation_model` |
| `screening_questions` | `local_model` | `zai_model` | `claude_evaluation_model` | `kilo_evaluation_model` |
| `screening_answers` | `local_model` | `zai_model` | `claude_evaluation_model` | `kilo_evaluation_model` |
| `behavioral_questions` | `local_model` | `zai_model` | `claude_evaluation_model` | `kilo_evaluation_model` |

Only `local` gives keyword generation its own key. On `zai` and `kilo_cli`, `keywords`
rides the evaluation model; on `claude_cli`, everything except screening and quick-eval
rides `claude_evaluation_model`, which when left at `None` means the CLI's own default.

Default timeouts follow the same split:

| Provider | Evaluation / keywords | Screening / quick eval |
| --- | --- | --- |
| `local` | `local_evaluation_timeout` (120s) | `local_screening_timeout` (90s) |
| `zai` | 120s | 60s |
| `claude_cli` | 90s | 60s |
| `kilo_cli` | 120s | 90s |

Callers may pass a tighter per-call timeout: title screening caps a batch at 60 seconds,
keyword generation allows 120, and custom-site extraction allows 60.

## Privacy

With `llm_provider = local` and no provider overrides, no prompt leaves your network.
That covers everything the LLM touches: job descriptions, your profile and negative
description, your career tracks, the full text of your CV, the tailored resumes and cover
letters generated from it, and your screening answers. The only outbound traffic a run
makes is to the job boards themselves, the geocoding and routing services, and your
notification channel.

This is not a side benefit. The tool is built around a personal CV and a private job
hunt — often one you would rather your current employer not learn about — and a local
model is the only configuration where that material never becomes someone else's log
line. It is why `local` is the default despite being the fiddliest to set up.

Points worth checking if privacy is the reason you are here:

- **Any override leaks that stage.** A single `evaluation_provider: zai` sends job
  descriptions *and* your profile text off-box. Check with `config show`.
- **A LAN endpoint is still local.** `local_base_url` pointing at another machine on your
  own network keeps the guarantee; a fallback URL pointing somewhere else does not.
- **The application tooling is the most sensitive.** `resume_tailoring`, `cover_letter`,
  `screening_answers` and `cv_parsing` carry the most personal text of any purpose. Four
  of them cannot be routed away from the default provider at all, so the default provider
  is the one to get right.
- **Web search is separate.** Company review, official-source lookup and person search
  query a search backend (`searxng_url`, or Brave with `brave_api_key`) regardless of
  which LLM provider you use. See [CONFIGURATION.md](CONFIGURATION.md).

## Retries, backoff and timeouts

Every client is wrapped in `RetryingLLMClient`, so retry behaviour is identical across
providers and is controlled by two global keys:

| Key | Default | Meaning |
| --- | --- | --- |
| `llm_max_attempts` | `3` | Total attempts per call, including the first |
| `llm_retry_base_delay` | `1.0` | Base delay in seconds; doubles each attempt |

The delay before attempt *n* is `llm_retry_base_delay * 2 ** (n - 1)`. At the defaults
that is a 1-second pause after the first failure and 2 seconds after the second, then the
last error is re-raised. Attempt counts below 1 are clamped to 1.

```bash
uv run job-scout config set llm_max_attempts 5
uv run job-scout config set llm_retry_base_delay 2
```

What is and is not retried:

- **Retried:** anything raised as `LLMError` — timeouts, transport failures, non-zero CLI
  exits, unparseable output, a token-ceiling cut-off, an unreachable endpoint list.
- **Not retried by the wrapper:** endpoint failover on `local`, which happens *inside* one
  attempt. Each attempt walks the endpoint list in priority order, so a single retry may
  already have tried every route.
- **Not retried at all:** provider misconfiguration detected by the factory, such as
  `zai` without a key. That is a setup error, and retrying it wastes time.

Both provider SDKs are constructed with `max_retries=0` so there is exactly one retry
policy in the system, and raising `llm_max_attempts` has the effect you expect.

Timeouts stack with attempts. On the defaults, a stubborn local evaluation can occupy up
to three 120-second attempts plus 3 seconds of backoff before the job is given up on.
Keep that in mind on a large first run — and remember `max_parallel_evaluations`
(per-user, default 5) controls how many of these run at once.

## Troubleshooting

### Test the connection from the dashboard

The fastest check is the **LLM Settings** tab in the web dashboard. It tests a candidate
configuration *before* you save it, so you can try a URL or a model name without
committing it to your config.

```bash
uv run job-scout web --host 127.0.0.1 --port 8000
```

Two actions are available there. **Test connection** builds a throwaway client for the
provider, model and (for `local`/`zai`) base URL and API key you have typed in, runs
`check_available()` and reports either `Connection successful` or the provider's own
error message. **Detect models** queries an OpenAI-compatible endpoint's `/models` route
and lists what it actually serves, per endpoint, with timings — the quickest way to
settle an argument about a model id.

Note what each check does *not* prove. `check_available()` never generates a completion:
for `local` it proves the endpoint answers with a model list, for the two CLI providers it
proves the binary is on `PATH`, and for `zai` it proves only that a key was present when
the client was built. A successful test with a mistyped model id is entirely possible;
the first real call is where that surfaces.

### Common failure modes

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `llm_provider is 'zai' but zai_api_key is not set` | Key missing from `data/secrets.yaml` and from the environment | Add it to `data/secrets.yaml` or export `JOB_SCOUT_ZAI_API_KEY`. `config set zai_api_key` is refused by design — secrets never go in a tracked config file |
| `Cannot reach local LLM server` listing every endpoint | Server down, wrong port, asleep host, or a firewall | Check the server, then re-probe with **Detect models**. If the host sleeps, configure `wake_mac` and `llm_health_url` |
| `HTTP 404 from …/models` | Base URL missing the version path | It must end in `/v1`, e.g. `http://localhost:11434/v1` |
| `non-JSON response` or `unexpected response shape` | Something else is listening on that port | Confirm nothing but the model server is bound there |
| `<model> hit the token ceiling before finishing its answer` | A verbose or reasoning model outran the cap | Raise `local_max_tokens_reasoning` (or `local_max_tokens_direct`), or add the purpose to `local_reasoning_purposes` so it gets the larger ceiling |
| `Model … rejected the thinking switch` (warning only) | The chat template does not understand `enable_thinking` | Nothing to do — the client disables the switch and retries; reasoning simply stays on |
| Endpoint switch logged mid-run | Primary route dropped; a fallback took over | Expected behaviour. If it is constant, reorder `local_fallback_base_urls` |
| `Claude Code CLI not found` | `claude` not on `PATH` — including inside the container | `npm install -g @anthropic-ai/claude-code`, or pick a provider that needs no binary |
| `Kilo CLI failed (exit N)` / `returned no text content` | Bad model name, or no assistant output in the stream | Kilo models need the `provider/model` prefix, e.g. `zai/glm-5.1` |
| `Local LLM API error: … model not found` | Model id not served by that endpoint | Run **Detect models** and copy the id exactly, tag included |
| Evaluations time out under load | Too many parallel calls for the hardware | Lower `max_parallel_evaluations` (per-user) or raise `local_evaluation_timeout` |
| Screening produces nonsense on a tiny model | Model too small to hold an 80-title batch | Use a larger screening model, or route `screening_provider` to a hosted one |
| Screening still batches 80 titles after lowering `zai_screening_batch_size` | The smaller batch applies only when `llm_provider` itself is `zai` or `kilo_cli`, not when `screening_provider` is | Make the hosted provider the default, or leave the batch size alone |
| `Provider for screening: …` at start-up | A per-stage override is misconfigured | The message names the purpose — fix that provider's keys |

### Reading the logs

Run with `-v` to see per-call detail. Every provider logs token usage at DEBUG, and the
local client logs endpoint switches at INFO, so a verbose run tells you which endpoint
and which model actually served each stage:

```bash
uv run job-scout -v run --user alex --dry-run
```

`--dry-run` evaluates without saving or notifying, which makes it the right way to test a
provider change against real listings.

---

See also: [CONFIGURATION.md](CONFIGURATION.md) for every key and its default,
[USAGE.md](USAGE.md) for the commands that trigger each stage, [DEPLOY.md](DEPLOY.md) for
running against a model host that sleeps, and [ARCHITECTURE.md](../ARCHITECTURE.md) for
where the LLM sits in the pipeline.
