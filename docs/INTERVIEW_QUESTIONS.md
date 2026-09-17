# Interview questions

The questions **you** ask **them**. For the moment near the end of an interview when
the interviewer says "do you have any questions for us?", job-scout writes the
questions worth asking this particular employer, from the vacancy, whatever is
already known about the company, and your own CV.

The aim is not to have something to say. It is to leave the room knowing things you
could not have looked up: why the role is open, what has already been tried, who
decides, what would make it fail.

## This is not interview prep, and not Document Review

Three features sit near each other and answer different questions. Reach for the
right one:

| Feature | Direction | What it produces |
| --- | --- | --- |
| **Interview questions** (this document) | You ask the employer | Questions to ask, grouped by theme, each with why it matters for you and what it was based on. |
| **Interview prep** (`profile interview-prep`) | The employer asks you | The behavioural questions a posting is likely to ask, each matched to the STAR story from your bank that best answers it. |
| **Document Review** | Neither — it judges your paperwork | A critique of your CV, or of a motivational letter against the vacancy it answers. |

They share no code and no storage. Interview prep needs a STAR story bank; this
needs a CV Builder profile. Running one has no effect on the other.

## What the questions are grounded in

Four sources, all of them material the pipeline already gathered. Nothing is
scraped, researched or reviewed on demand, so generating questions costs one model
call and never changes your data.

| Source | What it contributes |
| --- | --- |
| **The vacancy** | Title, employer, location and the description text (the first 16,000 characters). This is what makes a question specific: a product, a technology, a standard, a stated challenge. |
| **Company research** | Industry, company size, culture indicators, tech-stack hints, growth signals and research notes, read from the cache for that vacancy. |
| **Company review** | The work-quality review for that employer, up to a year old: score, summary, pros, cons, employee sentiment, financial health, growth, company age and confidence. |
| **Your CV** | The factual sections of the selected CV Builder profile — the same view the cover letter writer uses. Contact and personal-detail sections are excluded and stay excluded. |

The cons of a review and the culture indicators of the research are the most
valuable material here. A question that quietly probes a real reported weakness —
asked as "how is that handled now", never as an accusation and never by quoting a
review back at the interviewer — is worth ten generic ones.

### When a source is missing

Missing grounding is reported, not invented. Each absent source is named, the model
is told it is absent and told not to guess what it would have said, and the names
come back with the questions:

| Reported as | Means |
| --- | --- |
| `no vacancy description` | The stored vacancy has no description text. |
| `no company research yet` | No research is cached for this vacancy, or what is cached is unreadable. |
| `no company review yet` | No review is cached for this employer within the last year, or it is unreadable. |

The dashboard prints these under the questions ("Some grounding was missing: …"),
and the CLI prints them under the heading *Not seen, so nothing above is based on
it*. If you want research-grounded questions, run
[`company research`](USAGE.md#company-research) and
[`company-review`](USAGE.md#company-review) first, then generate.

A missing CV is different: it is a hard stop, not a gap. Save a CV in
[CV Builder](CV_BUILDER.md) before generating.

## The themes

Every question is filed under one of six themes, so a set spreads across the
conversation instead of clustering. The dashboard groups them in the order below —
the role first, what is worth probing last, when some trust has been built — and the
CLI prints the same grouping.

| Theme | Dashboard heading | Typically asks about |
| --- | --- | --- |
| `role` | The role | What you would own, how your experience would actually be used, why the role is open. |
| `team` | The team | Who you would work with, how the team is composed, who decides. |
| `company` | The company | Market, product, direction, the business behind the vacancy. |
| `growth` | Growth and future | Where the role and the company are heading, and what that means for you. |
| `ways_of_working` | Ways of working | Process, standards, tooling, how work actually gets done. |
| `concerns` | Worth probing | The uncomfortable but genuine questions, including anything the grounding flagged as a weakness. |

Each question arrives with two extra lines: **why this matters for you**, one
sentence on what the answer would tell *you specifically* given your CV, and **based
on**, naming the source it came from (`company review: cons`, `vacancy`, `your CV`,
`culture`). Read both — they are how you judge whether a question is worth spending.

Questions that would fit any employer unchanged are asked for explicitly *not* to be
written: "what does a typical day look like", "what is the culture like", "where do
you see the company in five years" and "what makes someone successful here" are
banned in the prompt.

## Language and CV selection

**Language.** *Automatic* reads the vacancy description and decides Dutch or English
with a stopword vote; if the description is missing it votes on the title, and if
the text is too short or evenly split it falls back to Dutch. Choose `nl` or `en`
explicitly for mixed-language postings.

**CV.** Leave the CV choice on *Automatic* and the profile is picked by the question
language: `nederlands` for Dutch and `default` for English when one exists in that
language, otherwise any saved profile in that language, otherwise the first saved
profile. Name a profile yourself to override this. This is exactly the letter
writer's rule, using the same helper — the two features cannot drift apart.

## Notes, and the one rule about money

The optional notes field carries context only you have: a doubt you want answered,
something from an earlier call, a subject you want on the table.

Notes also decide one thing on their own. Questions about salary, holiday or
benefits are **off by default** — the model is told not to raise them. The switch is
a literal word match on your notes, in either language: *salary*, *salaris*, *loon*,
*pay*, *compensation*, *benefit*, *arbeidsvoorwaarden*, *secundaire*, *holiday*,
*vacation*, *vakantie*, *verlof*, *bonus*, *pension* or *pensioen*. Use one of them
and up to two concrete questions about pay or conditions become allowed. Your own
notes are the only thing that can put money on the list.

## In the dashboard

**Prepare applications → Interview Questions**.

1. Select a single user. The tab needs one user, not **all**.
2. Choose the vacancy. The dropdown offers the vacancies still worth working on —
   nothing the pipeline rejected, nothing expired, nothing you closed, and nothing
   without a description — best match first, with the fit score beside each one. The
   Cover Letter Writer offers the same shortlist from the same code.
3. Choose the question language and the CV, or leave both on *Automatic*.
4. Add anything you want to raise, then click **Generate questions**. It can take a
   minute or more.
5. The questions appear grouped by theme, each with why it matters and what it was
   based on, followed by a note about any grounding that was missing. **Copy all**
   puts the whole set on the clipboard as plain text.

**Refresh vacancies & CVs** re-reads both dropdowns without touching questions you
have already generated. Switching user resets the tab.

Nothing here is saved. The questions live in the page until you copy them or
regenerate; there is no saved-version store as there is for letters, and generating
questions does not change a vacancy's progress or send anything to anyone.

## CLI

```bash
uv run job-scout interview questions 42 --user alex
uv run job-scout interview questions 42 --user alex --language en --cv default
uv run job-scout interview questions 42 --user alex --notes "Second round, team lead"
```

Full flag reference: [`interview questions`](USAGE.md#interview-questions).

The output is grouped by theme, each question followed by its `why:` and `from:`
lines, with any missing grounding listed at the end.

## Privacy

One model call, routed through the existing `behavioral_questions` purpose — the
same routing entry interview prep uses, so no new provider setting appears and
nothing needs configuring. See [LLM providers](LLM_PROVIDERS.md) for per-stage
routing; a local provider keeps this request on your own infrastructure, a hosted
one receives it.

What that call carries: the vacancy title, employer, location and description; the
cached company research and review fields listed above; the factual sections of the
selected CV; and your notes. What it does not carry: your contact details and
personal-detail sections, which `cv_facts` excludes on purpose, and anything from
another user's data.

Nothing is written. The generator reads the database and the CV store and returns a
result; it stores no questions, updates no vacancy and starts no research. The
`/api/interview/*` routes sit behind the dashboard's optional shared bearer token
like every other `/api/` route — see
[Authentication](WEB_DASHBOARD.md#authentication).

## Limits

Read this before you take a printout into a room.

- **The questions are only as good as the company research behind them.** With no
  research and no review, the model has the vacancy text and your CV and nothing
  else, and the questions get noticeably more generic. The missing-context line is
  there so you can see that rather than guess it. Research the company first if the
  interview matters.
- **Read every question before you ask it.** A model wrote these. A question can be
  built on research that was wrong, on a review that described a different part of
  the company, or on a detail that has since changed. Asking a question whose
  premise is false costs you more than not asking it.
- **A question you would not ask out loud is not your question.** Drop anything that
  does not sound like you, and drop anything the conversation has already answered —
  asking it anyway signals that you were reading a list instead of listening.
- **Eight to twelve are requested, and the spread is asked for, not enforced.** The
  model is told to keep any one theme under four questions; nothing in the code
  rejects a set that clusters. Only exact repeats are removed automatically — two
  questions differing solely in punctuation or capitalisation count as one, but two
  different phrasings of the same idea both survive.
- **Sources are named, not verified.** `grounded_in` says where the model believes a
  question came from. It is a useful audit line, not a guarantee.
- **The review can be up to a year old.** Cached reviews are accepted within that
  window, which is the same window the vacancy list uses, so the questions see
  exactly what you see — including when it is stale.

## Related documentation

[CV Builder](CV_BUILDER.md) · [Cover Letter Writer](LETTER_WRITER.md) ·
[Web dashboard](WEB_DASHBOARD.md) · [CLI reference](USAGE.md) ·
[LLM providers](LLM_PROVIDERS.md) · [Architecture](../ARCHITECTURE.md)
