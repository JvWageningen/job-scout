# Interview questions and answers

Both directions of the same conversation, in one guide and one dashboard tab.

**Questions to ask them.** For the moment near the end of an interview when the
interviewer says "do you have any questions for us?", job-scout writes the questions
worth asking this particular employer, from the vacancy, whatever is already known
about the company, and your own CV. The aim is not to have something to say. It is to
leave the room knowing things you could not have looked up: why the role is open, what
has already been tried, who decides, what would make it fail.

**Questions they may ask you.** The mirror image: the questions this interviewer is
likely to put to *you*, each with a draft answer you could actually give. The
prediction is the easy half. The answers are the point — and the reason the rules
below are strict, because an answer that invents an employer, a tool or a number is
found out by the person sitting opposite you, while you are saying it.

| Half | Direction | Produces | Built from |
| --- | --- | --- | --- |
| **Questions to ask them** | You → employer | Questions grouped by theme, each with why it matters for you and what it was based on. | Vacancy, company research, company review, your CV. |
| **Questions they may ask you** | Employer → you | Likely questions grouped by what they probe, each with why it is coming, a draft answer, and how much real evidence stands behind it. | The same four, plus your STAR story bank. |

Neither half saves anything, neither researches anything on demand, and running one has
no effect on the other.

## This is not interview prep, and not Document Review

Four features sit near each other and answer different questions. Reach for the right
one:

| Feature | Direction | What it produces |
| --- | --- | --- |
| **Questions to ask them** (this document) | You ask the employer | Questions to ask, grouped by theme, each with why it matters for you and what it was based on. |
| **Questions they may ask you** (this document) | The employer asks you | Predicted questions with a draft answer for each, grounded in your CV and your STAR stories, honest where you have a gap. |
| **Interview prep** (`profile interview-prep`) | The employer asks you | The behavioural questions a posting is likely to ask, each matched to the STAR story from your bank that best answers it. |
| **Document Review** | Neither — it judges your paperwork | A critique of your CV, or of a motivational letter against the vacancy it answers. |

### Which of the two "they ask you" features do I want?

They overlap in purpose and share nothing but the story bank.

`profile interview-prep` is the older, smaller one. It reads **the job description
alone**, derives behavioural questions from it, and matches each one to the two STAR
stories whose keywords fit best. No company research, no CV, no draft answers, no
dashboard, and it exits 1 if your story bank is empty. It is a fast way to ask "which
of my stories do I need for this posting?".

`interview answers` is the full version: it sees the vacancy *and* the cached company
research and review *and* your CV Builder profile *and* the story bank, writes the
answer rather than pointing at a story, marks how much evidence each answer really has,
and works even with no stories saved. Use it when you have an actual interview.

Both remain supported and neither touches the other's data.

---

## Part one — the questions you ask them

### What the questions are grounded in

Four sources, all of them material the pipeline already gathered. Nothing is scraped,
researched or reviewed on demand, so generating questions costs one model call and never
changes your data.

| Source | What it contributes |
| --- | --- |
| **The vacancy** | Title, employer, location and the description text (the first 16,000 characters). This is what makes a question specific: a product, a technology, a standard, a stated challenge. |
| **Company research** | Industry, company size, culture indicators, tech-stack hints, growth signals and research notes, read from the cache for that vacancy. |
| **Company review** | The work-quality review for that employer, up to a year old: score, summary, pros, cons, employee sentiment, financial health, growth, company age and confidence. |
| **Your CV** | The factual sections of the selected CV Builder profile — the same view the cover letter writer uses. Contact and personal-detail sections are excluded and stay excluded. |

The cons of a review and the culture indicators of the research are the most valuable
material here. A question that quietly probes a real reported weakness — asked as "how
is that handled now", never as an accusation and never by quoting a review back at the
interviewer — is worth ten generic ones.

### The themes

Every question is filed under one of six themes, so a set spreads across the
conversation instead of clustering. The dashboard groups them in the order below — the
role first, what is worth probing last, when some trust has been built — and the CLI
prints the same grouping.

| Theme | Dashboard heading | Typically asks about |
| --- | --- | --- |
| `role` | The role | What you would own, how your experience would actually be used, why the role is open. |
| `team` | The team | Who you would work with, how the team is composed, who decides. |
| `company` | The company | Market, product, direction, the business behind the vacancy. |
| `growth` | Growth and future | Where the role and the company are heading, and what that means for you. |
| `ways_of_working` | Ways of working | Process, standards, tooling, how work actually gets done. |
| `concerns` | Worth probing | The uncomfortable but genuine questions, including anything the grounding flagged as a weakness. |

Each question arrives with two extra lines: **why this matters for you**, one sentence
on what the answer would tell *you specifically* given your CV, and **based on**, naming
the source it came from (`company review: cons`, `vacancy`, `your CV`, `culture`). Read
both — they are how you judge whether a question is worth spending.

Questions that would fit any employer unchanged are asked for explicitly *not* to be
written: "what does a typical day look like", "what is the culture like", "where do you
see the company in five years" and "what makes someone successful here" are banned in
the prompt.

### The one rule about money

Questions about salary, holiday or benefits are **off by default** — the model is told
not to raise them. The switch is a literal word match on your notes, in either language.
Most of the triggers are matched as word *stems*, so any inflection counts (`salaris`
also catches *salarissen*, `verdien` catches *verdienen*, `arbeidsvoorwaard` catches
*arbeidsvoorwaarden*):

- Stems: *salaris*, *salary*, *compensation*, *remuneration*, *benefit*,
  *arbeidsvoorwaard*, *secundaire*, *vakantie*, *verlof*, *holiday*, *vacation*,
  *pensioen*, *pension*, *verdien*, *beloning*, *vergoeding*, *inschaling*.
- Whole words only, because they hide inside unrelated ones: *pay*, *loon*, *bonus*,
  *aanbod*, *schaal*.

Use one of them in your notes and up to two concrete questions about pay or conditions
become allowed. Your own notes are the only thing that can put money on the list. This
switch belongs to this half only; it has no effect on the answers half.

---

## Part two — the questions they may ask you

### What it predicts, and what it drafts

One model call returns a set of likely questions. Each one carries:

| Field | What it is |
| --- | --- |
| **The question** | Phrased the way the interviewer would actually ask it. |
| **Why they may ask this** | One sentence naming what in *this* vacancy, *this* employer or *your* CV prompts it — not a general remark about interviewing. |
| **A draft answer** | First person, spoken, plainly worded, roughly 60 to 150 words. A starting point you edit, not a script. |
| **Footing** | How much real evidence you have for that answer. See below. |
| **Based on** | The CV entries or STAR stories the answer draws on, named as the grounding names them (`CV: Ervaring`, `STAR story 3`). Empty is the correct value when the honest answer draws on nothing. |

Questions are filed under six kinds, so a set spreads over an interview instead of
clustering on one subject. The dashboard groups them in this order and uses these
headings; the CLI prints them in the order the model returned, numbered, with the kind
in brackets.

| Kind | Dashboard heading | Typically asks about |
| --- | --- | --- |
| `motivation` | Motivation and fit | Why this role, why this employer, why now, why you are leaving. |
| `experience` | Your experience | What you have actually done, and how it maps onto what the vacancy asks. |
| `technical` | Technical depth | The tools, methods and standards the posting names. |
| `behavioural` | How you work with others | How you handled a situation rather than what you know — the kind of question a STAR story answers. |
| `gap` | Gaps they will probe | A requirement your CV does not meet, an unexplained gap, a short stint. |
| `practical` | Practical matters | The practicalities around the role rather than the work itself. |

The uncomfortable questions are asked for on purpose. Why you are leaving, an
unexplained gap, a requirement you do not meet, why this employer and not another — a
real interviewer asks those, and leaving them out would make the whole exercise
pointless. They are exactly the ones worth rehearsing.

### The rule the answers live by

**An answer may use only what is in your CV, your STAR stories and your notes.** Never
an invented employer, project, tool, number, qualification or achievement. Where the
evidence is not there, the honest answer *is* the answer.

That is not politeness, it is self-preservation: a borrowed achievement gets found out
in the room, by the person who just asked about it. So the prompt states the rule, the
draft for a gap is written as an admission rather than a dodge, and the `footing` field
exists so you can see at a glance which answers are standing on something real.

### What the footing values mean

| Footing | Dashboard label | CLI note | What to do with it |
| --- | --- | --- | --- |
| `strong` | Backed by your CV | *your CV or a STAR story carries this* | The material is there. Make the wording yours and move on. |
| `partial` | Partly covered | *only adjacent experience, so it has to be framed* | You have something near it. Check that the framing is one you would defend if pushed. |
| `gap` | Gap — rehearse this | *you do not have this; rehearse saying so plainly* | **This is the one to rehearse.** |

A `gap` answer says so in its first sentence, then says what is adjacent and how you
would close it. No bluffing, no padding, no changing the subject. Said straight — "my
background is in X, not Y; I would like to build that" — it reads as confidence rather
than as weakness, but only if you have said it out loud before, which is the entire
reason these are marked.

The dashboard marks a gap block in amber and counts them in the summary line. The CLI
prefixes those questions with `!!` and closes with a count: *N answers marked !! GAP:
you do not have that experience, so rehearse the honest version before someone asks for
it.*

### The STAR story bank feeds the answers

Your STAR stories are the only anecdotes an answer is allowed to contain. They are
yours, you wrote them, you stand behind them — which is precisely what makes them safe
to build an answer on. Where a story fits a question, the answer is built out of it (the
situation, what you actually did, how it ended) and the story is named in **based on**.

This is the same bank `profile interview-prep` matches against — one store, reused, not
a second copy. Up to 20 stories are sent, each field trimmed to 1,200 characters.

Add one with the existing command:

```bash
uv run job-scout profile star-story add --user alex \
  --situation "The measurement pipeline at Deltameet Institute stalled every Monday." \
  --task "Find the cause and keep the weekly report on time." \
  --action "Traced it to a lock held by the archiver and rewrote the batching." \
  --result "Runtime fell from 40 minutes to 6; the report has not been late since." \
  --keywords "troubleshooting,data pipeline,ownership"
```

`profile star-story list`, `profile star-story update --id N` and `profile star-story
delete --id N` manage the rest. Full flag reference: [`profile star-story`](USAGE.md#profile-star-story). Note the
collision documented there — the positional `ACTION` selects the mode while `--action`
carries the story's action text.

**An empty bank is allowed.** Unlike `profile interview-prep`, which stops, this half
carries on: the model is told in so many words that there are no stories and that it
must build every answer from the CV and your notes alone rather than invent an anecdote
to fill the space. You get fewer `strong` footings and thinner behavioural answers. Add
two or three stories and the difference is immediate.

### Notes

The notes box carries what only you know: why you are leaving, something from an earlier
call, a constraint you want handled well. It is quoted into the grounding like any other
source, and it is the only place a fact that is in neither your CV nor your stories can
legitimately enter an answer.

---

## What both halves share

### When a source is missing

Missing grounding is reported, not invented. Each absent source is named, the model is
told it is absent and told not to guess what it would have said, and the names come back
with the result:

| Reported as | Means | Half |
| --- | --- | --- |
| `no vacancy description` | The stored vacancy has no description text. | Both |
| `no company research yet` | No research is cached for this vacancy, or what is cached is unreadable. | Both |
| `no company review yet` | No review is cached for this employer within the last year, or it is unreadable. | Both |
| `no STAR stories saved yet` | The story bank is empty. | Answers |

The dashboard prints these under the result ("Some grounding was missing: …"), and the
CLI prints them under *Not seen, so nothing above is based on it* (questions) or *Notes
— not seen, so nothing above is based on it* (answers). If you want research-grounded
preparation, run [`company research`](USAGE.md#company-research) and
[`company-review`](USAGE.md#company-review) first, then generate.

A missing CV is different: it is a hard stop for both halves, not a gap. Save a CV in
[CV Builder](CV_BUILDER.md) before generating.

### How many you get

Thin material is the main way this degrades into padding, so the count follows the
grounding rather than a fixed target:

| Grounding available | Questions requested |
| --- | --- |
| Vacancy + CV + company research + company review | 8 to 12 |
| Vacancy + CV + one of research or review | 6 to 9 |
| Vacancy + CV only | 4 to 6 |

The same tiers apply to both halves. The prompt adds that fewer excellent items beat
more padded ones, that a question already asked may not be rephrased to reach the
number, and that one piece of evidence may not be stretched across several answers. The
story bank enriches the answers but does not raise the count.

### Language and CV selection

**Language.** *Automatic* reads the vacancy description and decides Dutch or English
with a stopword vote; if the description is missing it votes on the title, and if the
text is too short or evenly split it falls back to Dutch. Choose `nl` or `en` explicitly
for mixed-language postings. In the answers half the choice covers the questions *and*
the answers, so a Dutch interview gets Dutch you could actually say out loud rather than
translated English.

**CV.** Leave the CV choice on *Automatic* and the profile is picked by the chosen
language: `nederlands` for Dutch and `default` for English when one exists in that
language, otherwise any saved profile in that language, otherwise the first saved
profile. Name a profile yourself to override this. This is exactly the letter writer's
rule, using the same helper — the three features cannot drift apart.

### In the dashboard

**Prepare applications → Interview Questions**. One tab, two halves, switched with the
pair of buttons under the heading (or with the left and right arrow keys). A badge beside
the heading says which half you are in: *You ask the employer* or *They ask you*.

1. Select a single user. The tab needs one user, not **all**.
2. Choose the vacancy. The dropdown offers the vacancies still worth working on —
   nothing the pipeline rejected, nothing expired, nothing you closed, and nothing
   without a description — best match first, with the fit score beside each one. The
   Cover Letter Writer offers the same shortlist from the same code.
3. Choose the language and the CV, or leave both on *Automatic*.
4. Add anything the model should know, then press the generate button for the half you
   are in: **Generate questions to ask** or **Generate questions & draft answers**. It
   can take a minute or more.

The setup — vacancy, language, CV, notes — is shared, so you can prepare one half and
then the other without re-entering anything, and switching halves leaves a generated
result standing.

**Questions to ask them** appear grouped by theme, each with why it matters and what it
was based on. **Questions they may ask you** appear grouped by kind, each as a block
with its footing chip, why they may ask it, an **editable answer box** and its *based
on* line. The box is the point: edit the draft until it sounds like you. **Copy all**
takes what is on screen — your edits, not the original draft — as plain text.

**Refresh vacancies & CVs** re-reads both dropdowns without touching anything already
generated. Switching user resets the whole tab.

Nothing here is saved. Both results, and your edits to an answer, live in the page until
you copy them or regenerate. There is no saved-version store as there is for letters,
and generating changes no vacancy's progress and sends nothing to anyone.

### CLI

```bash
uv run job-scout interview questions 42 --user alex
uv run job-scout interview answers 42 --user alex
uv run job-scout interview answers 42 --user alex --language en --cv default
uv run job-scout interview answers 42 --user alex --notes "Leaving because the team was cut"
```

Both take the same options: `--user`, `--language auto|nl|en`, `--cv`, `--notes`. Full
reference: [`interview questions`](USAGE.md#interview-questions) and
[`interview answers`](USAGE.md#interview-answers).

`questions` prints the set grouped by theme, each question followed by its `why:` and
`from:` lines. `answers` prints a numbered list in the order the model returned, each
entry showing the kind, `asked because:`, `footing:` with a plain-language note, `based
on:` where there is one, and the draft answer wrapped to 88 columns. Gaps are prefixed
`!!` and counted at the end. Both list any missing grounding last.

### Privacy

One model call per generation, both routed through the existing `behavioral_questions`
purpose — the same routing entry interview prep uses, so no new provider setting appears
and nothing needs configuring. See [LLM providers](LLM_PROVIDERS.md) for per-stage
routing; a local provider keeps this request on your own infrastructure, a hosted one
receives it.

What that call carries: the vacancy title, employer, location and description; the
cached company research and review fields listed above; the factual sections of the
selected CV; your notes; and, for the answers half, your STAR stories. What it does not
carry: your contact details and personal-detail sections, which `cv_facts` excludes on
purpose, and anything from another user's data.

Nothing is written. Both generators read the database and the CV store and return a
result; they store no questions and no answers, update no vacancy and start no research.
The `/api/interview/*` routes sit behind the dashboard's optional shared bearer token
like every other `/api/` route — see
[Authentication](WEB_DASHBOARD.md#authentication).

### Limits

Read this before you take a printout into a room.

#### Both halves

- **They are only as good as the company research behind them.** With no research and no
  review, the model has the vacancy text and your CV and nothing else, and the output
  gets noticeably more generic. The missing-context line is there so you can see that
  rather than guess it. Research the company first if the interview matters.
- **The spread is asked for, not enforced.** No theme may take more than three questions
  and no kind more than four — but nothing in the code rejects a set that clusters.
- **Only exact repeats are removed.** Two entries differing solely in punctuation or
  capitalisation count as one; two different phrasings of the same idea both survive.
- **Sources are named, not verified.** `based on` and `grounded_in` say where the model
  believes something came from. That is a useful audit line, not a guarantee.
- **The review can be up to a year old.** Cached reviews are accepted within that window,
  which is the same window the vacancy list uses, so the preparation sees exactly what
  you see — including when it is stale.
- **Nothing is saved.** Copy what you want to keep before you regenerate or switch user.

#### The questions you ask them

- **Read every question before you ask it.** A model wrote these. A question can be built
  on research that was wrong, on a review that described a different part of the company,
  or on a detail that has since changed. Asking a question whose premise is false costs
  you more than not asking it.
- **A question you would not ask out loud is not your question.** Drop anything that does
  not sound like you, and drop anything the conversation has already answered — asking it
  anyway signals that you were reading a list instead of listening.

#### The questions they may ask you

- **A draft answer is a starting point. It is not your answer until you make it so.** It
  is written to be spoken, but it was not spoken by you. Read every one out loud, cut
  what you would never say, put back the detail only you know. An answer recited from a
  page sounds exactly like an answer recited from a page, and the dashboard gives you an
  editable box for precisely this reason.
- **Predicted questions are a guess at this interviewer, not a script.** They come from
  the vacancy text and the cached material, not from the person who will sit opposite
  you. Expect questions nobody predicted, and expect half of these never to come up.
  Preparing the thinking is the value; matching the list is not.
- **Footing is the model's judgement of the evidence, not a check of it.** The code
  verifies one thing only: if an answer cites a STAR story when your bank was empty, a
  warning goes to the log — not to the screen. Everything else is the model's word.
  Reading each answer against what you have actually done is your job and cannot be
  delegated.
- **An answer with nothing behind it is the one to fix first.** Sort by footing in your
  head: the `gap` answers are where an interview is actually won or lost, and they are
  the ones you should say out loud, to a wall if necessary, before the day.
- **With an empty story bank the behavioural answers thin out.** They are built from CV
  facts alone, which reads as competent and abstract. Two or three real stories change
  that more than any amount of rewriting.

### Related documentation

[CV Builder](CV_BUILDER.md) · [Cover Letter Writer](LETTER_WRITER.md) ·
[Web dashboard](WEB_DASHBOARD.md) · [CLI reference](USAGE.md) ·
[LLM providers](LLM_PROVIDERS.md) · [Architecture](../ARCHITECTURE.md)
