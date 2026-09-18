# Writing style

Everything job-scout writes for you to read or send follows one house style. Text
from a language model gives itself away through small marks: dashes joining every
other clause, bullet lists where a sentence would do, stock words such as
"passionate" or "naadloos", and the same polish in every sentence. An employer who
notices that reads the whole application differently. The house style is there so
the drafts read like something a capable person wrote on a normal working day.

## What the house style asks

**Punctuation.** No em dash, no en dash, and no spaced hyphen joining two parts of a
sentence. A full stop, a comma, a colon or brackets do that job. A hyphen belongs
inside a word (e-commerce, B2B-klanten) or in a range of numbers (2019-2021).

**No formatting.** No bullet points or numbered lists unless the output itself is a
list, no bold, no italics, no headings, no emoji and no exclamation marks.

**Plain words.** A fixed list of stock words is banned in both languages. In English
that includes passionate, excited, eager, leverage, seamless, dynamic, "I am
confident that" and "furthermore". In Dutch it includes passie, gepassioneerd,
"met veel enthousiasme", uitdagend as praise, naadloos, cruciaal and "graag wil ik",
and a sentence may not open with Daarnaast, Bovendien, Kortom or "Al met al". The
full list is in `src/job_scout/writing_style.py`.

**Uneven on purpose.** Short and long sentences mixed. Each point made once. No
groups of three adjectives, no rhetorical questions, no closing sentence that sums up
what was just said, and no compliments to the reader or the company.

**Specific rather than impressive.** A number, a tool, a place or a result beats an
adjective. With nothing specific to say, the draft says less.

**Your register.** Dutch is direct and plain, the way Dutch colleagues write to each
other. English is plain international business English without sales language.

## Where it applies

| Feature | What follows the house style |
| --- | --- |
| Cover Letter Writer | Every paragraph of the letter, and the style guide it learns from your old letters |
| Older cover letter and screening commands | The letter, the screening questions and your draft answers |
| Interview questions, both directions | Each question, why it matters, and each draft answer |
| Interview prep | The behavioural questions |
| Document feedback | The summary, strengths, each point and each example rewrite |
| Career coach | The intake questions, the summary, each direction's description, what to rule out and the follow-up question |
| Vacancy evaluation | The fit, search and compensation explanations on each vacancy card |
| CV tailoring | The reworded profile text, descriptions and bullets, for both the CV Builder document and the plain-text CV |

Text that is not prose is left alone: search keywords, scores, source labels, the
copy the CV importer makes of your own CV (it has to keep your words exactly), and
the facts on a CV such as employers, job titles, schools and dates.

## Three layers

**The prompt.** Every prompt that produces prose carries the house style. The prompts
themselves avoid the dashes they forbid, because a model copies the punctuation of
the instructions it is given.

**The clean-up floor.** After the model answers, a fixed rule repairs what it did
anyway. A dash joining two parts of a sentence becomes a comma, a dash between two
numbers becomes a plain hyphen, bold and italic markers are removed, emoji are
removed and the typographic ellipsis becomes three full stops. Line breaks stay, and
so does a line that starts with `- `. This is a floor, not a rewrite: a rule cannot
turn "passionate" into what you actually mean. It also never touches names, e-mail
addresses, links or the CV facts listed above. In a tailored plain-text CV, a line
that contains a year is left exactly as written, because the dash on a date line is
a range.

**The warning.** A rule cannot fix stock phrases, so the Cover Letter Writer names
the ones it finds in the finished letter among its review warnings. Rewrite those in
your own words before you send. A word that only contains a stock word, such as
"passief" or "compassie", does not count.

## Your own style guide

The guide learned from your old letters works together with the house style. Where
your old letters used dashes, lists or stock words, the house style wins and the
guide is asked to list them under Improve or Never. The learned guide is cleaned the
same way as a letter; a guide you edit by hand is used as you wrote it.

The letter editor still turns a line that starts with `- ` into a bullet in the PDF.
The writer will not suggest one.

## Earlier text

Nothing already saved is rewritten. A letter, answer or vacancy explanation written
before this change keeps its old wording until it is generated again.

## Changing the house style

The house style is one block of text, `HOUSE_STYLE` in
`src/job_scout/writing_style.py`, with the clean-up rules (`humanise`) and the list
of stock phrases (`ai_tells`) next to it. A change there reaches every generator at
once. `tests/test_writing_style.py` shows what the clean-up changes and what it
leaves alone.

See also the [Cover Letter Writer guide](LETTER_WRITER.md) and the
[interview questions guide](INTERVIEW_QUESTIONS.md).
