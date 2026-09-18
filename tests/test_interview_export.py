"""Interview sets as Word and text files the applicant can edit.

Every person and employer below is invented. The repository is public, so no
fixture may carry a real name, address or company.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from docx import Document

from job_scout.company_lookups import LookupAttempt, LookupOutcome
from job_scout.interview_answers import (
    AnswerFooting,
    InterviewAnswerSet,
    LikelyQuestion,
    QuestionKind,
)
from job_scout.interview_export import (
    ExportFormat,
    export_filename,
    interview_blocks,
    render_docx,
    render_text,
)
from job_scout.interview_questions import (
    NO_PUBLIC_INFO,
    NO_REVIEW,
    RESEARCH_FAILED,
    THIN_REVIEW,
    InterviewQuestion,
    InterviewQuestionSet,
    QuestionTheme,
    _remembered_research_gap,
    _review_gap,
)
from job_scout.interview_store import InterviewSet
from job_scout.letters.models import LetterLanguage
from job_scout.writing_style import ai_tells

COMPANY = "Findwhere"
TITLE = "Meetspecialist"
MOMENT = datetime(2026, 9, 18, 9, 30, tzinfo=UTC)
# Midday UTC is 10 September in every time zone the tests could run in.
CHECKED = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
REVIEWED = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
ROLE_QUESTION = "Wat moet er na een jaar in deze rol anders zijn?"
CONCERN_QUESTION = "Hoe vangt het team de piek rond de audits op?"
DRAFT = "Bij Bureau Kalibra draaide ik de jaarlijkse audit.\n\nDat ging goed."
GAP_DRAFT = "Die norm ken ik niet. Wel heb ik met een verwante norm gewerkt."
DASHES = ("\u2014", "\u2013", " - ")


def question_set(language: LetterLanguage = LetterLanguage.NL) -> InterviewQuestionSet:
    """Build a set of questions to ask, out of theme order on purpose.

    Args:
        language: The set's language.

    Returns:
        Two questions under two themes, with a gap and a source.
    """
    return InterviewQuestionSet(
        job_id=4,
        company=COMPANY,
        language=language,
        questions=[
            InterviewQuestion(
                question=CONCERN_QUESTION,
                theme=QuestionTheme.CONCERNS,
                why="De reviews noemen werkdruk.",
                grounded_in="company review: cons",
            ),
            InterviewQuestion(
                question=ROLE_QUESTION,
                theme=QuestionTheme.ROLE,
                why="Dan weet je waar je op wordt beoordeeld.",
                grounded_in="vacancy",
            ),
        ],
        generated_at=MOMENT,
        missing_context=[NO_PUBLIC_INFO, THIN_REVIEW],
        sources_used=["your own CV (cv.pdf)", "2 STAR stories"],
        company_review_date=REVIEWED,
    )


def answer_set(language: LetterLanguage = LetterLanguage.NL) -> InterviewAnswerSet:
    """Build a set with one strong answer and one gap.

    Args:
        language: The set's language.

    Returns:
        The set.
    """
    return InterviewAnswerSet(
        job_id=4,
        company=COMPANY,
        language=language,
        questions=[
            LikelyQuestion(
                question="Werk je met ISO 17025?",
                kind=QuestionKind.GAP,
                why_asked="De vacature vraagt die norm.",
                draft_answer=GAP_DRAFT,
                footing=AnswerFooting.GAP,
            ),
            LikelyQuestion(
                question="Hoe pak je een jaarlijkse audit aan?",
                kind=QuestionKind.EXPERIENCE,
                why_asked="De vacature noemt de audits.",
                draft_answer=DRAFT,
                based_on=["STAR story 3", "CV: Ervaring"],
                footing=AnswerFooting.STRONG,
            ),
        ],
        generated_at=MOMENT,
        missing_context=["no STAR stories saved yet"],
        sources_used=["your own CV (cv.pdf)"],
    )


def docx_of(interview: InterviewSet) -> bytes:
    """Render a set as Word, as the endpoint does.

    Args:
        interview: The set.

    Returns:
        The .docx bytes.
    """
    blocks = interview_blocks(interview, TITLE)
    return render_docx(blocks, interview.language, interview.generated_at)


def document_xml(data: bytes) -> str:
    """Read the body of a Word file, which is XML inside a zip archive.

    Args:
        data: The .docx bytes.

    Returns:
        word/document.xml as text.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return archive.read("word/document.xml").decode("utf-8")


def paragraphs(data: bytes) -> list[str]:
    """Open a Word file the way an editor would and read its text.

    Args:
        data: The .docx bytes.

    Returns:
        The text of every non-empty paragraph, in order.
    """
    return [p.text for p in Document(io.BytesIO(data)).paragraphs if p.text]


def test_the_questions_open_in_word_with_their_reasons_and_sources() -> None:
    """Each question is its own paragraph, with its why and source after it."""
    text = paragraphs(docx_of(question_set()))

    assert text[:3] == [
        f"{TITLE} bij {COMPANY}",
        "Vragen die jij stelt",
        "Gemaakt op 18 september 2026",
    ]
    role = text.index(ROLE_QUESTION)
    assert text[role - 1] == "De functie"
    assert text[role + 1] == (
        "Waarom dit voor jou telt: Dan weet je waar je op wordt beoordeeld."
    )
    assert text[role + 2] == "Gebaseerd op: vacature"
    assert text.index("Om door te vragen") > role
    assert text.index(CONCERN_QUESTION) > text.index("Om door te vragen")


def test_the_word_file_uses_heading_styles_and_plain_paragraphs() -> None:
    """Editable means paragraphs: no tables, no text boxes, real headings."""
    data = docx_of(answer_set())
    document = Document(io.BytesIO(data))
    styles = {p.text: p.style.name for p in document.paragraphs if p.text}

    assert styles[f"{TITLE} bij {COMPANY}"] == "Title"
    assert styles["Je ervaring"] == "Heading 1"
    assert styles["Dat ging goed."] == "Normal"
    assert document.tables == []
    assert "txbxContent" not in document_xml(data)
    assert document.core_properties.author == ""
    assert document.core_properties.language == "nl-NL"


def test_each_draft_answer_is_a_paragraph_of_its_own() -> None:
    """The draft answer is what the applicant rewrites, so it stands alone."""
    text = paragraphs(docx_of(answer_set()))

    start = text.index("Hoe pak je een jaarlijkse audit aan?")
    assert text[start : start + 7] == [
        "Hoe pak je een jaarlijkse audit aan?",
        "Waarom ze dit vragen: De vacature noemt de audits.",
        "Jouw antwoord:",
        "Bij Bureau Kalibra draaide ik de jaarlijkse audit.",
        "Dat ging goed.",
        "Onderbouwing: Sterk. Je cv of een STAR-verhaal onderbouwt dit.",
        "Gebaseerd op: STAR-verhaal 3; CV: Ervaring",
    ]
    assert GAP_DRAFT in text
    assert "Gebaseerd op: niets uit je cv, verhalen of notities" in text


def test_the_missing_context_and_sources_close_the_document() -> None:
    """What the set could not see, and what it drew on, in the set's language.

    A thin review was used, with caution, so it is not listed as missing: the
    dashboard and the command line say the same.
    """
    text = paragraphs(docx_of(question_set()))

    missing = text.index("Wat ontbrak")
    company = text.index("Gebruikte informatie over het bedrijf")
    sources = text.index("Gebruikte bronnen over jou")
    assert text[missing + 1 : company] == [
        "geen openbare informatie over het bedrijf gevonden"
    ]
    assert text[company + 1 : sources] == [
        "Beoordeling van het bedrijf van 1 september 2026",
        "De beoordeling van het bedrijf rust op weinig bronnen en is met "
        "voorzichtigheid gebruikt.",
    ]
    assert text[sources + 1 :] == ["je eigen cv (cv.pdf)", "2 STAR-verhalen"]


def test_an_english_set_gets_english_labels() -> None:
    """The labels follow the set, not the dashboard."""
    text = paragraphs(docx_of(answer_set(LetterLanguage.EN)))

    assert text[:3] == [
        f"{TITLE} at {COMPANY}",
        "Questions they may ask you, with draft answers",
        "Generated on 18 September 2026",
    ]
    assert "Gaps they will probe" in text
    assert "Your answer:" in text
    assert "Footing: Gap. You do not have this, so rehearse saying so plainly." in text
    assert "What was missing" in text
    assert "no STAR stories saved yet" in text


@pytest.mark.parametrize("build", [question_set, answer_set])
def test_the_text_file_has_the_same_content_in_the_same_order(
    build: Callable[..., InterviewSet],
) -> None:
    """Word and text are rendered from one layout, so they cannot drift."""
    interview = build()
    blocks = interview_blocks(interview, TITLE)
    word = paragraphs(render_docx(blocks, interview.language, MOMENT))
    text = [line for line in render_text(blocks).splitlines() if line.strip()]

    assert [line.casefold() for line in text] == [p.casefold() for p in word]


def test_the_text_file_reads_well_in_a_plain_editor() -> None:
    """Headings in capitals, blank lines around each question and answer."""
    text = render_text(interview_blocks(answer_set(), TITLE))

    assert text.startswith(f"{TITLE} bij {COMPANY}\n")
    assert "\n\n\nJE ERVARING\n\nHoe pak je een jaarlijkse audit aan?\n" in text
    assert (
        "\n\nJouw antwoord:\nBij Bureau Kalibra draaide ik de jaarlijkse audit."
        "\n\nDat ging goed.\n\nOnderbouwing:"
    ) in text
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert "\r" not in text


def test_a_vacancy_that_is_gone_leaves_the_company_as_title() -> None:
    """The title needs the vacancy only when it can still be looked up."""
    text = render_text(interview_blocks(question_set(), None))

    assert text.splitlines()[0] == COMPANY


@pytest.mark.parametrize("language", list(LetterLanguage))
@pytest.mark.parametrize("build", [question_set, answer_set])
def test_the_fixed_wording_follows_the_house_style(
    build: Callable[..., InterviewSet], language: LetterLanguage
) -> None:
    """No dash between clauses, no emoji and no stock phrase in our own words.

    The content here contains none of those, so anything found came from the
    labels and the layout.
    """
    interview = build(language).model_copy(
        update={"company_research_date": CHECKED, "company_review_date": CHECKED}
    )
    interview.missing_context.extend([NO_REVIEW, THIN_REVIEW, *_dated_gaps()])
    text = render_text(interview_blocks(interview, TITLE))
    xml = document_xml(docx_of(interview))

    for dash in DASHES:
        assert dash not in text
        assert dash not in xml
    assert not any(ord(char) > 0x2000 for char in text + xml)
    assert ai_tells(text) == []


def test_every_footing_theme_and_kind_has_a_label_in_both_languages() -> None:
    """A new enum value must not reach the file as a raw key or a crash."""
    for language in LetterLanguage:
        for theme in QuestionTheme:
            interview = question_set(language)
            interview.questions[0].theme = theme
            assert render_text(interview_blocks(interview, TITLE))
        for kind in QuestionKind:
            for footing in AnswerFooting:
                answers = answer_set(language)
                answers.questions[0].kind = kind
                answers.questions[0].footing = footing
                text = render_text(interview_blocks(answers, TITLE))
                assert f"{kind.value}\n" not in text
                assert f": {footing.value}\n" not in text


@pytest.mark.parametrize(
    ("build", "language", "file_format", "expected"),
    [
        (question_set, LetterLanguage.NL, ExportFormat.DOCX,
         "20260918 Interviewvragen Findwhere Meetspecialist.docx"),
        (question_set, LetterLanguage.EN, ExportFormat.TXT,
         "20260918 Interview questions Findwhere Meetspecialist.txt"),
        (answer_set, LetterLanguage.NL, ExportFormat.DOCX,
         "20260918 Interviewantwoorden Findwhere Meetspecialist.docx"),
        (answer_set, LetterLanguage.EN, ExportFormat.TXT,
         "20260918 Interview answers Findwhere Meetspecialist.txt"),
    ],
)  # fmt: skip
def test_the_file_is_named_by_date_half_company_and_vacancy(
    build: Callable[..., InterviewSet],
    language: LetterLanguage,
    file_format: ExportFormat,
    expected: str,
) -> None:
    """The name sorts by date and says what is inside, in the set's language."""
    assert export_filename(build(language), file_format, TITLE) == expected


def test_two_vacancies_at_one_employer_get_two_names() -> None:
    """Exporting both on one day must not write one file over the other."""
    first = export_filename(question_set(), ExportFormat.DOCX, "Meetspecialist")
    second = export_filename(
        question_set(), ExportFormat.DOCX, "Kwaliteitsingenieur / QA"
    )
    gone = export_filename(question_set(), ExportFormat.DOCX, None)

    assert len({first, second, gone}) == 3
    assert second == "20260918 Interviewvragen Findwhere Kwaliteitsingenieur QA.docx"
    assert gone == "20260918 Interviewvragen Findwhere vacature 4.docx"


@pytest.mark.parametrize(
    ("company", "expected"),
    [
        ('Soci\u00e9t\u00e9 "G\u00e9n\u00e9rale"', "Societe Generale"),
        ("A/B\\C: D*E?F|G<H>", "A B C D E F G H"),
        ("..\\..\\etc", "etc"),
        ("\u2728\u2728", ""),
        ("Bureau\r\nKalibra; x=1", "Bureau Kalibra x 1"),
        ("B" * 200, "B" * 60),
    ],
)
def test_the_file_name_is_ascii_and_header_safe(company: str, expected: str) -> None:
    """Whatever the scraped company name holds, the header stays one clean line."""
    interview = question_set().model_copy(update={"company": company})

    name = export_filename(interview, ExportFormat.DOCX)

    parts = ["20260918 Interviewvragen", expected, "vacature 4"]
    assert name == " ".join(filter(None, parts)) + ".docx"
    assert name.isascii()
    assert not any(char in name for char in '"\\/\r\n;:*?<>|')


def test_the_date_is_the_applicants_day_not_the_servers() -> None:
    """Late in the evening in the Netherlands is already the next day in UTC."""
    late = question_set().model_copy(
        update={"generated_at": datetime(2026, 9, 18, 22, 30, tzinfo=UTC)}
    )

    assert export_filename(late, ExportFormat.TXT).startswith("20260919 ")
    assert "Gemaakt op 19 september 2026" in render_text(interview_blocks(late))


def _dated_gaps() -> list[str]:
    """Build the entries a remembered lookup writes, the way the generator does.

    Returns:
        A dated "nothing found", a dated failure and a dated "no review".
    """
    nothing = LookupAttempt(outcome=LookupOutcome.NOTHING_FOUND, at=CHECKED)
    failed = LookupAttempt(outcome=LookupOutcome.FAILED, at=CHECKED)
    return [
        _remembered_research_gap(nothing),
        _remembered_research_gap(failed),
        *_review_gap(None, nothing),
    ]


def test_a_dated_gap_is_all_dutch_in_a_dutch_file() -> None:
    """A remembered lookup dates its entry in English; a Dutch file says it in Dutch."""
    interview = question_set().model_copy(update={"missing_context": _dated_gaps()})
    blocks = interview_blocks(interview, TITLE)
    text = render_text(blocks)
    word = paragraphs(render_docx(blocks, interview.language, MOMENT))

    expected = [
        "geen openbare informatie over het bedrijf gevonden "
        "(gecontroleerd op 10 september 2026)",
        "het bedrijfsonderzoek kon deze keer niet worden afgerond "
        "(geprobeerd op 10 september 2026)",
        "nog geen beoordeling van het bedrijf (gecontroleerd op 10 september 2026)",
    ]
    missing = word.index("Wat ontbrak")
    assert word[missing + 1 : missing + 4] == expected
    for line in expected:
        assert line in text
    for english in ("checked", "tried", "September", NO_PUBLIC_INFO, RESEARCH_FAILED):
        assert english not in text
        assert english not in " ".join(word)


def test_a_dated_gap_stays_as_it_is_in_an_english_file() -> None:
    gaps = _dated_gaps()
    interview = question_set(LetterLanguage.EN).model_copy(
        update={"missing_context": gaps}
    )

    lines = render_text(interview_blocks(interview, TITLE)).splitlines()

    assert gaps[0] == f"{NO_PUBLIC_INFO} (checked 10 September 2026)"
    assert set(gaps) <= set(lines)


def test_the_company_dates_close_the_file_in_its_language() -> None:
    """Stored research is reused for months, so its age is worth saying."""
    dated = {"company_research_date": CHECKED, "company_review_date": REVIEWED}
    dutch = answer_set().model_copy(update=dated)
    english = answer_set(LetterLanguage.EN).model_copy(update=dated)

    nl = paragraphs(docx_of(dutch))
    en = render_text(interview_blocks(english, TITLE)).splitlines()

    heading = nl.index("Gebruikte informatie over het bedrijf")
    assert nl[heading + 1 : heading + 3] == [
        "Bedrijfsonderzoek van 10 september 2026",
        "Beoordeling van het bedrijf van 1 september 2026",
    ]
    heading = en.index("COMPANY INFORMATION THAT WAS USED")
    assert en[heading + 2 : heading + 4] == [
        "Company research from 10 September 2026",
        "Company review from 1 September 2026",
    ]


def test_no_company_section_without_dates_or_a_thin_review() -> None:
    text = render_text(interview_blocks(answer_set(), TITLE))

    assert "GEBRUIKTE INFORMATIE OVER HET BEDRIJF" not in text


def test_story_citations_are_translated_only_for_display() -> None:
    """The stored set keeps the English labels the citation check compares."""
    dutch = answer_set()
    dutch.questions[1].based_on = ["star story 2", "STAR story 3", "CV: Ervaring"]
    english = answer_set(LetterLanguage.EN)

    nl = render_text(interview_blocks(dutch, TITLE))
    en = render_text(interview_blocks(english, TITLE))

    assert "Gebaseerd op: STAR-verhaal 2; STAR-verhaal 3; CV: Ervaring" in nl
    assert "Based on: STAR story 3; CV: Ervaring" in en
    assert dutch.questions[1].based_on[1] == "STAR story 3"


@pytest.mark.parametrize(
    ("grounded", "shown"),
    [
        ("company review: cons", "bedrijfsbeoordeling: nadelen"),
        ("Company research and your CV", "bedrijfsonderzoek and je cv"),
        ("vacancy, culture", "vacature, cultuur"),
        (
            "company review: pros of the team",
            "bedrijfsbeoordeling: voordelen of the team",
        ),
        ("consultancy", "consultancy"),
    ],
)
def test_a_question_source_is_shown_in_dutch_in_a_dutch_file(
    grounded: str, shown: str
) -> None:
    """Known source words are translated; anything else is left as written."""
    interview = question_set()
    interview.questions[1].grounded_in = grounded

    lines = render_text(interview_blocks(interview, TITLE)).splitlines()

    assert f"Gebaseerd op: {shown}" in lines
    assert interview.questions[1].grounded_in == grounded


def test_an_english_question_source_is_left_as_written() -> None:
    lines = render_text(interview_blocks(question_set(LetterLanguage.EN), TITLE))

    assert "Based on: company review: cons" in lines.splitlines()


@pytest.mark.parametrize(
    ("language", "sources", "shown"),
    [
        (LetterLanguage.EN, ["1 career track", "3 career tracks"],
         ["1 career track", "3 career tracks"]),
        (LetterLanguage.EN, ["1 career track(s)", "3 career track(s)"],
         ["1 career track", "3 career tracks"]),
        (LetterLanguage.NL, ["1 career track", "3 career tracks"],
         ["1 loopbaanrichting", "3 loopbaanrichtingen"]),
        (LetterLanguage.NL, ["1 career track(s)", "3 career track(s)"],
         ["1 loopbaanrichting", "3 loopbaanrichtingen"]),
    ],
)  # fmt: skip
def test_career_tracks_are_counted_in_words(
    language: LetterLanguage, sources: list[str], shown: list[str]
) -> None:
    """Sets saved before the plural was fixed read as well as new ones."""
    interview = answer_set(language).model_copy(update={"sources_used": sources})

    lines = render_text(interview_blocks(interview, TITLE)).splitlines()

    assert lines[-2:] == shown
