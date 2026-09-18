# Writing style

Everything job-scout writes for you to read or send follows one house style. Text
from a language model gives itself away through small marks: dashes joining every
other clause, bullet lists where a sentence would do, stock words such as
"passionate" or "naadloos", and the same polish in every sentence. An employer who
notices that reads the whole application differently. The house style is there so
the drafts read like something a capable person wrote on a normal working day.

## What the house style asks

**Punctuation.** No em dash, no en dash, no double hyphen (`--`) and no spaced hyphen
joining two parts of a sentence. A full stop, a comma, a colon or brackets do that
job. A hyphen belongs inside a word (e-commerce, B2B-klanten) or in a range of
numbers (2019-2021).

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
| Company research and review | The research findings, the review's summary, pros, cons and other text, wherever they are shown: the vacancy cards, notifications, the command line and the interview prompts |

Text that is not prose is left alone: search keywords, scores, source labels, the
copy the CV importer makes of your own CV (it has to keep your words exactly), and
the facts on a CV such as employers, job titles, schools and dates.

## Three layers

**The prompt.** Every prompt that produces prose carries the house style. The prompts
themselves avoid the dashes they forbid, because a model copies the punctuation of
the instructions it is given.

**The clean-up floor.** After the model answers, a fixed rule repairs what it did
anyway. A dash joining two parts of a sentence becomes a comma, bold and italic
markers are removed, emoji and symbols such as rating stars are removed, and the
typographic ellipsis becomes three full stops. Two hyphens between words (`--`) are a
dash too; two hyphens before a word, as in a command-line option, are not. Line breaks
stay, and so does a line that starts with `- `, because a letter may hold a list on
purpose. Interview questions and spoken draft answers never do, so there a list the
model writes anyway becomes sentences: the markers (`-`, `*`, a bullet sign, `1.`) go
and each item joins the line before it, while a blank line between paragraphs stays.
This is a floor, not a rewrite: a rule cannot turn "passionate" into what you actually
mean.

A dash in a range stays a range, written with a plain hyphen: 2019-2021,
€ 3.500-€ 4.800, 42k-55k, € 3m-€ 5m, jan 2019-dec 2021 and 2019-heden, whether the
model wrote a dash or two hyphens. That matters for salary explanations, for revenue
in company research and for the periods on a tailored CV, where a comma would split
one range into two separate amounts or dates.

The clean-up is not applied to fields that hold names, links or CV facts, and inside
prose it leaves e-mail addresses and web links exactly as written. On a tailored CV
only the lines the model reworded are cleaned: a line whose words are in your own CV
(your contact details, an employer and its town, "Nederlands" and its level) keeps
your punctuation and only loses bold or italic markers. A name inside a sentence
gets no such protection, so a double surname written with spaces around its hyphen
would come out with a comma instead. Dutch double surnames are normally written
without those spaces (Jansen-de Vries), so this rarely comes up.

Document feedback, the style guide learned from your letters and the company review
often quote someone's own words. Whatever they put in quotation marks is kept exactly
as it was, dashes included, so a quoted sentence is still the sentence it quotes.

**The warning.** A rule cannot fix stock phrases, so the Cover Letter Writer names
the ones it finds in the finished letter among its review warnings. Rewrite those in
your own words before you send. A word that only contains a stock word, such as
"passief" or "compassie", does not count. Neither does a stock word inside a name
you cannot change: the employer, the job title, the person you address, or a name
the vacancy itself uses, such as a product or client whose name contains "Elevate"
or "Pivotal".

## Your own style guide

The guide learned from your old letters works together with the house style. Where
your old letters used dashes, lists or stock words, the house style wins and the
guide is asked to list them under Improve or Never, naming the mark in words ("a
dash") rather than typing it. The learned guide is cleaned the same way as a letter,
except for the phrases it quotes from your letters; a guide you edit by hand is used
as you wrote it.

The letter editor still turns a line that starts with `- ` into a bullet in the PDF.
The writer will not suggest one.

## Earlier text

Nothing already saved is rewritten. A letter, answer or vacancy explanation written
before this change keeps its old wording until it is generated again. Two exceptions:
when a new vacancy reuses the evaluation of an earlier one with the same title and
company, the reused explanation is cleaned as it is copied, and company research and
reviews, which are kept and reused for months, are cleaned every time they are shown
or used.

The text job-scout wraps around generated prose follows the same rules: the
notifications and the command line join a score and its explanation with a full stop
rather than a dash.

## Changing the house style

The house style is one block of text, `HOUSE_STYLE` in
`src/job_scout/writing_style.py`, with the clean-up rules (`humanise`) and the list
of stock phrases (`ai_tells`) next to it. A change there reaches every generator at
once. The generators call `clean_prose` in `src/job_scout/prose.py`, which adds the
range, link and quotation handling described above before handing the text to
`humanise`. `tests/test_writing_style.py` and `tests/test_prose.py` show what the
clean-up changes and what it leaves alone.

See also the [Cover Letter Writer guide](LETTER_WRITER.md) and the
[interview questions guide](INTERVIEW_QUESTIONS.md).
