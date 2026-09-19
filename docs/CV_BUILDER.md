# CV builder

job-scout scores vacancies against the CV you already have. The CV builder is the other
half of that: a place to *write* the CV, section by section, and render it to a designed
two-column PDF with a coloured sidebar, a circular portrait and embedded Lato — the kind of
document you would otherwise build in Word and re-align by hand every time a line wraps.

It is a second application living inside job-scout, at `src/job_scout/cv/`. It has its own
document model, its own editor front end and its own renderer, and it reuses job-scout's
LLM routing, per-user data layout and dashboard token. Nothing in it touches the pipeline.

Two PDFs come out of job-scout, and they are not interchangeable. Read
[Designed CV or ATS resume](#designed-cv-or-ats-resume) before you send either one.

---

## The document

Everything the builder edits is one Pydantic model, `CVDocument`
(`src/job_scout/cv/models.py`), stored as a single `cv.json` per profile.

| Field | What it holds |
| --- | --- |
| `full_name`, `headline` | The identity block at the top of the main column |
| `language` | A short tag such as `EN` or `NL`, used in the download filename |
| `photo` | Filename of the portrait inside the profile's own `uploads/` directory |
| `theme` | Colours, font, page size and geometry — see [What you can change](#what-you-can-change) |
| `sidebar`, `main` | Two ordered lists of sections, one per column |

A section is a tagged variant, so the editor can add, remove and reorder without the
renderer having to guess what a payload means. There are **seven kinds**:

| Kind | Renders as | Payload |
| --- | --- | --- |
| `text` | Free prose — a profile or personal statement | `body` |
| `experience` | An ordered list of roles | `entries` of title, organisation, period, description, bullets |
| `education` | An ordered list of qualifications | `entries` of degree, school, period, courses, note, plus three configurable row labels |
| `skills` | A one-, two- or three-column grid, optionally with 0–5 rating bars | `items` of name and level |
| `details` | A two-column table of label/value pairs | `items` of label and value, plus a label suffix |
| `contact` | Icon-prefixed contact lines | `items` of icon, value and URL |
| `list` | A plain bulleted or unbulleted list | `items` of strings |

Every section carries a title, an optional icon and an `enabled` flag, so a section can be
switched off without deleting what is in it. The sixteen icon names are drawn as vectors in
`src/job_scout/cv/render/icons.py` — there is no icon font to install — and the editor gets
the live list from `GET /api/cv/meta`.

The `education` kind carries certifications and courses too: its `school_label`,
`period_label` and `courses_label` are editable, so the same kind renders "Issuer:" and
"Issued:" without needing a kind of its own. An empty label hides that row's prefix.

## The editor

### In the dashboard

Open the **CV Builder** tab. It is the fifth tab, between Document Review and Keywords, and
it embeds the editor in an iframe pointed at `/cv/?user=<name>`. Profiles belong to the user
selected in the header picker; selecting `all` is treated as no selection, because there is
no single CV behind it. The iframe is only pointed at its page the first time you open the
tab, so a dashboard nobody opens that tab on never renders a preview.

The editor reuses the dashboard's bearer token from `sessionStorage`, because its API is
mounted under `/api/cv` — inside the prefix `TokenAuthMiddleware` guards. The page itself
and its assets sit outside `/api/`, exactly like `index.html`, and are inert until their
API calls succeed.

### Importing the CV you already have

**Import CV**, next to **New**, turns a CV you already have into a profile you can edit
here. Take the CV you uploaded under **Profile & Filters**, or upload a PDF, DOCX, ODT or text file,
choose the profile's language and give it a name. The model transcribes the CV: it
repairs words the PDF extraction split and translates faithfully when you choose the
other language, but adds nothing. When your uploaded CV is used, roles and education
from your parsed profile — which is where a LinkedIn import lands — are added where the
CV lacks them. Every employer, school and year in the result is then looked up in the
source, and anything that cannot be found there is listed for you to check.

An import never overwrites a profile that already holds a CV; it can fill an empty one.
It needs the dashboard's LLM settings, so the button only appears inside the dashboard.

CV Builder stays optional. Letters and interview material read your own CV file whether
or not you ever import it; importing is for when you want to edit and lay out that CV
here.

### Standalone

```bash
uv run job-scout cv serve --user alex
```

Serves the same editor on `http://127.0.0.1:38271` with no dashboard around it — useful when
you want the two-pane layout on a full screen. The port is deliberately not 8000, and sits
below the Windows dynamic range so it cannot collide with an ephemeral port. If it is taken,
the command says so in one line instead of a uvicorn traceback.

### What you can change

The left pane is four collapsible panels; the right pane is the rendered PDF, refreshed as
you type unless you switch **Auto refresh** off.

**Identity** — full name, headline, language tag, and the portrait. Upload is any image
Pillow can decode, up to 12 MB; it is EXIF-rotated, centre-cropped square, downscaled to a
1400px edge and stored as PNG, so what the phone produced is not what lands on disk. The
circular mask is a vector clip applied at render time, so the edge stays crisp at any zoom.

**Design** — the theme:

| Control | Range | Default |
| --- | --- | --- |
| Sidebar, accent, page, sidebar text, body text, heading colours | `#rgb` or `#rrggbb` | `#0B3D2C` sidebar, `#1B7A4B` accent, `#FBFBF8` page |
| Font | `Lato` (bundled) or `Helvetica` (ReportLab built-in) | `Lato` |
| Page size | `A4` or `LETTER` | `A4` |
| Base font size | 6–14 pt | 9 pt |
| Line spacing | 1.0–2.0 | 1.32 |
| Sidebar width | 0.20–0.55 of the page | 0.362 |
| Portrait size | 0.30–1.0 of the sidebar width | 0.72 |
| Uppercase headings, show portrait | on/off | both on |

The green defaults are a deepened version of the palette they came from: the original
`#38CB78` gave white heading text 2.1:1 contrast, below the WCAG AA minimum of 4.5:1 and
visibly weak in greyscale print. `#1B7A4B` keeps the identity at 5.3:1. Colours are
validated server-side — anything that is not a hex string is rejected.

**Sidebar sections** and **Main sections** — add, delete, reorder and re-kind sections in
either column, and edit their entries and items.

**Save** writes `cv.json`. **Download PDF** renders what is on disk and names the file
`YYYYMMDD <Full Name> CV <LANG>.pdf`, so a folder of applications sorts chronologically and
the English and Dutch versions never overwrite each other. The inline preview renders what
the editor currently holds, unsaved edits included; the download renders only what was
saved.

The two columns paginate independently and are drawn onto the same canvas, so a long sidebar
does not push the main column around. The page count is whichever column needs more.

## Where the data lives

Each profile is one directory, so it can be copied or backed up by moving it:

```text
data/users/<name>/cv/
└── profiles/
    ├── default/
    │   ├── cv.json
    │   └── uploads/
    │       └── portrait.png
    └── nederlands/
        ├── cv.json
        └── uploads/
```

`job_scout.config.user_cv_dir(name)` returns `data/users/<name>/cv`, and `ProfileStore`
derives its own `profiles/` subdirectory from that root. Giving each user their own root is
what keeps one person's CV out of another's list on a shared install. The whole tree moves
with `JOB_SCOUT_DATA_DIR` like everything else — see
[CONFIGURATION.md](CONFIGURATION.md#relocating-the-data-directory).

Profile names become slugs: accents folded, everything outside `[a-z0-9]` turned into a
hyphen, trimmed to 60 characters. That normalisation is the single gate stopping a slug from
escaping the data directory, and it is why a profile called "Nederlands (2026)" lands in
`nederlands-2026/`.

Opening the editor with no profiles stored creates two empty starter profiles —
`default` (EN) and `nederlands` (NL), with their section titles in that language. Empty
fields show lorem ipsum in the preview, so the layout is visible before anything is filled
in; that placeholder text exists only in the preview and is never saved or downloaded.
Older versions filled the starters with a fictional example CV instead; a profile that
still holds that example untouched is blanked the next time the editor opens, and one you
started editing is left alone. This happens in `cv serve` and in `GET /profiles`; `cv list`
reports an empty store rather than filling it, and `cv render` and `cv tailor` fail on a
profile that does not exist. Saves are
atomic: the JSON goes to a sibling temporary file and is moved into place, so an interrupted
save cannot truncate the previous version.

## CLI

Four commands, all documented with their options in
[USAGE.md](USAGE.md#cv-builder).

```bash
uv run job-scout cv list --user alex
uv run job-scout cv serve --user alex --port 38271
uv run job-scout cv render default --user alex -o ~/cv.pdf
uv run job-scout cv tailor 42 --user alex --slug default
```

`--user` resolves the same way it does everywhere else: on a single-user install you can
leave it off, and on a multi-user one the command refuses to guess.

## HTTP API

Mounted under `/api/cv` when the dashboard is running, and under `/api` when the editor runs
standalone. Every route takes `?user=<name>` in the dashboard, where the store is resolved
per request; standalone there is one store for the process and no user is sent.

| Route | Does |
| --- | --- |
| `GET /meta` | Version, icon names, the seven section kinds and the theme defaults |
| `GET /profiles` | List profiles, creating empty starters when there are none |
| `POST /profiles` | Create an empty one |
| `GET`/`PUT`/`DELETE /profiles/{slug}` | Read, overwrite or remove a document |
| `POST`/`GET`/`DELETE /profiles/{slug}/photo` | Upload, serve or forget the portrait |
| `POST /profiles/{slug}/preview` | Render the posted document inline, without saving; empty fields show lorem ipsum |
| `GET /profiles/{slug}/pdf` | Render the saved document as a download |
| `POST /profiles/{slug}/tailor` | Tailor to a vacancy — dashboard only, see below |
| `GET /import/options` | Whether an uploaded CV and a parsed profile exist — dashboard only |
| `POST /import` | Transcribe a CV into a new profile — dashboard only, see above |

Tailoring and import are declared by job-scout rather than the vendored router, because
they are the CV operations that need job-scout itself: the vacancy comes out of the user's
jobs database and the model comes from their LLM settings, neither of which the standalone
editor knows anything about.

## Tailoring a CV to a vacancy

```bash
uv run job-scout cv tailor 42 --user alex --slug default
```

This takes the designed CV you already wrote and retargets it at one vacancy from your
database: the most relevant sections, roles and skills move to the front, and prose is
reworded so genuinely relevant experience stands out. The result is saved as a **new
profile**, named after the company — `default` tailored to Meridiaan Data becomes
`default-meridiaan-data` — and rendered to a PDF. The source profile is read and never
written, so you can tailor it again tomorrow for the next vacancy. The portrait is copied
into the new profile rather than shared, so it renders with the same face.

There is no tailor button in the editor UI. Use the CLI command, or
`POST /api/cv/profiles/{slug}/tailor?user=<name>&job_id=<id>`; either way the tailored
profile then appears in the editor's picker like any other.

### The integrity constraint

A CV is a factual document about a real person, so the model gets a deliberately narrow
mandate: **reorder what is there, and reword prose.** That is all.

| The model may | The model may not |
| --- | --- |
| Reorder sections within a column | Move a section between columns |
| Reorder entries within a section | Add, remove or duplicate anything |
| Reword a `text` section's body | Touch `details` or `contact` sections — they hold personal data and are sent to the model as a heading only, marked frozen |
| Reword an experience entry's description | Change a job title, employer, school, degree or date range |
| Reword existing bullets, and add one bullet per [memory](MEMORIES.md) it cites that you allowed on your CV | Return more bullets than the entry had plus the memories it cites |
| — | Change the identity block, the portrait or the theme |

Only `text` and `experience` sections have prose the model may rewrite at all. `education`,
`skills` and `list` sections are reorderable but not rewritable — a degree, a skill name or a
list item comes back exactly as it went in, or the document is refused.

This is enforced three times over, not just asked for. The prompt states the rules; the
patch layer rejects an edit that rewrites a frozen field or grows a bullet list beyond the
memories it cites; and
`_verify_integrity` re-checks the finished document against the original, refusing it
outright if the set of organisations, titles, periods or skill names it claims is not a
subset of what went in. **A CV that gained an employer is rejected, not handed back.** So is
one where a column's ordering is not a permutation of the original, or where the model
patched a section id this CV does not have.

The failure mode is therefore a one-line error and no file written, never a quietly
embellished CV:

```text
Tailoring failed: tailored CV invented organisations that are not in the original: acme bv
```

An empty plan is refused too — if the model asks for no change at all, the command says so
rather than saving an untailored copy under a new name.

Tailoring runs through the same LLM client, per-purpose routing and retries as the rest of
job-scout, under the `resume_tailoring` purpose. See
[LLM_PROVIDERS.md](LLM_PROVIDERS.md). It reuses `extract_resume_keywords` from
`resume_tailor.py` to decide which terms to foreground, so the designed CV and the plain
resume aim at the same keywords.

## Designed CV or ATS resume

job-scout produces two PDFs by two different paths, and this is the honest trade-off between
them.

| | **CV builder** (`job-scout cv render` / `cv tailor`) | **Resume tailor** (`job-scout profile tailor-resume`) |
| --- | --- | --- |
| Source | The `CVDocument` you wrote in the editor | Your CV PDF, parsed to text and rewritten by the LLM |
| Layout | Two columns, coloured sidebar, portrait, icons, rating bars | Single column, plain paragraphs, default styles |
| Renderer | `job_scout.cv.render` (ReportLab canvas) | `generate_resume_pdf()` in `resume_tailor.py` (ReportLab `SimpleDocTemplate`) |
| Reads well by | A human | A parser |

**The caveat.** A two-column CV with a sidebar parses poorly in many applicant tracking
systems. A parser that reads a page in one pass can interleave the two columns, so a skills
list ends up spliced through your job history; icons carry no text at all, and a rating bar
is a rectangle, not a proficiency. Some modern ATS handle multi-column layouts; plenty of
older ones silently do not, and you will not be told which one you hit.

That is exactly why job-scout keeps the plain `generate_resume_pdf` output as well. It is
deliberately boring — one column, no graphics, standard styles — and it is documented as
ATS-safe because that is the only thing it optimises for.

**When to use which:**

- **Upload to a job board, a big-employer careers portal, or any "upload your CV" box that
  then pre-fills a form** — use the plain resume from
  [`profile tailor-resume`](USAGE.md#profile-tailor-resume). If the form was pre-filled
  correctly, the parse worked.
- **Email it to a hiring manager, attach it to an application you send yourself, hand it
  over at an interview, or link it from your own site** — use the designed CV. A person is
  reading it, and it looks like someone cared.
- **Unsure, and the application allows two files** — send both, the designed CV as the CV
  and the plain one as the resume. Neither costs anything to produce once the profile
  exists.

Nothing stops you tailoring both against the same vacancy: `cv tailor 42` and
`profile tailor-resume 42` read the same job row and foreground the same keywords.

## Related documentation

- [USAGE.md](USAGE.md#cv-builder) — the four `cv` commands with their options.
- [CONFIGURATION.md](CONFIGURATION.md#cv-builder-data) — where the data lives and what moves it.
- [WEB_DASHBOARD.md](WEB_DASHBOARD.md) — the dashboard around the tab, and its token.
- [LLM_PROVIDERS.md](LLM_PROVIDERS.md) — which model does the tailoring.
- [../ARCHITECTURE.md](../ARCHITECTURE.md#module-map) — the `cv/` subpackage and how it
  relates to `resume_tailor.py`, `cv_parser.py` and `exporter.py`.
