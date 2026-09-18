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
    NO_REVIEW,
    THIN_REVIEW,
    InterviewQuestion,
    InterviewQuestionSet,
    QuestionTheme,
)
from job_scout.interview_store import InterviewSet
from job_scout.letters.models import LetterLanguage
from job_scout.writing_style import ai_tells

COMPANY = "Findwhere"
TITLE = "Meetspecialist"
MOMENT = datetime(2026, 9, 18, 9, 30, tzinfo=UTC)
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
        missing_context=[NO_REVIEW, THIN_REVIEW],
        sources_used=["your own CV (cv.pdf)", "2 STAR stories"],
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
    assert text[role + 2] == "Gebaseerd op: vacancy"
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
        "Gebaseerd op: STAR story 3; CV: Ervaring",
    ]
    assert GAP_DRAFT in text
    assert "Gebaseerd op: niets uit je cv, verhalen of notities" in text


def test_the_missing_context_and_sources_close_the_document() -> None:
    """What the set could not see, and what it drew on, in the set's language."""
    text = paragraphs(docx_of(question_set()))

    missing = text.index("Wat ontbrak")
    assert text[missing + 1 : missing + 3] == [
        "nog geen beoordeling van het bedrijf",
        "de beoordeling van het bedrijf rust op weinig bronnen",
    ]
    sources = text.index("Gebruikte bronnen over jou")
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
    interview = build(language)
    interview.missing_context.extend([NO_REVIEW, THIN_REVIEW])
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
         "20260918 Interviewvragen Findwhere.docx"),
        (question_set, LetterLanguage.EN, ExportFormat.TXT,
         "20260918 Interview questions Findwhere.txt"),
        (answer_set, LetterLanguage.NL, ExportFormat.DOCX,
         "20260918 Interviewantwoorden Findwhere.docx"),
        (answer_set, LetterLanguage.EN, ExportFormat.TXT,
         "20260918 Interview answers Findwhere.txt"),
    ],
)  # fmt: skip
def test_the_file_is_named_by_date_half_and_company(
    build: Callable[..., InterviewSet],
    language: LetterLanguage,
    file_format: ExportFormat,
    expected: str,
) -> None:
    """The name sorts by date and says what is inside, in the set's language."""
    assert export_filename(build(language), file_format) == expected


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

    assert name == " ".join(filter(None, ["20260918 Interviewvragen", expected])) + (
        ".docx"
    )
    assert name.isascii()
    assert not any(char in name for char in '"\\/\r\n;:*?<>|')


def test_the_date_is_the_applicants_day_not_the_servers() -> None:
    """Late in the evening in the Netherlands is already the next day in UTC."""
    late = question_set().model_copy(
        update={"generated_at": datetime(2026, 9, 18, 22, 30, tzinfo=UTC)}
    )

    assert export_filename(late, ExportFormat.TXT).startswith("20260919 ")
    assert "Gemaakt op 19 september 2026" in render_text(interview_blocks(late))
