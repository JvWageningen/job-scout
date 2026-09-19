"""Tailoring a designed CV document to one vacancy."""

from __future__ import annotations

import json
from typing import Any

import pytest

from job_scout.cv import tailor
from job_scout.cv.models import (
    ContactItem,
    ContactSection,
    CVDocument,
    DetailItem,
    DetailsSection,
    EducationEntry,
    EducationSection,
    ExperienceEntry,
    ExperienceSection,
    ListSection,
    SkillItem,
    SkillsSection,
    TextSection,
)
from job_scout.cv.storage import StorageError, normalise_slug
from job_scout.cv.tailor import (
    SLUG_LIMIT,
    TailorError,
    TailorPlan,
    _verify_integrity,
    tailor_cv_document,
    tailored_slug,
)
from job_scout.memories import MEMORY_CV_RULE
from job_scout.models import JobListing
from tests.helpers import FakeLLMClient
from tests.style_checks import GENERATED, PLAIN, assert_styled_prompt

KEYWORDS = '{"keywords": ["Python", "SQL", "pipelines"]}'


def make_doc() -> CVDocument:
    """A small two-column CV with stable ids, so plans can name them."""
    return CVDocument(
        full_name="Sam de Vries",
        headline="Data Engineer",
        photo="portrait.png",
        sidebar=[
            SkillsSection(
                id="sk",
                title="Skills",
                items=[
                    SkillItem(id="s1", name="Welding"),
                    SkillItem(id="s2", name="Python"),
                    SkillItem(id="s3", name="SQL"),
                ],
            ),
            ContactSection(
                id="ct",
                title="Contact",
                items=[
                    ContactItem(id="c1", icon="envelope", value="sam@example.com"),
                ],
            ),
            DetailsSection(
                id="dt",
                title="Details",
                items=[DetailItem(id="p1", label="Date of birth", value="12-05-1987")],
            ),
            ListSection(
                id="ls",
                title="Languages",
                items=["Dutch", "English", "German"],
            ),
        ],
        main=[
            TextSection(
                id="pf",
                title="Profile",
                body="Engineer with a soldering iron and a terminal.",
            ),
            ExperienceSection(
                id="xp",
                title="Experience",
                entries=[
                    ExperienceEntry(
                        id="e1",
                        title="Field Engineer",
                        organisation="Acme BV",
                        period="2014 - 2019",
                        description="Installed lasers across the Benelux.",
                        bullets=["Trained customers", "Repaired optics"],
                    ),
                    ExperienceEntry(
                        id="e2",
                        title="Data Engineer",
                        organisation="Beta NV",
                        period="2019 - 2025",
                        description="Built reporting pipelines.",
                        bullets=["Shipped an ETL stack"],
                    ),
                ],
            ),
            EducationSection(
                id="ed",
                title="Education",
                entries=[
                    EducationEntry(
                        id="d1",
                        degree="BSc Applied Physics",
                        school="TU Delft",
                        period="2008 - 2012",
                    ),
                    EducationEntry(
                        id="d2",
                        degree="MSc Data Science",
                        school="UvA",
                        period="2012 - 2014",
                    ),
                ],
            ),
        ],
    )


def make_job(**overrides: Any) -> JobListing:
    """A vacancy, with any field overridden."""
    fields: dict[str, Any] = {
        "title": "Data Engineer",
        "company": "Meridiaan Data",
        "location": "Utrecht",
        "url": "https://example.invalid/vacancy/1",
        "source": "test",
        "description": "We build Python and SQL data pipelines.",
    }
    fields.update(overrides)
    return JobListing(**fields)


def plan(**payload: Any) -> str:
    """Serialise a tailoring plan the way the model would return it."""
    return json.dumps(payload)


def client_for(*responses: str, keywords: str = KEYWORDS) -> FakeLLMClient:
    """A fake client that answers the keyword call and then the tailoring call."""
    return FakeLLMClient([keywords, *responses], repeat_last=False)


def experience_of(doc: CVDocument) -> ExperienceSection:
    """The document's experience section."""
    section = next(s for s in doc.all_sections() if s.id == "xp")
    assert isinstance(section, ExperienceSection)
    return section


HAPPY_PLAN = plan(
    main=["xp", "pf", "ed"],
    sections={
        "pf": {"body": "Data engineer who builds Python and SQL pipelines."},
        "xp": {
            "order": ["e2", "e1"],
            "entries": {
                "e2": {
                    "description": "Built Python and SQL data pipelines.",
                    "bullets": ["Shipped an ETL stack in Python"],
                }
            },
        },
    },
)


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


def test_tailoring_rewords_prose_and_reorders_content() -> None:
    result = tailor_cv_document(make_doc(), make_job(), client_for(HAPPY_PLAN))

    assert [section.id for section in result.main] == ["xp", "pf", "ed"]
    experience = experience_of(result)
    assert [entry.id for entry in experience.entries] == ["e2", "e1"]
    assert experience.entries[0].description == "Built Python and SQL data pipelines."
    assert experience.entries[0].bullets == ["Shipped an ETL stack in Python"]
    profile = result.main[1]
    assert isinstance(profile, TextSection)
    assert profile.body == "Data engineer who builds Python and SQL pipelines."


def test_reworded_prose_comes_back_plain_and_facts_stay_as_they_are() -> None:
    """Only the prose the tailor may rewrite is cleaned; periods keep their hyphen."""
    response = plan(
        sections={
            "pf": {"body": GENERATED},
            "xp": {
                "entries": {
                    "e1": {
                        "period": "2014 - 2019",
                        "description": GENERATED,
                        "bullets": [GENERATED, "\U0001f680"],
                    }
                }
            },
        },
    )
    client = client_for(response)

    result = tailor_cv_document(make_doc(), make_job(), client)

    profile = result.main[0]
    assert isinstance(profile, TextSection)
    assert profile.body == PLAIN
    entry = experience_of(result).entries[0]
    assert entry.description == PLAIN
    assert entry.bullets == [PLAIN]
    assert (entry.title, entry.organisation, entry.period) == (
        "Field Engineer",
        "Acme BV",
        "2014 - 2019",
    )
    assert_styled_prompt(client.calls[1][0])


def test_a_period_inside_reworded_prose_stays_a_range() -> None:
    """A comma would make "jan 2019 to heden" say two separate things."""
    response = plan(
        sections={
            "pf": {"body": "Sinds 2019 \u2013 heden in de meettechniek."},
            "xp": {
                "entries": {
                    "e1": {
                        "bullets": [
                            "Trained customers (jan 2019 \u2013 dec 2021)",
                            "Repaired optics, 2014 \u2013 2019",
                        ]
                    }
                }
            },
        }
    )

    result = tailor_cv_document(make_doc(), make_job(), client_for(response))

    profile = result.main[0]
    assert isinstance(profile, TextSection)
    assert profile.body == "Sinds 2019-heden in de meettechniek."
    assert experience_of(result).entries[0].bullets == [
        "Trained customers (jan 2019-dec 2021)",
        "Repaired optics, 2014-2019",
    ]


def test_clean_prose_does_not_hide_a_changed_employer() -> None:
    response = plan(
        sections={
            "xp": {
                "entries": {
                    "e1": {
                        "organisation": "Globex Industries",
                        "description": GENERATED,
                    }
                }
            }
        }
    )

    with pytest.raises(TailorError, match="not editable"):
        tailor_cv_document(make_doc(), make_job(), client_for(response))


def test_the_source_document_is_never_mutated() -> None:
    doc = make_doc()
    before = doc.model_dump()

    result = tailor_cv_document(doc, make_job(), client_for(HAPPY_PLAN))

    assert result is not doc
    assert doc.model_dump() == before


def test_design_identity_and_columns_survive_tailoring() -> None:
    doc = make_doc()

    result = tailor_cv_document(doc, make_job(), client_for(HAPPY_PLAN))

    assert result.full_name == doc.full_name
    assert result.headline == doc.headline
    assert result.photo == doc.photo
    assert result.theme == doc.theme
    assert result.language == doc.language
    assert {s.id for s in result.sidebar} == {s.id for s in doc.sidebar}
    assert {s.id for s in result.main} == {s.id for s in doc.main}


def test_sidebar_skills_and_list_items_can_be_reordered() -> None:
    response = plan(
        sidebar=["sk", "ls", "ct", "dt"],
        sections={
            "sk": {"order": ["s2", "s3", "s1"]},
            "ls": {"order": ["English", "Dutch", "German"]},
        },
    )

    result = tailor_cv_document(make_doc(), make_job(), client_for(response))

    assert [section.id for section in result.sidebar] == ["sk", "ls", "ct", "dt"]
    skills = result.sidebar[0]
    assert isinstance(skills, SkillsSection)
    assert [item.name for item in skills.items] == ["Python", "SQL", "Welding"]
    languages = result.sidebar[1]
    assert isinstance(languages, ListSection)
    assert languages.items == ["English", "Dutch", "German"]


def test_education_entries_can_be_reordered() -> None:
    response = plan(sections={"ed": {"order": ["d2", "d1"]}})

    result = tailor_cv_document(make_doc(), make_job(), client_for(response))

    education = result.main[2]
    assert isinstance(education, EducationSection)
    assert [entry.degree for entry in education.entries] == [
        "MSc Data Science",
        "BSc Applied Physics",
    ]


def test_personal_detail_sections_are_left_untouched() -> None:
    response = plan(
        sections={
            "ct": {"body": "call me", "order": ["c1"]},
            "dt": {"body": "born yesterday"},
        }
    )

    result = tailor_cv_document(make_doc(), make_job(), client_for(response))

    contact = result.sidebar[1]
    details = result.sidebar[2]
    assert isinstance(contact, ContactSection)
    assert isinstance(details, DetailsSection)
    assert contact.items[0].value == "sam@example.com"
    assert details.items[0].value == "12-05-1987"


def test_dropping_a_bullet_is_allowed() -> None:
    response = plan(
        sections={"xp": {"entries": {"e1": {"bullets": ["Trained customers"]}}}}
    )

    result = tailor_cv_document(make_doc(), make_job(), client_for(response))

    assert experience_of(result).entries[0].bullets == ["Trained customers"]


def test_a_fenced_response_is_understood() -> None:
    response = f"Sure!\n```json\n{HAPPY_PLAN}\n```"

    result = tailor_cv_document(make_doc(), make_job(), client_for(response))

    assert [section.id for section in result.main] == ["xp", "pf", "ed"]


# --------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------


def test_prompt_carries_the_vacancy_the_rules_and_no_personal_data() -> None:
    client = client_for(HAPPY_PLAN)

    tailor_cv_document(make_doc(), make_job(), client)

    prompt, purpose = client.calls[1]
    assert purpose == "resume_tailoring"
    assert "Meridiaan Data" in prompt
    assert "We build Python and SQL data pipelines." in prompt
    assert "Never invent an employer" in prompt
    assert "Acme BV" in prompt
    # Details and contact sections stay out of the prompt entirely.
    assert "sam@example.com" not in prompt
    assert "12-05-1987" not in prompt


def test_plain_text_cv_is_offered_as_extra_context() -> None:
    client = client_for(HAPPY_PLAN)

    tailor_cv_document(make_doc(), make_job(), client, cv_text="PLAIN-TEXT-CV-MARKER")

    assert "PLAIN-TEXT-CV-MARKER" in client.calls[1][0]


def test_tailoring_survives_a_vacancy_without_a_description() -> None:
    client = client_for(HAPPY_PLAN)

    result = tailor_cv_document(make_doc(), make_job(description=None), client)

    assert "Data Engineer, Meridiaan Data, Utrecht" in client.calls[1][0]
    assert [section.id for section in result.main] == ["xp", "pf", "ed"]


def test_unusable_keywords_do_not_stop_the_tailoring() -> None:
    client = client_for(HAPPY_PLAN, keywords="I could not think of any.")

    result = tailor_cv_document(make_doc(), make_job(), client)

    assert "(none)" in client.calls[1][0]
    assert [section.id for section in result.main] == ["xp", "pf", "ed"]


# --------------------------------------------------------------------------
# Integrity: facts may not be rewritten or invented
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("organisation", "Globex Industries"),
        ("title", "Chief Technology Officer"),
        ("period", "2010 - 2019"),
    ],
)
def test_rewriting_a_frozen_fact_is_refused(field: str, value: str) -> None:
    response = plan(sections={"xp": {"entries": {"e1": {field: value}}}})

    with pytest.raises(TailorError, match="not editable"):
        tailor_cv_document(make_doc(), make_job(), client_for(response))


def test_echoing_frozen_facts_back_unchanged_is_accepted() -> None:
    response = plan(
        sections={
            "xp": {
                "entries": {
                    "e1": {
                        "title": "Field  Engineer",
                        "organisation": "acme bv",
                        "period": "2014 - 2019",
                        "description": "Installed and serviced laser systems.",
                    }
                }
            }
        }
    )

    result = tailor_cv_document(make_doc(), make_job(), client_for(response))

    entry = experience_of(result).entries[0]
    assert entry.organisation == "Acme BV"
    assert entry.title == "Field Engineer"
    assert entry.description == "Installed and serviced laser systems."


def test_an_entry_may_not_gain_a_bullet() -> None:
    response = plan(
        sections={
            "xp": {
                "entries": {
                    "e2": {
                        "bullets": [
                            "Shipped an ETL stack",
                            "Led a team of six engineers",
                        ]
                    }
                }
            }
        }
    )

    with pytest.raises(TailorError, match="never invented"):
        tailor_cv_document(make_doc(), make_job(), client_for(response))


def test_verify_integrity_rejects_a_gained_employer() -> None:
    original = make_doc()
    tailored = make_doc()
    experience_of(tailored).entries.append(
        ExperienceEntry(
            id="e3",
            title="Field Engineer",
            organisation="Globex Industries",
            period="2014 - 2019",
        )
    )

    with pytest.raises(TailorError, match="invented organisations"):
        _verify_integrity(original, tailored)


def test_verify_integrity_rejects_a_gained_qualification() -> None:
    original = make_doc()
    tailored = make_doc()
    education = tailored.main[2]
    assert isinstance(education, EducationSection)
    education.entries.append(
        EducationEntry(
            id="d3",
            degree="PhD Machine Learning",
            school="TU Delft",
            period="2008 - 2012",
        )
    )

    with pytest.raises(TailorError, match="invented titles"):
        _verify_integrity(original, tailored)


def test_verify_integrity_rejects_a_gained_skill() -> None:
    original = make_doc()
    tailored = make_doc()
    skills = tailored.sidebar[0]
    assert isinstance(skills, SkillsSection)
    skills.items.append(SkillItem(id="s4", name="Kubernetes"))

    with pytest.raises(TailorError, match="invented skills"):
        _verify_integrity(original, tailored)


def test_verify_integrity_rejects_a_gained_date_range() -> None:
    original = make_doc()
    tailored = make_doc()
    experience_of(tailored).entries[0].period = "2011 - 2019"

    with pytest.raises(TailorError, match="invented periods"):
        _verify_integrity(original, tailored)


def test_verify_integrity_allows_a_dropped_fact() -> None:
    original = make_doc()
    tailored = make_doc()
    experience_of(tailored).entries.pop()

    _verify_integrity(original, tailored)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("name", "identity block"),
        ("theme", "portrait or theme"),
        ("photo", "portrait or theme"),
        ("column", "sections live in"),
    ],
)
def test_verify_integrity_rejects_design_changes(change: str, message: str) -> None:
    original = make_doc()
    tailored = make_doc()
    if change == "name":
        tailored.full_name = "Someone Else"
    elif change == "theme":
        tailored.theme.accent = "#FF0000"
    elif change == "photo":
        tailored.photo = "other.png"
    else:
        tailored.sidebar.append(tailored.main.pop())

    with pytest.raises(TailorError, match=message):
        _verify_integrity(original, tailored)


def test_integrity_is_checked_after_the_plan_is_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def sneak_in_an_employer(doc: CVDocument, applied: TailorPlan) -> None:
        experience_of(doc).entries.append(
            ExperienceEntry(
                id="e9",
                title="Head of Data",
                organisation="Globex Industries",
                period="2025 - now",
            )
        )

    monkeypatch.setattr(tailor, "_apply_plan", sneak_in_an_employer)

    with pytest.raises(TailorError, match="invented organisations"):
        tailor_cv_document(make_doc(), make_job(), client_for(HAPPY_PLAN))


# --------------------------------------------------------------------------
# Malformed and truncated responses
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ("I am afraid I cannot help with that.", "no usable JSON"),
        ('{"main": ["xp", "pf"', "no usable JSON"),
        ("", "no usable JSON"),
        ('{"sections": {"xp": {"entries": {"e1": {"bullets": "one"}}}}}', "malformed"),
        ('{"sidebar": "everything"}', "malformed"),
        ("{}", "empty tailoring plan"),
        ('{"sidebar": [], "main": [], "sections": {}}', "empty tailoring plan"),
    ],
)
def test_broken_responses_raise_rather_than_half_tailor(
    response: str, message: str
) -> None:
    with pytest.raises(TailorError, match=message):
        tailor_cv_document(make_doc(), make_job(), client_for(response))


@pytest.mark.parametrize(
    "order",
    [
        ["xp", "pf"],  # a truncated ordering would drop a section
        ["xp", "pf", "ed", "zz"],  # an id this CV does not have
        ["xp", "xp", "pf", "ed"],  # a duplicate would clone a section
    ],
)
def test_an_ordering_that_is_not_a_reordering_is_refused(order: list[str]) -> None:
    with pytest.raises(TailorError, match="not a reordering"):
        tailor_cv_document(make_doc(), make_job(), client_for(plan(main=order)))


def test_an_invented_list_item_is_refused() -> None:
    response = plan(sections={"ls": {"order": ["Dutch", "English", "Klingon"]}})

    with pytest.raises(TailorError, match="not a reordering"):
        tailor_cv_document(make_doc(), make_job(), client_for(response))


def test_patching_an_unknown_section_is_refused() -> None:
    response = plan(sections={"nope": {"body": "hello"}})

    with pytest.raises(TailorError, match="not in this CV"):
        tailor_cv_document(make_doc(), make_job(), client_for(response))


def test_patching_an_unknown_entry_is_refused() -> None:
    response = plan(sections={"xp": {"entries": {"zz": {"description": "hi"}}}})

    with pytest.raises(TailorError, match="which is not in"):
        tailor_cv_document(make_doc(), make_job(), client_for(response))


# --------------------------------------------------------------------------
# Memories: extra evidence that may reword or add a bullet, never a fact
# --------------------------------------------------------------------------

MEMORIES = [
    {
        "label": "memory 3",
        "kind": "project",
        "text": "I moved the Beta NV reporting to dbt in 2023, halving its run time.",
        "noted_on": "2026-09-01",
        "hint": "Use for data engineering roles.",
        "tags": ["dbt", "pipelines"],
    },
    {
        "label": "memory 5",
        "kind": "achievement",
        "text": "I built a churn model in Python that cut churn by 12 percent.",
        "noted_on": "2026-09-01",
    },
    {
        "label": "memory 7",
        "kind": "preference",
        "text": "I want to work four days a week.",
        "noted_on": "2026-09-02",
    },
    {
        "label": "memory 9",
        "kind": "experience",
        "text": "I worked at Globex Industries as Head of Data in 2025.",
        "noted_on": "2026-09-03",
    },
]
MEMORY_BULLET = "Moved the reporting to dbt in 2023, halving its run time"
CHURN_BULLET = "Built a churn model in Python that cut churn by 12 percent"
E1_BULLETS = ["Trained customers", "Repaired optics"]
E2_BULLETS = ["Shipped an ETL stack"]


def bullets_plan(cited: object, bullets: list[str] | None = None) -> str:
    """A plan that gives entry e2 a new bullet, citing ``cited`` for it."""
    patch: dict[str, Any] = {
        "bullets": bullets or [*E2_BULLETS, MEMORY_BULLET],
        "memories": cited,
    }
    return plan(sections={"xp": {"entries": {"e2": patch}}})


def tailored_bullets(response: str) -> dict[str, list[str]]:
    """Tailor with the memories and return each entry's bullets by id."""
    result = tailor_cv_document(
        make_doc(), make_job(), client_for(response), memories=MEMORIES
    )
    return {entry.id: entry.bullets for entry in experience_of(result).entries}


def test_memories_reach_the_prompt_with_their_rules() -> None:
    client = client_for(HAPPY_PLAN)

    tailor_cv_document(make_doc(), make_job(), client, memories=MEMORIES)

    prompt = client.calls[1][0]
    assert MEMORIES[0]["text"] in prompt
    assert "memory 3" in prompt
    assert MEMORY_CV_RULE in prompt
    assert "only where its hint or tags fit" in prompt
    assert "one exception to rule 5" in prompt
    assert "after the entry's own bullets" in prompt
    assert_styled_prompt(prompt)


def test_without_memories_the_prompt_says_nothing_about_them() -> None:
    client = client_for(HAPPY_PLAN)

    tailor_cv_document(make_doc(), make_job(), client)

    assert "MEMORIES" not in client.calls[1][0]


def test_a_cited_memory_can_add_a_bullet_under_an_existing_role() -> None:
    response = bullets_plan(["Memory  3"])

    result = tailor_cv_document(
        make_doc(), make_job(), client_for(response), memories=MEMORIES
    )

    entry = experience_of(result).entries[1]
    assert entry.organisation == "Beta NV"
    assert entry.bullets == [*E2_BULLETS, MEMORY_BULLET]


def test_a_single_label_is_accepted_as_well_as_a_list() -> None:
    result = tailor_cv_document(
        make_doc(), make_job(), client_for(bullets_plan("memory 3")), memories=MEMORIES
    )

    assert MEMORY_BULLET in experience_of(result).entries[1].bullets


@pytest.mark.parametrize(
    "cited",
    ["Memory #3", "memory3", "herinnering 3", "herinnering nr. 3", 3, "3", [3]]
    + [[{"label": "memory 3"}]],
)
def test_a_label_is_read_in_any_spelling_the_interview_side_accepts(
    cited: object,
) -> None:
    """A citation spelt differently still backs its bullet."""
    assert tailored_bullets(bullets_plan(cited))["e2"] == [*E2_BULLETS, MEMORY_BULLET]


@pytest.mark.parametrize("cited", [3, [3], {"label": "memory 3"}, "rubbish", [None]])
def test_a_strange_memories_value_never_fails_the_plan(cited: object) -> None:
    """The field is advisory: a patch that only rewords still applies."""
    patch = {"description": "Reworded only.", "memories": cited}
    response = plan(sections={"xp": {"entries": {"e2": patch}}})

    result = tailor_cv_document(
        make_doc(), make_job(), client_for(response), memories=MEMORIES
    )

    assert experience_of(result).entries[1].description == "Reworded only."


def test_a_bullet_that_grows_without_naming_a_memory_is_refused() -> None:
    """Growing without a memory named breaks rule 5, as before memories."""
    with pytest.raises(TailorError, match="never invented"):
        tailor_cv_document(
            make_doc(), make_job(), client_for(bullets_plan([])), memories=MEMORIES
        )


def test_a_memory_cited_without_memories_in_the_prompt_adds_nothing() -> None:
    with pytest.raises(TailorError, match="never invented"):
        tailor_cv_document(
            make_doc(), make_job(), client_for(bullets_plan(["memory 3"]))
        )


@pytest.mark.parametrize(
    "cited",
    [
        ["memory 42"],  # a memory the prompt did not hold
        ["memory 7"],  # a wish, which never becomes a bullet
    ],
)
def test_a_label_without_a_usable_memory_adds_no_bullet(cited: list[str]) -> None:
    """The entry keeps its own bullets; the rest of the CV is still tailored."""
    assert tailored_bullets(bullets_plan(cited))["e2"] == E2_BULLETS


@pytest.mark.parametrize(
    "bullet",
    [
        # Nothing of the memory in it, an invented number and invented names.
        "Led a team of 40 engineers at NASA and won the Turing Award",
        # Some of the memory, plus a number it does not hold.
        "Moved the reporting to dbt in 2023, cutting its run time by 70 percent",
        # Some of the memory, plus a name it does not hold.
        "Moved the reporting to dbt in 2023 with Snowflake",
        # Only one word of it.
        "Mentored junior colleagues on dbt",
        # Its words, but the work put at another employer.
        "Moved the reporting to dbt in 2023 at Globex Industries",
    ],
)
def test_a_bullet_that_does_not_say_what_its_memory_says_is_not_added(
    bullet: str,
) -> None:
    """Naming a real memory is not enough: the bullet must state it."""
    response = bullets_plan(["memory 3"], [*E2_BULLETS, bullet])

    assert tailored_bullets(response)["e2"] == E2_BULLETS


def test_a_memory_about_another_employer_adds_no_bullet() -> None:
    """The Globex memory is the applicant's, but not a line under Beta NV."""
    bullet = "Head of Data at Globex Industries in 2025"
    response = bullets_plan(["memory 9"], [*E2_BULLETS, bullet])

    assert tailored_bullets(response)["e2"] == E2_BULLETS


def test_a_memory_that_names_one_employer_adds_no_bullet_under_another() -> None:
    """The dbt memory names Beta NV, so it has no place under Acme BV."""
    patch = {"bullets": [*E1_BULLETS, MEMORY_BULLET], "memories": ["memory 3"]}
    response = plan(sections={"xp": {"entries": {"e1": patch}}})

    assert tailored_bullets(response)["e1"] == E1_BULLETS


def test_a_memory_that_names_no_employer_may_go_under_any_role() -> None:
    patch = {"bullets": [*E1_BULLETS, CHURN_BULLET], "memories": ["memory 5"]}
    response = plan(sections={"xp": {"entries": {"e1": patch}}})

    assert tailored_bullets(response)["e1"] == [*E1_BULLETS, CHURN_BULLET]


def test_a_rejected_memory_bullet_leaves_the_rest_of_the_entry_tailored() -> None:
    """Only the bullets fall back; the reworded description stays."""
    patch = {
        "description": "Built Python and SQL reporting pipelines.",
        "bullets": [*E2_BULLETS, "Won the Turing Award"],
        "memories": ["memory 3"],
    }
    response = plan(sections={"xp": {"entries": {"e2": patch}}})

    result = tailor_cv_document(
        make_doc(), make_job(), client_for(response), memories=MEMORIES
    )

    entry = experience_of(result).entries[1]
    assert entry.bullets == E2_BULLETS
    assert entry.description == "Built Python and SQL reporting pipelines."


def test_one_memory_backs_one_new_bullet_only() -> None:
    three = [*E2_BULLETS, MEMORY_BULLET, "Moved reporting to dbt, halving run time"]
    response = bullets_plan(["memory 3", "memory 3"], three)

    assert tailored_bullets(response)["e2"] == E2_BULLETS


def test_one_memory_cannot_add_a_bullet_under_two_roles() -> None:
    response = plan(
        sections={
            "xp": {
                "entries": {
                    "e1": {
                        "bullets": [*E1_BULLETS, CHURN_BULLET],
                        "memories": ["memory 5"],
                    },
                    "e2": {
                        "bullets": [*E2_BULLETS, CHURN_BULLET],
                        "memories": ["memory 5"],
                    },
                }
            }
        }
    )

    bullets = tailored_bullets(response)

    assert bullets == {"e1": [*E1_BULLETS, CHURN_BULLET], "e2": E2_BULLETS}


def test_a_memory_named_by_an_entry_that_did_not_grow_is_still_free() -> None:
    """Only a new bullet uses a memory up, not a citation beside a reword."""
    response = plan(
        sections={
            "xp": {
                "entries": {
                    "e1": {"description": "Reworded.", "memories": ["memory 3"]},
                    "e2": {
                        "bullets": [*E2_BULLETS, MEMORY_BULLET],
                        "memories": ["memory 3"],
                    },
                }
            }
        }
    )

    assert tailored_bullets(response)["e2"] == [*E2_BULLETS, MEMORY_BULLET]


def test_two_memories_add_two_bullets_whatever_order_they_are_named_in() -> None:
    bullets = [*E2_BULLETS, MEMORY_BULLET, CHURN_BULLET]
    response = bullets_plan(["memory 5", "memory 3"], bullets)

    assert tailored_bullets(response)["e2"] == bullets


def test_a_memory_claiming_another_employer_cannot_rewrite_an_entry() -> None:
    patch = {"organisation": "Globex Industries", "memories": ["memory 9"]}
    response = plan(sections={"xp": {"entries": {"e2": patch}}})

    with pytest.raises(TailorError, match="not editable"):
        tailor_cv_document(
            make_doc(), make_job(), client_for(response), memories=MEMORIES
        )


def test_a_memory_claiming_a_skill_cannot_add_a_skill_item() -> None:
    response = plan(sections={"sk": {"order": ["s1", "s2", "s3", "s4"]}})

    with pytest.raises(TailorError, match="not a reordering"):
        tailor_cv_document(
            make_doc(), make_job(), client_for(response), memories=MEMORIES
        )


def test_the_integrity_check_still_runs_with_memories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def add_the_remembered_employer(doc: CVDocument, applied: TailorPlan) -> None:
        experience_of(doc).entries.append(
            ExperienceEntry(
                id="e9",
                title="Head of Data",
                organisation="Globex Industries",
                period="2025 - now",
            )
        )

    monkeypatch.setattr(tailor, "_apply_plan", add_the_remembered_employer)

    with pytest.raises(TailorError, match="invented organisations"):
        tailor_cv_document(
            make_doc(), make_job(), client_for(HAPPY_PLAN), memories=MEMORIES
        )


# --------------------------------------------------------------------------
# Slugs
# --------------------------------------------------------------------------


def test_slug_limit_matches_the_storage_layer() -> None:
    assert len(normalise_slug("a" * 200)) == SLUG_LIMIT


@pytest.mark.parametrize(
    ("company", "expected"),
    [
        ("Meridiaan Data", "default-meridiaan-data"),
        ("Café Groen & Zoon", "default-cafe-groen-zoon"),
        ("ASML", "default-asml"),
    ],
)
def test_tailored_slug_appends_the_company(company: str, expected: str) -> None:
    assert tailored_slug("default", make_job(company=company)) == expected


def test_tailored_slug_is_deterministic() -> None:
    job = make_job()
    assert tailored_slug("default", job) == tailored_slug("default", job)


def test_tailored_slug_never_returns_the_base_slug() -> None:
    base = "a" * 70

    slug = tailored_slug(base, make_job())

    assert slug != normalise_slug(base)
    assert len(slug) <= SLUG_LIMIT
    assert slug.endswith("-meridiaan-data")


def test_a_long_company_name_stays_within_the_limit() -> None:
    company = "Stichting Nederlandse Organisatie voor Toegepast Onderzoek Delft"

    slug = tailored_slug("default", make_job(company=company))

    assert len(slug) <= SLUG_LIMIT
    assert slug.startswith("default-stichting-nederlandse-o")


def test_long_company_names_sharing_an_opening_do_not_collide() -> None:
    shared = "Nederlandse Organisatie voor Toegepast Onderzoek"

    first = tailored_slug("default", make_job(company=f"{shared} Delft"))
    second = tailored_slug("default", make_job(company=f"{shared} Eindhoven"))

    assert first != second
    assert len(first) <= SLUG_LIMIT
    assert len(second) <= SLUG_LIMIT


def test_a_nameless_company_falls_back_to_the_job_title() -> None:
    slug = tailored_slug("default", make_job(company="", title="Data Engineer"))

    assert slug == "default-data-engineer"


def test_a_vacancy_with_no_usable_words_still_yields_a_slug() -> None:
    slug = tailored_slug("default", make_job(company="", title="!!!"))

    assert slug == "default-vacancy"


def test_an_unusable_base_slug_is_rejected() -> None:
    with pytest.raises(StorageError):
        tailored_slug("...", make_job())
