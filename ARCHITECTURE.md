# Architecture

This document describes how job-scout is put together today. It is aimed at someone about
to change the code: read it once and you should be able to guess which file a given piece
of behaviour lives in.

## System overview

job-scout is a Click CLI plus a FastAPI dashboard over a shared pipeline. One run scrapes
Dutch job boards, narrows thousands of listings down to a handful through four
progressively more expensive filters, enriches what survives, and pushes a notification.
All state is local: a SQLite database and YAML config per user, under one data directory.
There are no external services beyond the job boards, the routing APIs and whichever LLM
you point it at.

```text
                 ┌───────────────────────────────────────────────────┐
  CLI ──────────▶│                    pipeline                       │
  (cli.py)       │                                                   │
                 │  scrape ─▶ dedup ─▶ title filter ─▶ title screen   │
  Dashboard ────▶│     ─▶ commute ─▶ quick eval ─▶ full eval          │
  (web/app.py)   │     ─▶ verify ─▶ enrich ─▶ notify                  │
                 └───────┬───────────────────────┬───────────────────┘
                         │                       │
                  ┌──────▼──────┐        ┌───────▼────────┐
                  │  Database   │        │  LLM provider  │
                  │  (SQLite,   │        │  local / zai / │
                  │  per user)  │        │  claude / kilo │
                  └─────────────┘        └────────────────┘
```

Both front ends call the same functions in `cli.py`. The dashboard's "Run" button starts
the pipeline in a background thread of the dashboard process; nothing is duplicated
between the two entry points.

## Module map

Everything under `src/job_scout/`. Roughly in pipeline order.

### Orchestration and shared types

| Module | Role |
| --- | --- |
| `cli.py` | Defines the root Click group and the pipeline commands. Also holds the pipeline itself — `_run_pipeline` and the stage helpers — because both front ends call into it. The `cv` and `letter` groups are built in their respective subpackages, and the `memory` group in `memory_cli.py`, and attached with `cli.add_command()`. |
| `config.py` | Data-directory layout, global/user/secret file loading, the `GLOBAL_FIELDS`/`USER_FIELDS`/`SECRET_FIELDS` split, and type coercion for `config set`. |
| `models.py` | All Pydantic models. `JobListing` is the value carried through every stage; `Config` is the merged settings object; `CareerTrack`, `TravelTime`, `TrackScore`, `RunStats`, `JobStatus` and the evaluation results hang off them. |
| `database.py` | Every SQL statement in the project. Schema creation, additive migrations, dedup keys, caches and lifecycle transitions. |
| `progress.py` | In-process registry of the running pipeline's stage, counts, per-stage seconds and the cooperative stop flag. |

### Sourcing and screening

| Module | Role |
| --- | --- |
| `scraper.py` | Fetches from all configured sources in parallel and normalises them to `JobListing`. Indeed and LinkedIn via `python-jobspy`, Nationalevacaturebank via its JSON API, custom career pages via fetched (optionally Playwright-rendered) HTML that the LLM extracts listings from. |
| `title_filter.py` | Rule-based, morpheme-aware title filter. Deliberately not substring matching — on a Dutch board, `"AI"` as a substring hits *Maintenance*, *Trainee* and *detail*. |
| `title_screener.py` | Batched LLM title screening; many titles per call, several calls in parallel. |
| `tracks.py` | Resolves `career_tracks` into the searches actually run: standalone versus blend tracks, the effective description and negative text per track, and the interleaved deduplicated keyword lists. |

### Judging

| Module | Role |
| --- | --- |
| `evaluator.py` | Prompt construction and response parsing for quick eval, full evaluation and keyword generation. Also owns `evaluation_fingerprint()`, which decides when cached scores have gone stale. |
| `salary.py` | Deterministic, conservative regex backstop that re-scans the full description for EUR amounts near Dutch/English salary keywords, catching pay the truncated LLM prompt missed. |
| `travel.py` | Geocoding and routing. Nominatim for coordinates (with a module-level rate-limit throttle), OSRM as the free default router, OpenRouteService when `ors_api_key` is set, and the NS Journey Planner for public transport. |

### Post-match enrichment

| Module | Role |
| --- | --- |
| `pruner.py` | Fetches a posting and decides whether the vacancy is still open. Conservative by design: only an explicit filled/closed notice or a gone page prunes; ambiguous or unreachable pages are left alone. |
| `official_source.py` | Searches for the same vacancy on the employer's own site or ATS rather than a job board, then re-checks availability there using the pruner's detection. |
| `company_review.py` | Synthesises a cached work-quality review (score, pros, cons, financial health, growth) from web search results that name the company. The LLM may use only those snippets; confidence is counted from the number of distinct sources, and with no evidence the model is not called. Failures raise `CompanyReviewError` (or its `ReviewSearchError` when no search query returned anything, so the search was down) or `LLMError` instead of returning a placeholder. The prompt carries the house style and the prose that comes back is passed through `humanise()`; `tidy_review()` does the same for a review read from the cache. A placeholder name such as "Unknown" is never reviewed. |
| `company_research.py` | Company research from web search results that name the company (namesakes dropped, unsupported size and growth figures cleared, snippets stored as `CompanyResearch.evidence`). Stored per vacancy, read per company. Returns None when nothing is found and raises `CompanyResearchError` (or its `SearchUnavailableError` when no search query returned anything) or `LLMError` when the lookup fails. A placeholder name such as "Unknown" is never searched for. Hiring managers are suggested only on request and only when a search result prints the name. The prompts carry the house style; prose fields are passed through `humanise()` after the figure check, while names, URLs and snippets are kept as read. `tidy_research()` cleans research read back from the database the same way. |
| `websearch.py` | Keyless web search via the DuckDuckGo HTML endpoint, shared by the three modules above and by `person_search`. Returns an empty list on failure rather than raising. |
| `company_lookups.py` | The memory that makes research and reviews a once-per-company cost. Every lookup is remembered per company and kind with its time and outcome (found, nothing found, failed), and within that outcome's cooldown the same lookup does not run again: research 30 days, a review 14 days, a failure 1 hour. A search that returned no result for any query counts as failed, since web search always returns something when it works. Explicit refreshes (`company research`, `POST /api/company/research`) always run, in the user's database, and are remembered too. Nothing is remembered under a placeholder name. |

### Profile and application toolkit

| Module | Role |
| --- | --- |
| `cv_parser.py` | PDF text extraction (PyPDF2), `compute_cv_hash()`, and `parse_cv_structured()` which turns raw text into a `CvProfile` via the LLM. |
| `cv_profile.py` | Thin cache layer over the above: hash the text, return the cached profile if present, otherwise parse and store. |
| `linkedin_import.py` | Merges a LinkedIn export ZIP, profile HTML or profile PDF into the `CvProfile`, normalising companies and reconciling a stale open-ended "current" role. |
| `person_search.py` | Keyless web search on the candidate's name for facts a CV or LinkedIn import missed. Always presented for review, never applied blind. |
| `coach.py` | Guided intake: CV-grounded baseline questions, then an LLM proposal of up to four standalone or blend `CareerTrack` objects to accept or reject. |
| `feedback.py` | Critiques a document the candidate already has — a CV on its own or against one vacancy, a motivational letter always against a vacancy — as ranked, specific points. |
| `resume_tailor.py` | Extracts high-value keywords from a job description, rewrites the resume around them, and renders the result to PDF with ReportLab. `tailor_resume_text` also takes the memories allowed on a CV (`memories=`, from `applicant.memories_for_vacancy(user, "cv", job)`), which may reword or add a line under a role the CV already has or in the profile text, never a role, employer, date, school or skill. |
| `cover_letter_generator.py` | Drafts a cover letter from `CvProfile` + job, extracts the screening questions a posting implies, and answers them in the candidate's voice. Both the letter and the answers take the memories allowed in letters (`memories=`, from `applicant.memories_for_vacancy`), quoted with `applicant.memories_block`; `profile generate-cover-letter` and `profile answer-screening` pass them. |
| `applicant.py` | Everything the applicant has told job-scout about themselves: their own CV file and notes, a real CV Builder profile, the parsed profile (which is where a LinkedIn import lands), profile description, career tracks and STAR stories, each labelled by source for the prompt. No source is mandatory and CV Builder's example CV is never one of them. Also finds the name, place and contact details a letter heading needs. Used by the letter writer and both interview generators. A caller that names its purpose (`gather_applicant_facts(..., memories="letter", job=job)`) also gets the memories allowed for it under `memories`, never a private one, best fitting the vacancy first; `memories_for_vacancy` does the same for CV tailoring. `SOURCE_GUIDE` tells the model to use a memory only where its hint or tags fit and to read memory text as data, never as instructions; `memories_block` quotes memories with the same rules for a prompt that has no applicant sources. `describe_sources(user, purpose)` counts only the memories that document may use. |
| `interview_prep.py` | Derives likely behavioural questions for a job and matches each to the best STAR story from the saved bank. |
| `interview_questions.py` | The inverse direction: the questions the *candidate* asks the employer. Reads the vacancy, the stored `CompanyResearch` and `CompanyReview` and the applicant's facts, and returns a themed `InterviewQuestionSet` that names every grounding source it did not have. `company_context` fills company gaps first: research stored for any vacancy at the same company is reused, research that is missing or cites no source is looked up, and a review that is missing or rests on fewer than three web sources is rewritten, each only when `company_lookups` has no recent attempt on record. What is found is stored, nothing is stored on failure or when the web has nothing, every attempt is remembered, and an unreachable `evaluation` provider stops further lookups. A gap that a remembered attempt stands in for is reported with its date, and the context carries the date of the research and review it uses (or of the last check that found nothing). Stored prose is cleaned into the house style on reading, and a placeholder company name shares nothing with other vacancies. The memories allowed in interviews are part of the applicant's facts, and a question whose `grounded_in` names a memory the prompt did not hold is dropped like one citing absent research (`unknown_memory_citation`, shared with the answers). Only a clause that is a memory's label, read by `memories.parse_memory_label`, or the memories as a whole counts as citing one, so "vacancy: in-memory data grid" is a vacancy citation. The generator stores no questions; the `/api/interview` router and the CLI save each set through `interview_store.py`. |
| `interview_answers.py` | The mirror of `interview_questions.py`: what the *interviewer* asks the candidate, each prediction carrying a draft answer. Same grounding plus the saved STAR stories, the same `company_context` lookups, the same single `behavioral_questions` generation call, and an `InterviewAnswerSet` of `LikelyQuestion` objects with a `kind`, a `why_asked`, the draft, the sources it drew on and an `AnswerFooting` of `strong`, `partial` or `gap`. The hard rule lives in the prompt: an answer may use only the CV facts, the STAR stories, the memories allowed in interviews and the applicant's notes, and a gap is stated rather than filled. Stories and memories are cited by label (`STAR story 3`, `memory 4`), and `_check_citations` drops a citation of one the prompt did not hold. |
| `interview_store.py` | Keeps every generated interview set under `data/users/<name>/interview/`, one JSON file per vacancy, half (`ask` or `answer`) and language, written atomically like a letter draft. Generating again replaces that file; edited answers are saved over it. Timestamps are kept in UTC. Paths are built from a checked user, a positive vacancy id and two enums, and must sit directly in the user's folder. It also lists the vacancies with saved sets, so the tab still offers one that left the shortlist. |
| `interview_export.py` | Turns a set, as it is on screen, into an editable Word file (python-docx) or plain text. Both formats are rendered from one list of blocks, so they hold the same content in the same order; fixed labels are Dutch or English to match the set and follow the house style in `writing_style.py`. Story and memory citations (`STAR story 3`, `memory 4`) and the source counts are named in the set's language. |
| `memories.py` | The applicant's memories: facts kept for later letters, interview sets and CV tailoring, often ones left off the CV. The `Memory` models and enums, the per-user store over `Database`, duplicate detection (`same_fact`: equal after normalising, or the same negations, numbers and modal verbs and no meaningful word the known text lacks, so another name, tool, number or a flipped wish is a new fact; rewordings are left to the model), and selection: `select_memories` keeps the memories allowed for one purpose (cv, letter, interview), never a sensitive one, ranks them by tags and words shared with the vacancy and trims them to 25. A tag of one or two letters or a stopword (`it`, `hr`, `team`) counts only where the vacancy writes it in capitals. Deleting a memory records its text in `forgotten_memories` (`list_forgotten`, `clear_forgotten`). `memories_payload` turns them into labelled prompt material (`memory 3`) without their origin, and `parse_memory_label` reads a label back in any spelling a model uses (`Memory #3`, `herinnering 3`, a number) for every citation check; `MEMORY_GUIDE` (which also says the more recent of two disagreeing memories holds) and `MEMORY_CV_RULE` are the prompt sentences that go with it. See [Memories](docs/MEMORIES.md). |
| `memory_extract.py` | Turns free text into memory drafts with one `cv_parsing` call (300 s timeout): the model judges what is worth keeping and generalises it, code cleans the wording into the house style, enforces the limits, keeps wishes and conditions off the CV, marks a memory sensitive when its text, hint or a tag says the applicant has a private matter (first-person phrases only; a topic word such as "bijstand" also names someone's work), drops known facts and caps the drafts at 20. It returns a `MemoryExtraction` with the drafts and `truncated`, set when the text may hold more. Sensitive memories are never shown to the model, and the source detail (which can name a scraped company) never enters the prompt. `capture_from_notes` runs it once per notes text of a letter or interview request, leaves out every memory the applicant deleted (also shown to the model, private ones excepted), stores the result and never raises; the per-user `memory_auto_capture` setting switches it off. |
| `memory_cli.py` | The `memory` CLI group: `list`, `add`, `edit`, `delete`, `import`, `forgotten` and `auto-capture`. Also `report_notes_capture`, which `letter generate`, `interview questions` and `interview answers` call after their output: it runs `capture_from_notes` in the foreground on the `--notes` and says which memories were saved, and says nothing (and asks no model) when the notes are short, already captured or capture is off. `cv tailor` and `profile tailor-resume` pass the memories allowed on a CV to the tailor. |
| `letters/` | Per-user examples, style guides, CV-grounded letter generation (including the memories allowed in letters), structured draft storage, PDF rendering, and the `/api/letters` router and `letter` CLI group. See [Cover Letter Writer](docs/LETTER_WRITER.md). |
| `cv/` | The CV builder — a self-contained subpackage with its own document model, storage, renderer, FastAPI router, Click group and front end. See [the CV subpackage](#the-cv-subpackage) below. |

**Why the three interview modules sit beside each other rather than inside one.**
`interview_prep.py` consumes a job description and the STAR bank and returns questions
matched to stories; the other two consume company research, a company review and the
applicant's facts as well, and return their own models — different inputs, different output
models, different failure modes, and the only overlap is the `behavioral_questions`
routing purpose all three borrow. Folding them together would have entangled the STAR
matcher with applicant-fact gathering to save a file. They reuse
`applicant.gather_applicant_facts` and `letters.language` instead, because the view of the
applicant and the Dutch/English decision are genuinely one behaviour and must not drift
between the letter writer and the interview tab.

`interview_answers.py` is a sibling of `interview_questions.py`, not a branch inside it:
the two directions share a shape — grounding block, grounding-aware question budget,
strict JSON parse, dedupe on a punctuation-stripped key, `missing_context` instead of
invention — but they share no prompt, no enum and no output model, and the answers half
additionally reads `Database.get_star_stories()` and enforces its own evidence rule.
Both are exposed through one `/api/interview` router and one dashboard tab
(`web/static/interview.js`) with a shared request body and a shared vacancy shortlist, so
neither direction can be prepared for a vacancy the other would refuse. The router, not
the generators, saves each result through `interview_store.py`, so the generators stay
free of storage; `/saved/{job_id}` reads the sets back and `/export/{questions|answers}`
renders the body it is sent, the way the letter PDF route does. The older
`/api/interview-prep/{job_id}` route and `profile interview-prep` command are untouched.

### Delivery, scheduling and integration

| Module | Role |
| --- | --- |
| `notify/` | The notification layer: `base.py` (the `Notifier` protocol), `factory.py` (dispatch on `notification_channel`), and `ntfy.py`, `email.py`, `slack.py`, `discord.py`. |
| `notifier.py` | Legacy top-level ntfy module, superseded by `notify/ntfy.py` and kept for backward compatibility. New code should use the factory. |
| `ntfy_topic.py` | Generates a topic that is not trivially guessable — the topic name *is* the access control on ntfy. |
| `scheduler.py` | Host crontab management, tagged with a `# job-scout-managed` marker so unrelated entries survive. Per user. |
| `weekly_schedule.py` | The container-native scheduler: parses slots like `tue:17:00,sat:03:00` in a real timezone, DST-correct, so the image does not depend on a host crontab that ASUSTOR ADM restricts. |
| `wol.py` | Sends a Wake-on-LAN magic packet to the machine hosting the model server and polls a health URL until it answers, so a scheduled run does not fail against a sleeping GPU box. |
| `exporter.py` | `JobExporter.to_csv` / `to_json`, shared by `jobs export` and the dashboard export endpoint. |
| `mcp_server.py` | MCP server exposing the pipeline to MCP-capable AI clients. |
| `web/` | `app.py` (the FastAPI application factory and every route), `memories_api.py` (the `/api/memories` router and the background capture of memories after a letter or interview set), and `static/` (the single-page dashboard: `index.html`, `app.js`, one script per larger tab such as `letters.js`, `interview.js` and `memories.js`, `style.css`, icons). |
| `llm/` | The provider abstraction — see below. |

### The CV subpackage

`cv/` was a separate project, vendored in wholesale. It keeps its own layering because it
solves a different problem from everything above it: the pipeline reads a CV to judge
vacancies, while this writes one.

| Module | Role |
| --- | --- |
| `cv/models.py` | `CVDocument` — identity, `Theme`, and two ordered lists of sections (`sidebar`, `main`). A section is a discriminated union over seven kinds: `text`, `experience`, `education`, `skills`, `details`, `contact`, `list`. Every section and entry carries a generated id, which is what makes reordering expressible as data. |
| `cv/storage.py` | `ProfileStore`: one directory per profile holding `cv.json` and `uploads/`, atomic writes, slug normalisation as the single path-traversal gate, and the starter-profile seeding. Takes its root as a constructor argument, which is the whole reason multi-user support needed no change here. |
| `cv/render/` | The renderer. `document.py` builds geometry and blocks and drives the canvas, `frame.py` paginates each column independently, `blocks.py` and `text.py` measure and draw, `icons.py` draws sixteen named icons as vectors. |
| `cv/fonts.py`, `cv/images.py` | Bundled Lato registration with a Helvetica fallback, and portrait normalisation (EXIF-rotate, centre-crop square, downscale). |
| `cv/sample.py` | The seeded English and Dutch starter documents, and the empty skeleton behind "New profile". |
| `cv/app.py` | `build_api_router()` — the CRUD, portrait, preview and download endpoints — plus `create_app()` for the standalone server. The store is resolved through a module-level `get_store` dependency precisely so a host application can override it. |
| `cv/cli.py` | The `cv` Click group: `serve`, `render`, `list`, `tailor`. Click, not the typer CLI it shipped with, so job-scout gains no dependency and everything is one console script. |
| `cv/tailor.py` | Vacancy tailoring for a `CVDocument`, with the integrity checks described below. `tailor_cv_document(..., memories=)` takes the memories allowed on a CV as extra evidence: they may reword the profile text, a description or a bullet, and an entry may gain one bullet per memory its patch names in `memories`, placed after its own bullets. `cv/memory_bullets.py` holds each new bullet against the memory it names; a bullet it cannot vouch for is left out and the entry keeps its own bullets, while an entry that grows without naming a memory still fails the tailoring. The integrity check is unchanged. |
| `cv/memory_bullets.py` | The check behind a memory's new bullet: the memory must belong to the entry (it does not describe work at another organisation, or name another employer or school on the CV and not this one), the bullet must share at least two of its significant words or numbers, every number and capitalised name in the bullet must come from the memory or the entry, the bullet may not put its work "at" another organisation, a wish or condition backs nothing, and each memory backs one bullet in the whole CV. It reads words, not meaning. |
| `cv/static/` | The editor front end: `index.html`, `app.js`, `style.css`, `favicon.svg`. One file serves both hosts — it reads `window.CV_API_BASE` and a `?user=` parameter, and falls back to the standalone behaviour when neither is set. |

**Two integration seams, both narrow.** `cli.py` imports the group and calls
`cli.add_command()`; `web/app.py` mounts the router at `/api/cv` and overrides `get_store`
with one that returns `ProfileStore(user_cv_dir(user))`. The mount point is inside `/api/`
on purpose: `TokenAuthMiddleware` guards exactly that prefix, so the CV endpoints inherit
the dashboard's token instead of growing a second, easily forgotten auth path.

### Why there are two PDF paths

`resume_tailor.py` and `cv/` both produce a tailored PDF, and the overlap is deliberate.

| | `resume_tailor.py` | `cv/` |
| --- | --- | --- |
| Input | Raw CV **text**, from `cv_parser.parse_cv()` reading the `cv_path` PDF | A structured `CVDocument` the user authored in the editor |
| Unit of change | The whole document, rewritten by the LLM | Ordering plus individual prose fields, patched by id |
| Output | `generate_resume_pdf()` — ReportLab `SimpleDocTemplate`, one column, standard styles, deliberately ATS-safe | `cv/render` — a hand-driven canvas, two independently paginated columns, bleed sidebar, portrait, icons |
| Stored in | The jobs database, against the job row | A new profile directory of its own |

They are different because their inputs are. Text in, text out can be rewritten freely; a
structured document cannot, because its fields are claims about a real person and the model
is holding all of them at once. So `cv/tailor.py` never asks for a new document — it asks
for a *plan* of id orderings and reworded prose, applies that plan to a deep copy, and then
re-checks the result: `_reject_rewritten_facts` refuses a patch that edits a frozen field,
`_validated_order` refuses an ordering that is not a permutation, and `_verify_integrity`
refuses a finished document whose organisations, titles, periods or skill names are not a
subset of the original's. A CV that gained an employer raises `TailorError` and nothing is
written.

The two paths do share what they can: `cv/tailor.py` imports `extract_resume_keywords` and
`_parse_json_response` from `resume_tailor.py`, so both aim at the same keywords and both
survive the same chatty, markdown-fenced completions. `cv_parser.py` stays on the
`resume_tailor.py` side of the line — it extracts text from a PDF and never produces one,
and the CV builder has no need of it because its documents were never a PDF to begin with.
`exporter.py` is unrelated to both: it serialises `JobListing` rows to CSV and JSON, and
touches no document format at all.

Which of the two files to actually send is a judgement call about applicant tracking
systems, not an architectural one; [docs/CV_BUILDER.md](docs/CV_BUILDER.md) makes the
recommendation.

## Data flow

One `job-scout run` executes these stages in order. Each is cheaper than the one it feeds,
which is the whole point of the ordering: the expensive model only ever sees listings that
already cleared the free filters.

1. **Invalidate stale evaluations.** `evaluation_fingerprint(config)` hashes the prompt
   version, provider, model and the profile/track/reject text. If it differs from the
   `eval_fingerprint` row in `meta`, every cached `fit_score` is cleared — old verdicts
   answer a question no longer being asked.
2. **Auto-prune**, when `prune_enabled`. Sweeps active jobs for filled or closed postings
   before adding more.
3. **Scrape.** All sources in parallel, deduplicated within the batch.
4. **Deduplicate against the database** by URL, then by normalised title+company. Skipped
   entirely under `--full`.
5. **Title filter.** Rule-based, free, morpheme-aware.
6. **Title screen.** Batched LLM pass over the surviving titles.
7. **Commute filter.** Geocode, route by car, bike and public transport, then apply the
   per-mode ceilings and `max_distance_km`. Runs before any expensive model call: whether
   you could get there is settled before whether you would want to.
8. **Quick eval.** One cheap LLM call scores a job against all career tracks at once; the
   best-scoring track is recorded on the job. Below `quick_eval_threshold` it stops here.
9. **Full evaluation.** Fit score with reasoning, negative-criteria check, and salary and
   vacation extraction.
10. **Compensation filter.** Applies `min_salary`, `max_salary` and `min_vacation_days`,
    with the `salary.py` regex as a backstop. Genuinely unknown pay passes through.
11. **Verify still open.** Each match is re-checked live; filled ones are marked `EXPIRED`
    rather than notified.
12. **Enrich with the official source** — the employer's own posting URL and its
    availability.
13. **Enrich with a company review** — cached per company, not per job.
14. **Notify.** Cross-source duplicates are collapsed first, then the configured channel
    sends per job or as one digest, and any previously failed sends are retried.
15. **Persist run statistics** — per-stage counts and timings into the `runs` table.

`--dry-run` skips persistence and notification but runs every filter. `--full` bypasses
dedup, upserts instead of inserting, and re-notifies existing matches.

## Persistence model

One SQLite file per user, opened per operation through a context manager that commits on
success and rolls back on exception. `database.py` creates 16 tables and applies additive
`ALTER TABLE` migrations on every open, so an older database upgrades in place.

| Table | Holds |
| --- | --- |
| `jobs` | Every listing ever seen, matched or rejected, with scores, reasoning, salary, travel data, lifecycle status and official-source fields. |
| `runs` | One row per pipeline run: per-stage counts, errors and duration. Backs `runs history` and the Analytics tab. |
| `geocode_cache`, `travel_time_cache` | Address→coordinates and route→minutes, expiring after `geocode_cache_days` (90) and `travel_cache_days` (14). |
| `cv_cache` | Parsed `CvProfile` JSON keyed by a hash of the CV text. |
| `tailored_resumes`, `cover_letters`, `screening_questions` | Generated application material, keyed by job id. |
| `company_research`, `company_reviews`, `person_search_cache` | Enrichment results, cached with a max age. Research rows are keyed by job id and also carry `company_key`, the lower-cased, whitespace-collapsed company name the review cache uses, so any vacancy at the same employer finds them. Under a placeholder name ("Unknown", blank) only the vacancy's own row is read. Older databases gain the column on open and have it filled from the vacancy's company. |
| `company_lookups` | One row per company key, kind (`research`, `review`) and outcome (`found`, `nothing_found`, `failed`) with the time it last happened. Read by `company_lookups.py` to decide whether a lookup may run again. |
| `star_stories` | The reusable STAR story bank, read by `interview_prep.py` to match stories to questions and by `interview_answers.py` as the only anecdotes a draft answer may contain. |
| `memories` | The applicant's memories: text, kind, tags and uses (as JSON), hint, the private flag, origin and the vacancy it came from. A database whose table lacks a column added later gains it with its default on open. Read and written only through `memories.py`. |
| `captured_notes` | One row per notes text turned into memories, keyed by a hash of the normalised text, with `memories_added` NULL while the capture runs. The claim is one upsert, so two captures of the same notes cannot both run; an unfinished claim older than 30 minutes may be taken over. Kept when memories are deleted. |
| `forgotten_memories` | The text and private flag of every deleted memory, keyed by the text, written in the same transaction as the delete. Automatic capture leaves these facts out, so notes typed again with a change do not bring a deleted memory back. Erased only by `memory forgotten --clear`. |
| `meta` | Small key/value rows; currently the evaluation fingerprint. |

**Dedup keys.** `jobs.url` carries a `UNIQUE` constraint and is the primary identity. The
secondary key is `dedup_key`, a lowercased, whitespace-collapsed `"title||company"` string
stored in its own indexed column and backfilled for rows written before the column
existed. `is_duplicate()` checks URL first, then `dedup_key`, so the same vacancy posted to
Indeed and LinkedIn is seen once.

**Evaluation cache.** `get_cached_evaluation()` looks up the most recent evaluated row
with a matching `dedup_key` and reuses its fit score, reasoning, salary and vacation
fields. That is correct only while the question stays the same, which is exactly what the
fingerprint in stage 1 guards.

**Lifecycle.** `JobStatus` is a `StrEnum`: `new`, `viewed`, `approved`, `ready`,
`submitted`, `interviewing`, `offer`, `rejected`, `expired`, plus the legacy `matched`.
Transitions go through `update_job_status()`, which validates them rather than letting any
status become any other.

**Notification retry.** A failed send sets `notification_pending`; the next run picks those
rows up before sending anything new, so a transient network failure never silently loses a
match.

## Concurrency model

The process is threaded, not single-threaded. `Config.max_parallel_evaluations` (per user,
default 5) is the width used by every parallel stage.

- `scraper.py` runs each source in its own worker, so one slow board does not hold up the
  rest.
- `cli.py` uses a `ThreadPoolExecutor` for the commute filter, quick eval and full
  evaluation, submitting all jobs up front and consuming results with `as_completed`.
- `title_screener.py` parallelises its batches.
- `travel.py` fans out the routing calls for a single job. Nominatim is the exception: a
  module-level lock and a 1.1 s minimum interval serialise geocoding to respect the public
  instance's rate limit.

**Stopping a run.** The dashboard's stop button does not kill anything. `progress.request_stop()`
sets a flag on the active run; `_stop_checkpoint(executor)` is called between items and,
when the flag is set, calls `executor.shutdown(wait=False, cancel_futures=True)` and raises
`RunStoppedError`. Work that has not started is dropped and only the handful already in
flight finishes, so a stop takes seconds rather than the rest of the stage. Everything
already persisted stays, and the next run resumes roughly where the stopped one left off.

`progress.py` tracks which user's run is active on the current thread through a
`ContextVar`, so the reporting helpers do not have to be threaded through every call site.
Nothing about progress is persisted — the pipeline runs in the dashboard process, so an
in-process registry is enough.

## LLM provider abstraction

`llm/base.py` defines a small `LLMClient` protocol: `complete(prompt, *, purpose, timeout)`
and `check_available()`. `CallPurpose` is a `Literal` of ten values — `evaluation`,
`quick_eval`, `screening`, `keywords`, `cv_parsing`, `resume_tailoring`, `cover_letter`,
`screening_questions`, `screening_answers`, `behavioral_questions` — and is the hint each
client uses to pick a model, a timeout and, locally, whether to keep a reasoning block on.

Four implementations: `local.py` (any OpenAI-compatible server), `zai.py` (Z.AI GLM over
REST), `claude_cli.py` and `kilo_cli.py` (subprocess CLIs). `local` is the default, because
it needs no API key.

`llm/factory.py` builds the client. When `quick_eval_provider`, `screening_provider`,
`evaluation_provider`, `keywords_provider` or `cv_parsing_provider` differs from
`llm_provider`, it builds a separate client for that purpose and returns a dispatching
wrapper — this is how a cheap model screens titles while a strong one makes the final call.
The result is wrapped in `llm/retry.py`'s `RetryingLLMClient`, which retries
`llm_max_attempts` times with a delay that doubles from `llm_retry_base_delay`.

`local.py` adds two things worth knowing about: it tries `local_base_url` and then each of
`local_fallback_base_urls` in order (a LAN address plus a VPN address, typically), and it
falls back gracefully when a server rejects a thinking/reasoning parameter it does not
support.

Callers never construct a client directly. They take one from `get_llm_client(config)` and
pass a `purpose`.

## Notification abstraction

The same shape, one layer thinner. `notify/base.py` defines a `Notifier` protocol with
`send(job)`, `send_digest(jobs)` and `check_available()`. `notify/factory.py` dispatches on
`Config.notification_channel` to `NtfyNotifier`, `EmailNotifier`, `SlackNotifier` or
`DiscordNotifier` and raises `NotificationError` on a misconfigured channel. Each
implementation formats for its own medium — Slack Block Kit, Discord embeds, an HTML email
body — rather than sharing one lowest-common-denominator string.

`notification_mode` decides whether the pipeline calls `send` per match or `send_digest`
once at the end.

## Multi-user data layout

Everything lives under one root, `data/` by default and overridable with
`JOB_SCOUT_DATA_DIR` (the Docker image sets it to `/data`).

```text
data/
├── config.yaml              # global settings (GLOBAL_FIELDS only)
├── secrets.yaml             # API keys and the dashboard token
├── jobs.db                  # legacy single-user database
├── logs/                    # legacy single-user logs
└── users/
    └── <name>/
        ├── config.yaml      # this user's overrides (USER_FIELDS)
        ├── jobs.db          # this user's database
        ├── logs/            # one log file per run
        └── cv/              # CV builder root; profiles/<slug>/cv.json + uploads/
```

The split is enforced in `config.py`: `GLOBAL_FIELDS` is an explicit frozenset (providers,
models, SMTP relay, scheduler and wake settings), `SECRET_FIELDS` never touches either YAML
file, and `USER_FIELDS` is everything else in the `Config` model. `build_effective_config(name)`
merges global, then user, then secrets into one `Config` — so a user file only needs to
carry what it actually overrides. `load_llm_config()` is the user-agnostic subset used
where only a client is needed.

Passing `--user <name>` selects that user's config and database; omitting it uses the
legacy global pair. `--all` iterates every directory under `data/users/`. CV profiles are
the one per-user store that is not SQLite: `user_cv_dir(name)` returns the `cv/` directory
above, and `ProfileStore` derives `profiles/<slug>/` from it. The `cv` commands resolve the
user through the same `_require_single_user()` helper as the rest of the CLI, so they are
optional-`--user` on a single-user install and refuse to guess on a multi-user one.

> Known bug: `approval queue` and `approval approve` accept `--user` and validate it,
> but then open the global `data/jobs.db` instead of the user's. On a multi-user install they operate on a database the pipeline never writes to.

## Web and API layer

`web/app.py` is a FastAPI application factory and the single-page dashboard in
`web/static/`. Fourteen tabs (Dashboard, Profile & Filters, Document Review,
CV Builder, Memories, Cover Letter Writer, Interview Questions, Keywords, Custom Sites,
Notifications, LLM Settings, Secrets, Schedule, Analytics) over a JSON API grouped by
resource (`/api/jobs/*`, `/api/config`, `/api/profile/*`, `/api/coach/*`, `/api/llm/*`,
`/api/cv/*`, `/api/letters/*`, `/api/interview/*`, `/api/memories/*`, `/api/schedule`,
`/api/run*`).

**The CV Builder tab** is the one tab that is not built from `web/static/app.js`. It is an
iframe pointed at `/cv/?user=<name>`, which serves the vendored editor's own page with its
asset links rewritten from `/static/` to `/cv/` and a one-line script injected to set
`window.CV_API_BASE = "/api/cv"`. The vendored markup is never modified on disk, so the
standalone server keeps serving the same file unchanged. `app.js` sets the iframe's `src`
the first time the tab is opened and again when the selected user changes — never on page
load, because the editor renders a PDF preview as it starts. Importing an existing CV
(`GET /api/cv/import/options`, `POST /api/cv/import`, in `web/cv_import.py`) is declared on
the dashboard too, for the same reason as tailoring: it needs the user's settings and LLM.
The editor only shows its Import button when the options route answers. Tailoring
(`POST /api/cv/profiles/{slug}/tailor`) is declared on the dashboard rather than on the
vendored router, because it is the only CV operation that needs the jobs database and the
user's LLM settings; it answers 502, not 500, when the model returns something unusable.

**Running the pipeline.** `POST /api/run` starts it in a daemon thread and returns
immediately. `GET /api/run/status` reads the `progress` registry — stage, counts, per-stage
seconds, ETA. `POST /api/run/stop` sets the cooperative stop flag described above.

**Authentication.** A `TokenAuthMiddleware` (`starlette.middleware.base.BaseHTTPMiddleware`)
compares a `Bearer` header against `dashboard_token` using `secrets.compare_digest`. If no
token is configured, **every request is allowed** — the dashboard is unauthenticated by
default and should not be exposed beyond a trusted network without setting
`JOB_SCOUT_DASHBOARD_TOKEN`. See [SECURITY.md](SECURITY.md) and
[docs/WEB_DASHBOARD.md](docs/WEB_DASHBOARD.md).

`run_server()` serves the app with uvicorn, defaulting to `0.0.0.0:8000`. The shipped
Docker Compose files always pass `--port` explicitly, defaulting to `24817`.

The dashboard does not cover the entire CLI: `prune`, `find-sources`, `company-review`,
`profile tailor-resume`, `profile get-resume` and `mcp start` have no route (only
`/api/mcp/status` and `/api/auto-schedule/test-wake` touch the last two areas).

## Scheduling

Two independent mechanisms, for two different deployments.

- **Host crontab** (`scheduler.py`, `job-scout schedule install|status|remove`) writes
  marker-tagged entries that call `job-scout run`. Per user, with `schedule_hour`,
  `schedule_minute`, `schedule_days` and `schedule_paused`.
- **Container loop** (`weekly_schedule.py`, `job-scout schedule loop --all`) is what
  `docker-compose.yml` actually runs. It re-reads `schedule_slots`, `schedule_timezone`
  and the wake settings from config every cycle, so the dashboard can change the schedule
  of a running container. An unparseable slot string pauses the loop rather than crashing
  it. Before each run it optionally sends a Wake-on-LAN packet (`wol.py`) and polls
  `llm_health_url` until the model host answers.

Host networking is required for the container because magic packets are layer-2 broadcasts
that do not survive bridge NAT; the desktop override drops to bridge networking and
therefore loses Wake-on-LAN. See [docs/DEPLOY.md](docs/DEPLOY.md).

## Key design decisions

- **Cheapest filter first.** Four screening stages exist so that the rule-based filter
  costs nothing, the batched title screen costs almost nothing, and only a few dozen
  listings ever reach a full evaluation. The commute check sits deliberately ahead of the
  expensive passes.
- **Pluggable providers, local by default.** The tool has to be usable with no API key and
  no data leaving the house, so `local` is the default. Per-purpose routing exists because
  screening and final judgement genuinely want different models.
- **SQLite, one file per user.** No server to run, trivially backed up, and it makes
  per-user isolation a matter of choosing a path.
- **Fail-open filters, fail-closed pruning.** Unreachable travel APIs or ungeocodable
  locations let a job through rather than silently dropping it. The pruner is the mirror
  image: it only marks a vacancy dead on a strong, explicit signal, so a live vacancy is
  never expired by an ambiguous page.
- **Cooperative stop, not cancellation.** A run is asked to stop between items so partial
  work is persisted and consistent, instead of being killed mid-write.
- **Cache with a fingerprint, not a TTL.** Evaluations are reused indefinitely while the
  question is unchanged, and invalidated wholesale the moment the profile, tracks, reject
  criteria, model or prompt version moves.
- **Notification retry over guaranteed delivery.** Failed sends are flagged and retried on
  the next run; no queue, no broker.

### Letter writer

`letters/writer.py` reads the applicant's facts from every source through
`applicant.py` without modifying any of them, separates facts from historical style
examples, and calls the existing `cover_letter` LLM
purpose. `letters/api.py` is mounted under the dashboard token middleware. The
separate `web/static/letters.js` editor discards stale responses after a user switch.
Drafts and examples live in `user_letters_dir(name)`; the last saved draft is also
mirrored into the legacy database cover-letter field. No examples or personal
style guide are shipped with the package.

### Memories in the dashboard

`web/memories_api.py` is the `/api/memories` router behind the Memories tab
(`web/static/memories.js`, the same token helper, staleness guard and disabled-fieldset
pattern as the letter editor). It is a thin layer over `memories.py` and
`memory_extract.py`: list, add, change and delete, `/extract` (proposals for a pasted
text, nothing stored) and `/batch` (the proposals the applicant kept), `/read-file`,
the per-user capture switch and the list of deleted memories. It also holds
`start_capture`, which the letter route and both interview routes call after a
generation: when `capture_pending` says a capture will run, it adds
`capture_from_notes` to the request's FastAPI `BackgroundTasks` with the model the
generation used and an origin naming the vacancy, and sets `X-Memory-Capture: started`
so the page can say new memories may appear. The task runs after the response is sent,
so a capture never delays or fails a letter or interview set.
