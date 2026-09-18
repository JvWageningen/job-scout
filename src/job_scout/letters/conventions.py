"""Letter conventions per language.

These are general norms for Dutch and English application letters, not anyone's
personal style -- that comes from the user's own style guide, which takes
precedence wherever the two disagree. The Dutch conventions encode the directness
Dutch recruiters expect: motive and fit stated plainly, gaps named honestly rather
than hidden, and no stock enthusiasm.
"""

from __future__ import annotations

from datetime import date

from job_scout.letters.models import LetterLanguage

_DUTCH_MONTHS = (
    "januari", "februari", "maart", "april", "mei", "juni",
    "juli", "augustus", "september", "oktober", "november", "december",
)  # fmt: skip

# Bounds outside which the writer warns. They are deliberately wide: the user's
# style guide sets the real target, and a letter outside these is almost certainly
# either padded or missing its argument.
LENGTH_BOUNDS: dict[LetterLanguage, tuple[int, int]] = {
    LetterLanguage.NL: (120, 420),
    LetterLanguage.EN: (110, 400),
}

CONVENTIONS: dict[LetterLanguage, str] = {
    LetterLanguage.NL: """\
Write a Dutch motivatiebrief.

Form
- The letter opens with a place and date line and a "Betreft:" subject line; these are
  supplied separately, so do not repeat them in the body.
- Address the organisation formally with "u" and "uw". Write about yourself in plain
  first person.
- Salutation: "Beste [naam]," when a person or team is named. Without a name, use
  "Beste [organisatie]-team," or, for a conservative employer, "Geachte heer, mevrouw,".
- Close with "Met vriendelijke groet,".
- One page. Short paragraphs of whole sentences, without bulleted lists.

Directness (Dutch recruiters read this as competence, not rudeness)
- Say in the first paragraph which role this is and why you are applying. No warm-up.
- State the supplied motive plainly. Mention a career change only if the CV or notes
  support it; do not invent a reason for leaving.
- Back every claim with something concrete from the CV. A claim with no example behind
  it should be cut, not dressed up.
- If the vacancy asks for something the CV does not show, you may name that gap once,
  briefly and without apology. Include a plan only if the notes supply one.
- It is acceptable to say what you want to discuss in an interview or what matters to
  you in a role.
- No superlatives, no flattery of the employer, no stock enthusiasm ("ik ben enorm
  gepassioneerd", "uitdagende functie", "dynamische organisatie").
""",
    LetterLanguage.EN: """\
Write an English motivational letter.

Form
- A date line and a subject line are supplied separately; do not repeat them in the
  body.
- Salutation: "Dear [name]," when a person or team is named, otherwise
  "Dear hiring team,".
- Close with "Best regards,".
- One page. Short paragraphs of whole sentences, without bulleted lists.

Register
- Most of these letters go to Dutch employers who happen to recruit in English. For
  them, keep the directness of a Dutch letter: role and motive in the first
  paragraph, claims backed by concrete evidence, a gap named plainly if it matters,
  no flattery.
- For a genuinely international employer, allow slightly more context about why this
  organisation, but still avoid effusiveness. Never write in an American sales register.
- No stock phrases: "I am thrilled", "passionate about", "honed my skills", "a perfect
  fit", "contribute to your success", "dynamic environment", "I am confident that".
""",
}

SHARED_RULES = """\
Hard rules, whatever the language
- Use the CV and notes for applicant facts, the vacancy for employer needs. Never invent
  an employer, job title, date, qualification, skill, number, or achievement.
- The example letters are a reference for voice and structure ONLY. Never reuse
  anything specific from them: no employer, person, project, product, place, date,
  or achievement. Those belong to other applications and would be wrong in this one.
- Do not mention salary unless the user's notes ask you to.
- Write the letter the user would write on their best day: keep their voice and
  formality, and apply their style guide's improvements.
"""


def format_place_date(place: str, when: date, language: LetterLanguage) -> str:
    """Build the place-and-date line that heads a letter.

    Args:
        place: Town the letter is written from; may be empty.
        when: Date of the letter.
        language: Controls the month names.

    Returns:
        e.g. ``"Utrecht, 17 september 2026"``, or ``"17 September 2026"`` without
        a place.
    """
    if language is LetterLanguage.NL:
        stamp = f"{when.day} {_DUTCH_MONTHS[when.month - 1]} {when.year}"
    else:
        month = [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ][when.month - 1]
        stamp = f"{when.day} {month} {when.year}"
    return f"{place.strip()}, {stamp}" if place.strip() else stamp


def subject_line(job_title: str, language: LetterLanguage) -> str:
    """Build the subject line for a letter.

    Args:
        job_title: Title of the vacancy.
        language: Language of the letter.

    Returns:
        ``"Betreft: sollicitatie <title>"`` or ``"Application: <title>"``.
    """
    title = job_title.strip()
    if language is LetterLanguage.NL:
        return f"Betreft: sollicitatie {title}"
    return f"Application: {title}"


def default_salutation(recipient: str, language: LetterLanguage) -> str:
    """Build a salutation, used when the model does not return a usable one.

    Args:
        recipient: Who the letter is addressed to; may be empty.
        language: Language of the letter.

    Returns:
        A salutation ending in a comma.
    """
    name = recipient.strip()
    if language is LetterLanguage.NL:
        return f"Beste {name}," if name else "Geachte heer, mevrouw,"
    return f"Dear {name}," if name else "Dear hiring team,"


def default_closing(language: LetterLanguage) -> str:
    """Return the standard closing for a language.

    Args:
        language: Language of the letter.

    Returns:
        ``"Met vriendelijke groet,"`` or ``"Best regards,"``.
    """
    if language is LetterLanguage.NL:
        return "Met vriendelijke groet,"
    return "Best regards,"
