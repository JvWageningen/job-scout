# Memories

A CV is written for every reader at once, so it leaves things out: a project that only
matters for one kind of role, a result you would rather not print, a wish about hours or
travel, a certificate you are still working on. Until now such facts lived in the notes
you typed for one letter or one interview, and were gone with it.

A **memory** keeps each of them as one statement about you that job-scout can use again
later: when it writes a letter, prepares an interview or tailors your CV to a vacancy, it
looks at your memories and uses the ones that fit that vacancy.

## What a memory holds

| Field | What it is |
|---|---|
| Text | One self-contained statement about you, at most 600 characters, in the language you wrote it in (Dutch or English). Names, numbers, dates and tools are kept exactly. |
| Kind | project, achievement, skill, experience, education, preference, constraint, personal or other. |
| Tags | Up to 8 lower-case keywords that a vacancy it matters for would contain, such as `cro`, `retail` or `python`. |
| Hint | One short sentence on when it applies, such as "Use for CRO or experimentation roles". |
| Where used | Any of CV, letter and interview. All three by default. Something you want off your CV simply leaves out CV, and a wish or a condition (kind preference or constraint) never has it, however it was added. |
| Private | For health, family, religion, politics, finances and similar matters. A private memory is stored and shown to you, and never sent to a model until you clear the flag. |
| Origin | Added by hand, taken from a text, or taken from the notes of a letter or an interview, with the vacancy it came from and when. |

## How memories are made

Everything below works in the dashboard's **Memories** tab (see
[The Memories tab](#the-memories-tab)) and on the command line.

**By hand.** Write the statement yourself and choose where it may be used. In the tab,
fill in *Add a memory* and press **Save memory**; on the command line:

```bash
uv run job-scout memory add "Ik wil maximaal 32 uur per week werken." \
  --kind preference --use letter --use interview --user alex
```

Nothing is sent to a model when you add a memory by hand. If one of your memories
already says much the same, the new one is saved anyway and you are told which.

**From a text.** Give any text about yourself (a paragraph you paste, a file with a list
of projects, an old self-assessment) and the model turns it into proposed memories. You
see every proposal before anything is saved. In the tab, paste the text under *Turn a
text into memories*, or read a TXT, Markdown, PDF, DOCX or ODT file into the box, and
press **Propose memories**; on the command line:

```bash
uv run job-scout memory import projecten.docx --user alex
uv run job-scout memory import --text "Ik heb in 2022 veertig winkels ..." --user alex
```

One run reads at most 20,000 characters and proposes at most 20 memories. When a text
may hold more, the tab and the command say so; run it again after saving and the model,
which then sees the saved ones, proposes the rest. A file may hold up to 80,000
characters, read one part after the other; a TXT file may be UTF-8, UTF-16 or
Windows-1252 (what Notepad saves as ANSI).

Time relative to the day you write it is turned into a month and year: with today in
September 2026, "I can start in two months" becomes "I can start from November 2026", so
a later letter does not repeat an availability that has passed. Something that holds now,
such as a course in progress, says when it was noted.

**Automatically, from your notes.** The notes you type for a letter or an interview in
the dashboard are read once more after the letter or interview set is written. Facts about you that could
matter again become memories; instructions for that one document ("make it shorter",
"mention my salary wish") and facts about only that vacancy or employer do not. Each
memory is generalised so it stands on its own without the vacancy it came from, but
never embellished. In the dashboard this happens in the background once the letter or
interview set is on your screen, so it never makes you wait; the status line then says
that your notes are being read, and any new memories appear in the Memories tab once that
is done. Notes that hold only instructions, or only facts you already have, add none.
Each new memory says which vacancy's notes it came from.

The same notes are read only once: generating again with the same notes costs no second
model call, and neither does generating the other half of an interview with them. Notes
of one or two words are not read at all. Switch automatic capture off per user with the
switch at the bottom of the Memories tab, with `job-scout memory auto-capture off`, or
with the `memory_auto_capture` setting in [CONFIGURATION.md](CONFIGURATION.md).

**Deleting holds, within limits.** Notes you type again, also with a change, do not
bring a deleted memory back in the same or similar words. job-scout keeps the wording of
every deleted memory in your own database for that. Code drops any proposal that repeats
one or only leaves a detail such as the year out. For a memory that is not private, the
model is also told not to propose it again in any wording, whenever your notes touch its
subject. A private one never reaches a model, so it is compared in code only, and more
loosely: a proposal that shares most of its words and numbers is dropped. A fact written
quite differently can still come back; it then comes back marked private, and you can
delete it again. *Deleted memories* at the bottom of the Memories tab, or `job-scout
memory forgotten`, shows the list; **Erase** next to one, or `--remove N`, erases that
one, and **Erase this list**, or `--clear`, erases them all. After that new notes may
bring those facts back. Adding a fact again by hand, or through a text import you review,
always works.

In both automatic and text import, the model sees the memories you already have and
leaves out what they say, in any wording. Code adds a floor that only catches the plainly
identical: a proposal is dropped when it has the same words (case, accents, punctuation
and word order aside), or when it is a shorter wording that adds nothing. A different
number is a different fact ("since 2019" does not repeat "since 2020", "one month" does
not repeat "three months"), and so is another employer or tool, a wish turned around ("I
do not want to travel"), or a comparison or direction turned around ("rather Utrecht than
Amsterdam" does not repeat "rather Amsterdam than Utrecht").

Wishes and conditions (kinds preference and constraint) are never put on a CV. A proposal
whose text, hint or tags plainly say that you have a private matter ("ik ben zwanger",
"my divorce", "ik zit in de schuldsanering", "recovered from a burn-out", "ik heb in 2022
een burn-out gehad", "na een burn-out werk ik liever 32 uur") is marked private even when
the model did not mark it. A subject alone is not enough: "bijstand", "vakbond" or "sick
leave" also describe the work of a klantmanager, an HR adviser or an analyst, and work
marked private would be kept out of every document. For those the model decides, and you
can always set or clear the flag yourself. The model never sees your private memories,
so it cannot know that your notes touch one: a proposal that says what one of them says
in other words is marked private in code.

## How memories are used

Each generator asks for the memories allowed for its purpose: CV tailoring for CV,
the letter writer for letter, interview preparation for interview. Private memories are
never included, for any purpose.

The memories are ranked by how well they fit the vacancy: a tag that occurs in the
vacancy counts three points, each other significant word the memory's text and hint
share with the vacancy one point, and among equals the most recently changed memory comes
first. Words are compared without their common endings, so a field meets its role: the
tag `data engineering` fits "Data Engineer", `project manager` fits "project management".
At most 25 are sent. With fewer eligible memories all of them are sent and the model
decides from their text, hints and tags which fit.

A tag of one or two letters, or a very common word, counts only where the vacancy writes
it in capitals: the tag `it` fits "IT-servicedesk", not "It is a great place".

In the prompt each memory is one more labelled source of facts next to your CV, with a
label such as `memory 3`, its kind, text, hint, tags and the date it was last changed
with how long ago that was ("19 March 2026 (6 months ago)"). A memory you add by hand may
have no hint or tags; its text is enough. Where it came from is not sent: it can name
another employer. The model is told to use a memory only where its text, hint or tags fit
the vacancy, to treat a preference or constraint as a wish and never as experience, never
to stretch a memory beyond what it says, to follow the more recent of two memories that
disagree ("32 uur" noted in May, "40 uur" noted in September), to read relative time in
a memory against the date it was noted, and never to state as current an availability or
a plan that has passed or is months old.

On a CV built in CV Builder a memory may add a bullet after the own bullets of the role it
belongs to, or add to the profile text; education entries are only reordered, and a wish
or a condition goes nowhere on the CV. A memory never adds a role, an employer, a date, a
school or a skill item. Each new bullet is checked in code against the memory behind it,
whether the model named that memory or not: the memory must belong to that role (it may
not describe work for another organisation, such as a freelance job or a client, or be
dated only outside the role's period), and the bullet may hold no number (in digits or in
words), name or year that the memory does not. When the words alone cannot show that a
bullet states its memory, as for a Dutch memory on an English CV, the model is asked once
more, as a judge, and only its explicit yes lets the bullet in. A refused bullet is left
out, that role keeps its own bullets, and `cv tailor` and the tailor endpoint say which
memory was not used where. While memories are in the prompt, reworded descriptions,
bullets and the profile text are checked too: one that takes up a memory about other
work, or brings in a number or name that neither the CV nor a fitting memory holds, is put
back as it was. The checks read words, not meaning: they do not catch a claim made in
ordinary words, such as "led a team". For a plain text CV (`profile tailor-resume`) the
rule is the same, and a study line may also take what a memory adds.

## The Memories tab

The tab sits in the dashboard's *Prepare applications* group, right after CV Builder.
Choose a single user first; everything in it belongs to that user.

- **Add a memory.** The statement, its kind, where it may be used (CV, letters,
  interviews; all three are ticked to start with), tags separated by commas, an optional
  hint on when to use it, and *Private*. Choosing the kind Wish or Condition unticks CV,
  since a wish is not experience.
- **Turn a text into memories.** Paste a text or read a file into the box, then press
  **Propose memories**. This takes a model call, so it can take a few minutes. The
  proposals appear above your list, each ticked and with the same fields as a memory you
  add by hand, so you can correct the wording, the tags, the hint, where it is used and
  the private flag before you keep it. **Save ticked memories** stores the ticked ones
  together; **Discard proposals** drops them all. Facts you already have are not proposed.
  Proposals from a file are marked with the file's name as long as the box holds that
  file's text or a part of it; once you type or paste something else, they are marked as
  pasted text.
- **Your memories.** Newest first, each with its text, hint and tags, its label (such as
  *memory 12*, the name a draft interview answer uses when it cites the memory), its kind,
  where it may be used and where it came from and when: added by hand, taken from a text
  or file, or taken from the notes for a letter or interview, with the vacancy named. A
  private memory carries an amber mark. The search box filters on words in the text,
  tags, hint and kind, ignoring case and accents. **Edit** opens the same fields in place;
  **Delete** asks first. **Refresh** picks up memories that a capture added while the tab
  was open; opening the tab again does the same.
- **From your notes.** The automatic capture switch, and *Deleted memories*: the wording
  of every memory you deleted, with **Erase** next to each and **Erase this list**. The
  delete confirmation says what is kept and what is sent before you decide.

The Cover Letter Writer and Interview Questions tabs say, next to the notes box, that
facts in your notes are kept as memories, and say so again after a generation that
started a capture.

## Privacy

Memories live in your own database (`data/users/<name>/jobs.db`, tables `memories`,
`captured_notes` and `forgotten_memories`), like everything else about you. They reach
the configured model provider in two ways:

- As part of the applicant facts for a letter, interview set or CV, the way your CV does.
- Whenever new memories are proposed. **Propose memories** (and `memory import`) sends
  the text you gave, and automatic capture sends the notes you typed a second time,
  after the letter or interview set was written from them. Both send along up to 40 of
  your memories, the ones closest to that text, so that the model leaves out what you
  already have. Automatic capture also sends the wording of up to 40 deleted memories
  that share a word with your notes, so that it does not propose those facts again; a
  deleted memory is not sent with notes about something else.

Private memories never reach a model: not in the applicant facts, not as existing
memories and not as deleted ones. The wording of a deleted memory stays in
`forgotten_memories`, and goes along with automatic captures of notes on its subject,
until you erase it in the tab or run `job-scout memory forgotten --remove N` or
`--clear`. Deleted rows are overwritten in the database file, not only unlinked, and
erasing the list also compacts the file, so an erased wording does not linger in a
synced or backed up copy of it.

The dashboard's memory routes sit under `/api/memories`, behind the same token as every
other route, and each reads and writes only the user named in the request.

## Commands

| Command | What it does |
|---|---|
| `memory list [--search WORDS]` | Show your memories, newest first |
| `memory add TEXT` | Remember a statement, with `--kind`, `--tags`, `--hint`, `--use` and `--private` |
| `memory edit ID` | Change a memory; `--not-private` clears the private flag |
| `memory delete ID...` / `--all` | Delete memories; notes will not bring them back in the same or similar words |
| `memory import FILE` / `--text TEXT` | Turn a text into proposed memories and save them after asking |
| `memory forgotten [--remove N] [--clear]` | Show the memories you deleted, or erase one or all of them |
| `memory auto-capture [on\|off]` | Show or switch automatic capture from notes |

Every command takes `--user`. See [USAGE.md](USAGE.md#memories) for all options.

## For developers

The code is in `src/job_scout/memories.py` (models, store, selection, prompt material)
and `src/job_scout/memory_extract.py` (extraction and automatic capture). A generator
wires memories in with one call:

```python
from job_scout.memories import MEMORIES_SOURCE_KEY, MEMORY_GUIDE, select_memories_payload

payload = select_memories_payload(user, "letter", f"{job.title}\n{job.description}")
if payload:
    facts.sources[MEMORIES_SOURCE_KEY] = payload
```

`MEMORY_GUIDE` is the entry for the source guide, where memories are a key of the
applicant's facts; `MEMORY_USE_GUIDE` is the same guidance without the description, for a
prompt that quotes memories as a section of its own and describes them itself.
`MEMORY_CV_RULE` is the rule for tailoring a text CV; CV Builder tailoring has its own
`CV_MEMORY_RULE` in `cv/tailor.py`, and `tailor_cv(...)` returns the tailored document
with `memories_not_used`. The payload's `noted_on` reads like "19 March 2026 (6 months
ago)"; pass `today=` to `select_memories_payload` or `memories_payload` in tests.
`memory_labels(payload)` returns the labels a model may cite, for validating
citations the way STAR story citations are validated. After a generation with notes,
`capture_from_notes(user, notes, source="letter_notes", source_detail=..., job_id=...)`
runs the capture; it never raises, so it can run as a background task, and
`capture_pending(user, notes)` tells whether it will do anything. The source detail is
stored with each memory and shown to the applicant, and never put in a prompt, so a
company name from a scraped vacancy is safe there.

For the text-to-memories flow, `extract_memories(text, list_memories(user), client,
source="text_import", source_detail=...)` returns a `MemoryExtraction`: `drafts` to show
for ticking and editing, saved with `add_memories(user, chosen)`, and `truncated`, which
means the page should say there may be more in the text and a second run gets the rest.
Leave `forgotten` empty there, so the applicant can take a deleted fact back on purpose.

### HTTP API

`src/job_scout/web/memories_api.py` holds the dashboard's router, mounted under
`/api/memories`. Every route takes `?user=`; an unknown or unsafe user is a 400, a memory
number below 1 or beyond what SQLite can hold a 422, a model failure a 502 with a vague
message (the real cause goes to the log), unreadable data a 503. Responses are not
cached.

| Route | What it does |
|---|---|
| `GET /api/memories` | Every memory (with `origin` in words and `cited_as`, such as `memory 3`), `auto_capture` and the number of deleted memories |
| `POST /api/memories` | Add a memory by hand (`text`, `kind`, `tags`, `hint`, `use_in`, `sensitive`); returns it with `similar_id` when one already says much the same |
| `PUT /api/memories/{id}` | Replace a memory's text, kind, tags, hint, uses and private flag; origin and dates stay. 404 when it is gone |
| `DELETE /api/memories/{id}` | Delete a memory and record its wording as forgotten. 404 when it is gone |
| `POST /api/memories/extract` | `{text, source_detail}` to proposed drafts and `truncated`, with one model call; stores nothing |
| `POST /api/memories/batch` | Store the drafts the applicant kept, as edited, in one transaction (1 to 20) |
| `POST /api/memories/read-file` | The text of an uploaded TXT, MD, PDF, DOCX or ODT file (up to 8 MB and 80,000 characters), to check before proposing; stores nothing. A file that cannot be read gets one plain message |
| `GET` / `PUT /api/memories/auto-capture` | Read or set `{enabled}` for automatic capture |
| `GET` / `DELETE /api/memories/forgotten` | List the deleted memories (each with its `id`), or erase that list |
| `DELETE /api/memories/forgotten/{id}` | Erase one deleted memory. 404 when it is gone |

`start_capture(tasks, response, user, notes, source=..., job_id=..., client=...)` is what
`POST /api/letters/generate`, `POST /api/interview/questions` and
`POST /api/interview/answers` call once their result is ready. It asks `capture_pending`
first and, only when a capture will run, adds `capture_from_notes` to the request's
background tasks, with the model the generation used and an origin naming the vacancy
("notes for the Data analyst letter to Findwhere"), and sets the response header
`X-Memory-Capture: started`, which the page reads to say that new memories may appear.
Anything that goes wrong in that check is logged and never costs the applicant the
letter or interview set.
