# Cover letter writer

Write a Dutch or English letter from a vacancy and everything you have told
job-scout about yourself: your own CV, CV Builder, a LinkedIn import, your profile and
your STAR stories. Your previous letters teach the writer your voice. The result is an
editable draft, ready for your review.

## In the dashboard

1. Select one user and make sure job-scout has their CV: upload it under
   **Profile & Filters**, keep it in **CV Builder**, or both. Either is enough; see
   [Where your facts come from](#where-your-facts-come-from).
2. Open **Cover Letter Writer**, or click **Write cover letter** on a vacancy card.
3. Choose the vacancy, language and CV Builder profile. **Automatic** estimates
   Dutch or English from the vacancy text; select a language explicitly for
   mixed-language listings. **Automatic — all your sources** uses every source and,
   from CV Builder, prefers `nederlands` for Dutch and `default` for English, then
   another real profile in that language, flagging a profile in the other language.
   Choosing a profile yourself only changes which CV Builder profile is used.
4. Optionally name the recipient and add a specific reason for applying, a prior
   conversation, or something you want to discuss. Notes are treated as facts you
   supply, so include only things that are true.
5. Click **Generate full letter**. Edit the date, subject, salutation, body, closing and
   signature. Separate paragraphs with blank lines. The writer uses plain paragraphs;
   a line you start with `- ` still prints as a bullet.
   Expand **Read the complete letter** to see all fields together.
6. **Save draft** stores one version per vacancy and language. Saving replaces
   that language's previous version. To retrieve it, select Dutch or English and
   click **Load saved**. Generation alone does not overwrite a saved draft.
7. Download PDF or text. Both include the editor's current changes, including
   unsaved ones. PDF uses the CV Builder profile's colours and fonts when one was
   used, and the default theme otherwise; contact details come from the same
   sources as the letter. Long letters flow onto further pages instead of being
   clipped.

The writer does not send applications or change a vacancy's workflow status.
CV edits made after drafting apply to future generations, not the existing body.

## Where your facts come from

The letter draws on everything you have told job-scout about yourself. None of it is
required on its own, and each source is labelled in the prompt so the writer can weigh
overlapping or conflicting claims:

| Source | Where you set it |
| --- | --- |
| Your own CV file | **Profile & Filters** → **Upload CV** (a PDF you made yourself is fine) |
| Extra experience notes | **Profile & Filters**, next to the CV |
| CV Builder profile | **CV Builder**, when a profile holds your real CV |
| Parsed profile, including a LinkedIn import | Built from your CV; `profile import-linkedin` adds to it |
| Profile description and career tracks | **Profile & Filters** — used for motivation, never as evidence of experience |
| STAR stories | `profile star-story` |

When two sources disagree about a date or about which job is current, the most
recent information wins and the conflicting claims are not combined. The CV list in
the tab only chooses which CV Builder profile to prefer; **Automatic — all your
sources** uses every source. A CV Builder profile that is empty, or that still holds
the example CV an older version created, is listed but cannot be chosen, and is
never used: the letter would otherwise describe someone else's career. The letter
says which sources it was written from, and names anything it skipped.

The signature, place and PDF contact details come from a CV Builder profile when one
holds your real CV. Otherwise the name comes from your CV file's name or its text,
the place from your home address, and the email, mobile number and LinkedIn address
from your own CV. A detail no source states is left empty with a warning to fill it
in, never guessed.

## Teach it your writing style

Expand **Examples & writing style** and upload previous letters. Supported formats
are text-based PDF (up to 20 pages), DOCX, ODT, UTF-8 TXT and Markdown, up to 8 MB
per file. Image-only scans need text recognition before import. Duplicate filenames
are rejected; remove the old example first to replace it.

**Learn style from examples** proposes a guide from up to 30 of the newest examples,
with long examples shortened to 700 words each. Review and edit it, then click
**Save style guide**. The guide can also be written by hand. Removing an example
does not automatically rewrite a saved guide.

New drafts use the saved guide and up to three recent examples in the output
language. General defaults favour direct openings, concrete evidence, concise
paragraphs, and a professional level of formality. Dutch output uses `u` and `uw`;
English keeps the same directness. The guide can refine the tone without requiring
factual details from old applications.

Every letter also follows job-scout's house style: no dashes joining clauses, no
lists or bold text, and none of the stock words that make a letter read as
generated. Where your guide or your old letters do those things, the house style
wins. [Writing style](WRITING_STYLE.md) explains the rules and why they exist.

## Facts and review

The prompt separates current CV facts and your notes from historical examples and
employer requirements. It explicitly tells the model not to copy old employers,
prior conversations or outdated employment chronology into the new application.
Contact/detail sections are excluded from the factual prompt; PDF contact details
are read directly from the selected CV.

Warnings flag unusual length, missing references, a CV language fallback, some
recognisable names copied from examples, and stock phrases that read as generated
(such as "passionate" or "met veel enthousiasme") so you can rewrite them in your
own words. These are review aids, not a guarantee
that every claim is correct. Check employers, qualifications, dates, achievements
and reasons for applying before sending. Invalid or incomplete model JSON is
rejected without replacing a saved draft.

## Storage and privacy

Each user's files live under the runtime data directory:

```text
users/<user>/letters/
  examples/                 original uploaded letters
  style.md                  editable personal writing guide
  generated/<job>-nl.json    saved Dutch draft
  generated/<job>-en.json    saved English draft
```

These files belong in private runtime data, not the public repository. Back them up
alongside the database and CV profiles. Saving also updates the database's legacy
plain-text cover-letter field with the most recently saved language.

Generation sends the selected CV's factual sections, vacancy, notes, guide and
selected examples to your configured LLM. Learning style sends example text. Both
use the existing `cover_letter` routing purpose; see [LLM providers](LLM_PROVIDERS.md).
A local provider keeps these requests on your own infrastructure; a hosted provider
receives them. No new service or API key is required.

The `/api/letters/*` routes inherit the dashboard's optional shared bearer token.
User selection separates stored files; it is not an individual login or an access
boundary between people who already have dashboard access.

## CLI

```bash
job-scout letter import-examples past-letter.pdf another-letter.docx --user alex
job-scout letter learn-style --user alex
job-scout letter generate 42 --user alex --language nl --cv nederlands
job-scout letter generate 42 --user alex --language en --save --pdf letter.pdf
job-scout letter render 42 saved-letter.pdf --user alex --language en
```

`generate` also accepts `--recipient` and `--notes`. It prints the letter and review
warnings; saving requires `--save`. `--pdf` exports the generated text. The CLI's
`learn-style` command saves its result immediately; the dashboard offers a review
step before saving. Use `--help` on a command for its options.

`--cv` names the CV Builder profile to prefer; your own CV and profile are used
either way. The command prints which sources the letter was written from.

The older `profile generate-cover-letter` command remains available. The `letter`
commands and the Cover Letter Writer tab use every source listed under
[Where your facts come from](#where-your-facts-come-from).
