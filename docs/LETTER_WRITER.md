# Motivational letter writer

Write a Dutch or English letter from a saved CV Builder profile and a vacancy.
Your previous letters teach the writer your voice; the current CV supplies your
experience. The result is an editable draft, ready for your review.

## In the dashboard

1. Select one user and save their up-to-date CV under **CV Builder**.
2. Open **Letter Writer**, or click **Write letter** on a vacancy card.
3. Choose the vacancy, language and CV. **Automatic** estimates Dutch or English
   from the vacancy text; select a language explicitly for mixed-language listings.
   Automatic CV selection prefers `nederlands` for Dutch and `default` for English,
   then another saved profile in that language. If none matches, it uses an
   available CV and flags the fallback. You can always choose a profile yourself.
4. Optionally name the recipient and add a specific reason for applying, a prior
   conversation, or something you want to discuss. Notes are treated as facts you
   supply, so include only things that are true.
5. Click **Draft letter**. Edit the date, subject, salutation, body, closing and
   signature. Separate paragraphs with blank lines; use `- ` for bullet lines.
6. **Save draft** stores one version per vacancy and language. Saving replaces
   that language's previous version. To retrieve it, select Dutch or English and
   click **Load saved**. Generation alone does not overwrite a saved draft.
7. Download PDF or text. Both include the editor's current changes, including
   unsaved ones. PDF uses the selected CV's current colours, fonts and contact
   details. Long letters flow onto further pages instead of being clipped.

The writer does not send applications or change a vacancy's workflow status.
CV edits made after drafting apply to future generations, not the existing body.

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

## Facts and review

The prompt separates current CV facts and your notes from historical examples and
employer requirements. It explicitly tells the model not to copy old employers,
prior conversations or outdated employment chronology into the new application.
Contact/detail sections are excluded from the factual prompt; PDF contact details
are read directly from the selected CV.

Warnings flag unusual length, missing references, a CV language fallback and some
recognisable names copied from examples. These are review aids, not a guarantee
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

The older `profile generate-cover-letter` command remains available for workflows
based on parsed CV PDFs. The new `letter` commands and Letter Writer tab use the
structured, saved CV Builder profile.
