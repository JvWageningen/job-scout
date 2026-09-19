"""Memories reach the generators allowed to use them, and nothing else does.

Every person, employer and project here is invented. Nothing here calls a
model or the network: the model is a FakeLLMClient and company lookups are
stubbed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

import job_scout.config as config
from job_scout.applicant import (
    SOURCE_GUIDE,
    describe_sources,
    gather_applicant_facts,
    memories_for_vacancy,
)
from job_scout.cli import cli
from job_scout.cv.models import CVDocument, ExperienceEntry, ExperienceSection
from job_scout.cv.storage import ProfileStore
from job_scout.cv.tailor import tailored_slug
from job_scout.database import Database
from job_scout.interview_answers import (
    AnswerFooting,
    LikelyQuestion,
    QuestionKind,
    _check_citations,
    generate_interview_answers,
)
from job_scout.interview_questions import (
    InterviewQuestion,
    InterviewQuestionSet,
    QuestionTheme,
    _drop_unsupported,
    generate_interview_questions,
    unknown_memory_citation,
)
from job_scout.letters.models import LetterLanguage, LetterRequest
from job_scout.letters.writer import write_letter
from job_scout.memories import (
    MEMORIES_SOURCE_KEY,
    MEMORY_GUIDE,
    Memory,
    MemoryDraft,
    MemoryKind,
    MemorySource,
    MemoryUse,
    add_memories,
    list_memories,
    parse_memory_label,
)
from job_scout.memory_extract import set_auto_capture
from job_scout.models import CvProfile, JobListing, JobStatus
from tests.helpers import FakeLLMClient
from tests.style_checks import assert_plain, assert_styled_prompt

USER = "Sam"
NOW = datetime(2026, 9, 19, 9, 0, tzinfo=UTC)
COMPANY = "Voorbeeldwinkel Online"
DESCRIPTION = (
    "Wij zoeken een conversiespecialist die A/B-tests opzet met Optimizely en "
    "de resultaten deelt met het team. Je werkt in een team dat de webshop "
    "verbetert en je bent verantwoordelijk voor de kwaliteit van de tests."
)

SHARED = "Ik heb in 2023 een programma van veertig A/B-tests opgezet met Optimizely."
LETTER_ONLY = "Ik wil graag werken bij een bedrijf dat duurzaam onderneemt."
INTERVIEW_ONLY = "Ik kan per 1 november beginnen, na mijn opzegtermijn."
CV_ONLY = (
    "Ik schreef de testrichtlijnen van de webshop, die nog steeds gebruikt worden."
)
PRIVATE = "Ik ben in 2022 drie maanden thuis geweest met een burn-out."
NOWHERE = "Ik heb een oude hobbywebsite over aquaria bijgehouden."


@pytest.fixture(autouse=True)
def lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every interview test off the network: no research, no review."""
    monkeypatch.setattr(
        "job_scout.interview_questions.research_company", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "job_scout.interview_questions.review_company", lambda *_a, **_k: None
    )


@pytest.fixture
def job_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """One user with a real CV Builder profile and one Dutch vacancy."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    config.write_global_config({"llm_provider": "local"})
    config.save_user_config(USER, {})
    ProfileStore(config.user_cv_dir(USER)).save("nederlands", _cv())
    return Database(config.user_db_path(USER)).save_job(
        JobListing(
            title="Conversiespecialist",
            company=COMPANY,
            location="Utrecht",
            url="https://voorbeeld.example/vacature/cro",
            source="test",
            description=DESCRIPTION,
        )
    )


def _cv() -> CVDocument:
    """A small real CV, so every generator has career facts to start from."""
    return CVDocument(
        full_name="Sam de Vries",
        language="NL",
        main=[
            ExperienceSection(
                id="xp",
                title="Werkervaring",
                entries=[
                    ExperienceEntry(
                        id="e1",
                        title="Online marketeer",
                        organisation="Tuinhuis Noord",
                        period="2019 - 2025",
                        bullets=["Beheerde de webshop"],
                    )
                ],
            )
        ],
    )


@pytest.fixture
def stored(job_id: int) -> dict[str, Memory]:
    """One memory per way of limiting where a memory may go."""
    drafts = {
        "shared": MemoryDraft(
            text=SHARED,
            kind=MemoryKind.PROJECT,
            tags=["optimizely", "a/b-tests"],
            hint="Gebruik voor CRO-rollen.",
        ),
        "letter": MemoryDraft(
            text=LETTER_ONLY, kind=MemoryKind.PREFERENCE, use_in=[MemoryUse.LETTER]
        ),
        "interview": MemoryDraft(
            text=INTERVIEW_ONLY,
            kind=MemoryKind.CONSTRAINT,
            use_in=[MemoryUse.INTERVIEW],
        ),
        "cv": MemoryDraft(
            text=CV_ONLY, kind=MemoryKind.ACHIEVEMENT, use_in=[MemoryUse.CV]
        ),
        "private": MemoryDraft(text=PRIVATE, kind=MemoryKind.PERSONAL, sensitive=True),
        "nowhere": MemoryDraft(text=NOWHERE, use_in=[]),
    }
    saved = add_memories(USER, list(drafts.values()))
    return dict(zip(drafts, saved, strict=True))


def _letter_response() -> str:
    """A well-formed letter body."""
    return json.dumps(
        {
            "paragraphs": [
                "Ik solliciteer op de functie van conversiespecialist.",
                "Bij Tuinhuis Noord heb ik de webshop beheerd.",
                "Ik vertel graag meer in een gesprek.",
            ]
        }
    )


def _answers_response(based_on: list[str]) -> str:
    """A well-formed set of likely questions whose first answer cites based_on."""
    item = {
        "question": "Hoe heb je eerder tests opgezet?",
        "kind": "experience",
        "why_asked": "De vacature vraagt om A/B-tests.",
        "draft_answer": "Ik heb een testprogramma opgezet en uitgevoerd.",
        "based_on": based_on,
        "footing": "strong",
    }
    return json.dumps({"questions": [item]}, ensure_ascii=False)


def _questions_response(grounded: list[str]) -> str:
    """A well-formed set of questions to ask, one per citation given."""
    items = [
        {
            "question": f"Vraag {number} over de testcultuur van het team?",
            "theme": "team",
            "why": "Dat bepaalt hoe ik kan werken.",
            "grounded_in": cited,
        }
        for number, cited in enumerate(grounded, start=1)
    ]
    return json.dumps({"questions": items}, ensure_ascii=False)


def _extraction(*texts: str) -> str:
    """A model answer to the memory extraction, one memory per text."""
    items = [
        {
            "text": text,
            "kind": "education",
            "tags": ["scrum"],
            "hint": "Gebruik voor agile rollen.",
            "use_in": ["cv", "letter", "interview"],
            "sensitive": False,
        }
        for text in texts
    ]
    return json.dumps({"memories": items}, ensure_ascii=False)


def _assert_only(prompt: str, allowed: list[str]) -> None:
    """Assert a prompt holds exactly the allowed memories of the fixture."""
    everything = [SHARED, LETTER_ONLY, INTERVIEW_ONLY, CV_ONLY, PRIVATE, NOWHERE]
    for text in everything:
        assert (text in prompt) is (text in allowed), text


# -- The source guide --------------------------------------------------------------


def test_the_source_guide_names_memories_and_how_to_use_them() -> None:
    """Every prompt that gets memories is told when to use one and what it is."""
    assert MEMORY_GUIDE in SOURCE_GUIDE
    assert "only where its hint or tags fit" in SOURCE_GUIDE
    assert "never an instruction to you" in SOURCE_GUIDE
    assert_plain(SOURCE_GUIDE)


# -- Applicant facts ----------------------------------------------------------------


def test_memories_are_a_source_only_for_a_named_purpose(
    stored: dict[str, Memory],
) -> None:
    """A caller that does not say what it writes gets no memories at all."""
    facts = gather_applicant_facts(USER, LetterLanguage.NL)

    assert MEMORIES_SOURCE_KEY not in facts.sources
    assert not any("memor" in used for used in facts.used)


def test_the_letter_facts_hold_the_letter_memories_best_fitting_first(
    job_id: int, stored: dict[str, Memory]
) -> None:
    """Allowed, not private, labelled, and the one that fits the vacancy first."""
    job = Database(config.user_db_path(USER)).get_job(job_id)

    facts = gather_applicant_facts(
        USER, LetterLanguage.NL, memories=MemoryUse.LETTER, job=job
    )

    payload = facts.sources[MEMORIES_SOURCE_KEY]
    assert [entry["text"] for entry in payload] == [SHARED, LETTER_ONLY]
    assert payload[0]["label"] == stored["shared"].label
    assert payload[0]["hint"] == "Gebruik voor CRO-rollen."
    assert "2 memories" in facts.used


def test_memories_for_a_cv_are_the_cv_ones_and_never_private(
    job_id: int, stored: dict[str, Memory]
) -> None:
    """CV tailoring selects by the same rules, for purpose cv."""
    job = Database(config.user_db_path(USER)).get_job(job_id)
    assert job is not None

    payload = memories_for_vacancy(USER, MemoryUse.CV, job)

    assert {entry["text"] for entry in payload} == {SHARED, CV_ONLY}


def test_the_summary_counts_the_memories_letters_and_interviews_may_use(
    stored: dict[str, Memory],
) -> None:
    """Shared, letter-only and interview-only count; cv-only, private, unused not."""
    summary = describe_sources(USER)

    assert "3 memories, where they fit the vacancy" in summary["used"]


@pytest.mark.parametrize("purpose", [MemoryUse.LETTER, "interview"])
def test_the_summary_for_one_document_counts_only_the_memories_it_may_use(
    stored: dict[str, Memory], purpose: MemoryUse | str
) -> None:
    """The letter tab leaves out interview-only memories, and the other way."""
    used = describe_sources(USER, purpose)["used"]

    assert "2 memories, where they fit the vacancy" in used
    assert not any(line.startswith("3 memories") for line in used)


@pytest.mark.parametrize(
    ("value", "label"),
    [
        ("memory 3", "memory 3"),
        ("Memory #3", "memory 3"),
        ("memory3", "memory 3"),
        ("herinnering nr. 3", "memory 3"),
        ("your memory 3", "memory 3"),
        ("memory 3 (the dbt move)", "memory 3"),
        (3, "memory 3"),
        ("3", None),
        (0, None),
        (True, None),
        (None, None),
        ("memory", None),
        ("in-memory 3", None),
        ("High Bandwidth Memory 3", None),
    ],
)
def test_parse_memory_label(value: object, label: str | None) -> None:
    """Every citation check reads a label the same way."""
    assert parse_memory_label(value) == label


def test_a_bare_number_is_a_label_only_where_nothing_else_can_be_meant() -> None:
    assert parse_memory_label("3", bare_number=True) == "memory 3"
    assert parse_memory_label(" 12 ", bare_number=True) == "memory 12"


def test_a_user_without_memories_sees_no_memory_line(job_id: int) -> None:
    """Nothing stored, nothing said."""
    summary = describe_sources(USER)

    assert not any("memor" in used for used in summary["used"])


# -- Letters -----------------------------------------------------------------------


def test_the_letter_prompt_carries_only_the_letter_memories(
    job_id: int, stored: dict[str, Memory]
) -> None:
    """Private ones and ones kept out of letters never reach the letter model."""
    client = FakeLLMClient([_letter_response()])

    letter = write_letter(USER, LetterRequest(job_id=job_id), client)

    prompt = client.calls[0][0]
    _assert_only(prompt, [SHARED, LETTER_ONLY])
    assert stored["shared"].label in prompt
    assert "only where its hint or tags fit" in prompt
    assert "2 memories" in letter.sources_used
    assert_styled_prompt(prompt)


def _approve(job_id: int) -> None:
    """Move the vacancy to approved, which the older profile commands require."""
    db = Database(config.user_db_path(USER))
    for status in (JobStatus.VIEWED, JobStatus.APPROVED):
        assert db.update_job_status(job_id, status)


def _older_command(
    tmp_path: Path, job_id: int, monkeypatch: pytest.MonkeyPatch, answers: list[str]
) -> FakeLLMClient:
    """Set up an older 'profile' command: a CV file, an approved vacancy, a model.

    Args:
        tmp_path: Where the CV file goes.
        job_id: The vacancy, approved here.
        monkeypatch: To put the fake model and CV parser in place.
        answers: What the model answers, in order.

    Returns:
        The fake model, to read its prompts afterwards.
    """
    cv_file = tmp_path / "cv.txt"
    cv_file.write_text("Online marketeer bij Tuinhuis Noord", encoding="utf-8")
    config.save_user_config(USER, {"cv_path": str(cv_file)})
    _approve(job_id)
    client = FakeLLMClient(answers)
    monkeypatch.setattr("job_scout.llm.factory.get_llm_client", lambda _: client)
    monkeypatch.setattr("job_scout.cv_parser.parse_cv", lambda _: "Online marketeer")
    monkeypatch.setattr(
        "job_scout.cv_profile.get_or_parse_cv_profile", lambda *_: CvProfile()
    )
    return client


def test_the_older_cover_letter_command_uses_the_letter_memories(
    tmp_path: Path,
    job_id: int,
    stored: dict[str, Memory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'profile generate-cover-letter' writes a letter too, so it gets them."""
    client = _older_command(
        tmp_path, job_id, monkeypatch, ["Beste lezer, ik solliciteer graag."]
    )

    result = CliRunner().invoke(
        cli, ["profile", "generate-cover-letter", str(job_id), "--user", USER]
    )

    assert result.exit_code == 0, result.output
    prompt = client.calls[0][0]
    _assert_only(prompt, [SHARED, LETTER_ONLY])
    assert "only where its hint or tags fit" in prompt
    assert_styled_prompt(prompt)


def test_screening_answers_use_the_letter_memories(
    tmp_path: Path,
    job_id: int,
    stored: dict[str, Memory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Screening answers go out with the application, like a letter."""
    question = "Wanneer kun je beginnen?"
    answers = [
        json.dumps({"questions": [question]}),
        json.dumps({"answers": {question: "Na mijn opzegtermijn."}}),
    ]
    client = _older_command(tmp_path, job_id, monkeypatch, answers)

    result = CliRunner().invoke(
        cli, ["profile", "answer-screening", str(job_id), "--user", USER]
    )

    assert result.exit_code == 0, result.output
    _assert_only(client.calls[0][0], [])
    _assert_only(client.calls[1][0], [SHARED, LETTER_ONLY])


# -- Interview questions and answers ---------------------------------------------------


def test_the_interview_question_prompt_carries_only_the_interview_memories(
    job_id: int, stored: dict[str, Memory]
) -> None:
    """The questions to ask see what the applicant allowed in interviews."""
    client = FakeLLMClient([_questions_response(["vacancy"])])

    generate_interview_questions(USER, job_id, client)

    _assert_only(client.calls[-1][0], [SHARED, INTERVIEW_ONLY])


def test_the_interview_answer_prompt_carries_only_the_interview_memories(
    job_id: int, stored: dict[str, Memory]
) -> None:
    """The draft answers see the same memories, with labels to cite."""
    client = FakeLLMClient([_answers_response(["CV: Werkervaring"])])

    result = generate_interview_answers(USER, job_id, client)

    prompt = client.calls[-1][0]
    _assert_only(prompt, [SHARED, INTERVIEW_ONLY])
    assert f'"label": "{stored["interview"].label}"' in prompt
    assert "2 memories" in result.sources_used


def test_an_answer_may_cite_a_memory_it_was_given_and_no_other(
    job_id: int, stored: dict[str, Memory]
) -> None:
    """A private memory's label and an unknown one are dropped as citations."""
    cited = [
        stored["shared"].label,
        stored["private"].label,
        "memory 999",
        "CV: Werkervaring",
    ]
    client = FakeLLMClient([_answers_response(cited)])

    result = generate_interview_answers(USER, job_id, client)

    assert result.questions[0].based_on == [stored["shared"].label, "CV: Werkervaring"]
    assert result.questions[0].footing is AnswerFooting.STRONG


def test_a_question_citing_an_unknown_memory_is_dropped(
    job_id: int, stored: dict[str, Memory]
) -> None:
    """A question claiming a memory the model never saw is not believed."""
    client = FakeLLMClient(
        [_questions_response([stored["interview"].label, "memory 999"])]
    )

    result = generate_interview_questions(USER, job_id, client)

    assert [q.grounded_in for q in result.questions] == [stored["interview"].label]


def _answer(based_on: list[str]) -> LikelyQuestion:
    """A likely question whose citations a test sets."""
    return LikelyQuestion(
        question="Waarom deze rol?",
        kind=QuestionKind.MOTIVATION,
        why_asked="De vacature noemt het.",
        draft_answer="Omdat ik dit eerder deed.",
        based_on=based_on,
        footing=AnswerFooting.STRONG,
    )


def test_memory_citations_are_checked_like_story_citations() -> None:
    """Known labels stay in any spelling; unknown ones go, as for STAR stories."""
    item = _answer(["Memory 3", "memory #3", "herinnering 3", "memory 8", "memories"])

    _check_citations([item], [], {"memory 3"})

    assert item.based_on == ["Memory 3", "memory #3", "herinnering 3", "memories"]


def test_citing_memories_when_there_were_none_leaves_nothing_behind_it() -> None:
    """With nothing cited that was in the prompt, strong footing is not believed."""
    item = _answer(["memory 3", "your memories"])

    _check_citations([item], [])

    assert item.based_on == []
    assert item.footing is AnswerFooting.PARTIAL


def test_a_story_citation_is_still_checked_next_to_memories() -> None:
    """Adding memories did not loosen the check on stories."""
    item = _answer(["STAR story 9", "memory 3"])

    _check_citations([item], [{"label": "STAR story 1"}], {"memory 3"})

    assert item.based_on == ["memory 3"]


@pytest.mark.parametrize(
    ("cited", "unknown"),
    [
        ("vacancy", False),
        ("memory 3", False),
        ("memory 4", True),
        ("your CV and memory 3", False),
        ("memories", False),
        ("memory 3 (the dbt move)", False),
        ("Memory #4", True),
        ("your CV, memory 4", True),
    ],
)
def test_unknown_memory_citation(cited: str, unknown: bool) -> None:
    """Only a memory the prompt did not hold makes a citation false."""
    assert unknown_memory_citation(cited, {"memory 3"}) is unknown


TECHNICAL_MEMORY = [
    "vacancy: in-memory databases",
    "CV: Werkervaring (memory management in embedded C)",
    "your CV: memory controller design",
    "vacancy: High Bandwidth Memory 3",
    "CV: 16 GB memory tuning at Beta NV",
]


@pytest.mark.parametrize("cited", TECHNICAL_MEMORY)
def test_a_source_that_only_uses_the_word_memory_names_no_memory(cited: str) -> None:
    """Without memories in the prompt, a technical 'memory' is still a source."""
    assert unknown_memory_citation(cited, set()) is False


def test_an_answer_citing_technical_memory_keeps_its_citation_and_footing() -> None:
    """A user without memories keeps a CV citation that happens to say memory."""
    item = _answer(["CV: Data Engineer at Beta NV (memory profiling)"])

    _check_citations([item], [])

    assert item.based_on == ["CV: Data Engineer at Beta NV (memory profiling)"]
    assert item.footing is AnswerFooting.STRONG


def test_a_question_about_in_memory_software_is_kept_without_memories() -> None:
    question = InterviewQuestion(
        question="Welke in-memory database gebruiken jullie?",
        theme=QuestionTheme.ROLE,
        why="...",
        grounded_in="vacancy: in-memory data grid",
    )

    assert _drop_unsupported([question], [], set()) == [question]


def test_drop_unsupported_keeps_questions_citing_known_memories() -> None:
    """The memory check sits next to the company check without replacing it."""
    questions = [
        InterviewQuestion(
            question=f"Vraag {n}?", theme=QuestionTheme.ROLE, why="...", grounded_in=g
        )
        for n, g in enumerate(["memory 3", "memory 5", "company review: cons"])
    ]

    kept = _drop_unsupported(questions, ["no company review yet"], {"memory 3"})

    assert [q.grounded_in for q in kept] == ["memory 3"]


# -- CLI capture after a generation --------------------------------------------------


def _run_letter(job_id: int, notes: str) -> Result:
    """Run 'letter generate' with the given notes."""
    return CliRunner().invoke(
        cli,
        ["letter", "generate", str(job_id), "--user", USER, "--notes", notes],
    )


def test_letter_notes_become_memories_after_the_letter(
    job_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command writes the letter, then keeps the fact and says so."""
    fact = "Ik heb in 2024 het certificaat PSM I gehaald."
    client = FakeLLMClient([_letter_response(), _extraction(fact)], repeat_last=False)
    monkeypatch.setattr("job_scout.letters.cli.get_llm_client", lambda _: client)

    result = _run_letter(job_id, f"Noem dat {fact}")

    assert result.exit_code == 0, result.output
    assert "Saved 1 new memory from your notes" in result.output
    assert fact in result.output
    [memory] = list_memories(USER)
    assert memory.text == fact
    assert memory.source is MemorySource.LETTER_NOTES
    assert memory.source_detail == f"notes for the {COMPANY} letter"
    assert memory.job_id == job_id
    assert client.calls[1][1] == "cv_parsing"


def test_the_same_notes_are_not_read_twice(
    job_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Generating again with the same notes costs no second extraction."""
    notes = "Ik heb in 2024 het certificaat PSM I gehaald."
    first = FakeLLMClient([_letter_response(), _extraction(notes)], repeat_last=False)
    again = FakeLLMClient([_letter_response()], repeat_last=False)
    clients = iter([first, again])
    monkeypatch.setattr("job_scout.letters.cli.get_llm_client", lambda _: next(clients))

    _run_letter(job_id, notes)
    result = _run_letter(job_id, notes)

    assert result.exit_code == 0, result.output
    assert len(again.calls) == 1
    assert len(list_memories(USER)) == 1


def test_switching_capture_off_keeps_notes_out_of_memories(
    job_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the switch off no model is asked and nothing is said or stored."""
    set_auto_capture(USER, False)
    client = FakeLLMClient([_letter_response()], repeat_last=False)
    monkeypatch.setattr("job_scout.letters.cli.get_llm_client", lambda _: client)

    result = _run_letter(job_id, "Ik heb in 2024 PSM I gehaald.")

    assert result.exit_code == 0, result.output
    assert len(client.calls) == 1
    assert list_memories(USER) == []
    assert "memor" not in result.output.casefold()


def test_a_failed_capture_does_not_fail_the_letter(
    job_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The letter is done; unreadable extraction is reported and retried later."""
    client = FakeLLMClient([_letter_response(), "no json here"], repeat_last=False)
    monkeypatch.setattr("job_scout.letters.cli.get_llm_client", lambda _: client)

    result = _run_letter(job_id, "Ik heb in 2024 PSM I gehaald.")

    assert result.exit_code == 0, result.output
    assert "could not be read for memories this time" in result.output
    assert list_memories(USER) == []


def test_interview_notes_become_memories_after_the_questions(
    job_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interview commands capture their notes the same way."""
    fact = "Ik spreek vloeiend Duits."
    client = FakeLLMClient([_extraction(fact)], repeat_last=False)
    monkeypatch.setattr("job_scout.cli.check_llm_available", lambda _: (True, None))
    monkeypatch.setattr("job_scout.cli.get_llm_client", lambda _: client)
    monkeypatch.setattr(
        "job_scout.cli.generate_interview_questions",
        lambda user, job, *_a, **_k: InterviewQuestionSet(
            job_id=job,
            company=COMPANY,
            language=LetterLanguage.NL,
            questions=[
                InterviewQuestion(
                    question="Hoe test het team?",
                    theme=QuestionTheme.TEAM,
                    why="Dat wil ik weten.",
                    grounded_in="vacancy",
                )
            ],
            generated_at=NOW,
        ),
    )

    result = CliRunner().invoke(
        cli,
        ["interview", "questions", str(job_id), "--user", USER, "--notes", fact],
    )

    assert result.exit_code == 0, result.output
    assert "Saved 1 new memory from your notes" in result.output
    [memory] = list_memories(USER)
    assert memory.source is MemorySource.INTERVIEW_NOTES
    assert memory.source_detail == f"notes for the {COMPANY} interview questions"


# -- CV tailoring from the command line ----------------------------------------------


def test_cv_tailoring_offers_the_cv_memories_and_one_can_become_a_bullet(
    tmp_path: Path,
    job_id: int,
    stored: dict[str, Memory],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'cv tailor' sends the cv memories, and a cited one adds a bullet."""
    label = stored["cv"].label
    bullet = "Schreef de testrichtlijnen van de webshop"
    patch = {"bullets": ["Beheerde de webshop", bullet], "memories": [label]}
    tailoring = json.dumps({"sections": {"xp": {"entries": {"e1": patch}}}})
    client = FakeLLMClient(['{"keywords": ["A/B-tests"]}', tailoring])
    monkeypatch.setattr("job_scout.cv.cli.get_llm_client", lambda _: client)
    monkeypatch.setattr("job_scout.cv.cli._write_pdf", lambda *_args: 1)
    output = tmp_path / "tailored.pdf"

    result = CliRunner().invoke(
        cli,
        ["cv", "tailor", str(job_id), "--user", USER, "--slug", "nederlands"]
        + ["-o", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert "Offering 2 memories" in result.output
    _assert_only(client.calls[1][0], [SHARED, CV_ONLY])
    job = Database(config.user_db_path(USER)).get_job(job_id)
    assert job is not None
    saved = ProfileStore(config.user_cv_dir(USER)).load(
        tailored_slug("nederlands", job)
    )
    section = saved.main[0]
    assert isinstance(section, ExperienceSection)
    assert section.entries[0].bullets == ["Beheerde de webshop", bullet]
