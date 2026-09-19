"""Holding a memory's new CV bullet against the memory it names."""

from __future__ import annotations

import pytest

from job_scout.cv.memory_bullets import MemoryBullets
from job_scout.cv.models import (
    CVDocument,
    EducationEntry,
    EducationSection,
    ExperienceEntry,
    ExperienceSection,
)

WEBSHOP = ExperienceEntry(
    id="e1",
    title="Online marketeer",
    organisation="Tuinhuis Noord",
    period="2019 - 2025",
    bullets=["Beheerde de webshop"],
)
AGENCY = ExperienceEntry(
    id="e2",
    title="Stagiair",
    organisation="Bureau Kalibra",
    period="2018 - 2019",
)


def cv() -> CVDocument:
    """A Dutch CV with two employers and a school."""
    return CVDocument(
        full_name="Sam de Vries",
        language="NL",
        main=[
            ExperienceSection(id="xp", entries=[WEBSHOP, AGENCY]),
            EducationSection(
                id="ed",
                entries=[EducationEntry(id="d1", degree="BSc", school="Hanze")],
            ),
        ],
    )


def memory(text: str, label: str = "memory 4", kind: str = "project") -> dict:
    """One memory as the prompt holds it."""
    return {"label": label, "kind": kind, "text": text, "noted_on": "2026-09-01"}


def back(text: str, bullet: str, entry: ExperienceEntry = WEBSHOP) -> list[str] | None:
    """Ask whether one memory backs one new bullet under an entry."""
    return MemoryBullets([memory(text)], cv()).back(entry, [bullet], ["memory 4"])


def test_a_translated_bullet_keeps_to_its_memory() -> None:
    """An English memory may become a Dutch bullet: names and numbers carry it."""
    text = "At Tuinhuis Noord I set up forty A/B tests with Optimizely in 2023."
    bullet = "Zette in 2023 veertig A/B-tests op met Optimizely"

    assert back(text, bullet) == ["memory 4"]


@pytest.mark.parametrize(
    "text",
    [
        "Ik werkte als data-analist bij Globex en bouwde daar dashboards.",
        "I worked for Globex Industries on their dashboards.",
        "As an analyst at Globex I built dashboards.",
        "Bij Bureau Kalibra bouwde ik de dashboards.",  # another employer on the CV
        "Tijdens mijn studie aan de Hanze bouwde ik dashboards.",  # the school
    ],
)
def test_a_memory_that_belongs_elsewhere_backs_no_bullet(text: str) -> None:
    assert back(text, "Bouwde dashboards") is None


@pytest.mark.parametrize(
    ("text", "bullet"),
    [
        ("Ik bouwde dashboards voor de afdeling Finance.", "Bouwde dashboards"),
        (
            "I did the reporting work for Finance and built dashboards.",
            "Built dashboards for Finance",
        ),
        (
            "Bij Tuinhuis Noord bouwde ik de dashboards die ik eerder bij Bureau "
            "Kalibra ontwierp.",
            "Bouwde de dashboards",
        ),
    ],
)
def test_a_memory_about_this_work_backs_a_bullet(text: str, bullet: str) -> None:
    """A department is no employer, and naming the entry's own ties it there."""
    assert back(text, bullet) == ["memory 4"]


def test_a_used_memory_backs_nothing_more_and_a_refusal_uses_nothing() -> None:
    bullets = MemoryBullets([memory("Ik bouwde dashboards met Looker.")], cv())

    assert (
        bullets.back(WEBSHOP, ["Bouwde dashboards met Tableau"], ["memory 4"]) is None
    )
    assert bullets.back(WEBSHOP, ["Bouwde dashboards met Looker"], ["memory 4"]) == [
        "memory 4"
    ]
    assert bullets.back(AGENCY, ["Bouwde dashboards met Looker"], ["memory 4"]) is None


def test_a_wish_backs_no_bullet() -> None:
    wish = memory("Ik wil vier dagen per week werken.", kind="preference")

    bullets = MemoryBullets([wish], cv())

    assert bullets.back(WEBSHOP, ["Werkt vier dagen per week"], ["memory 4"]) is None


GLOBEX = ExperienceEntry(
    id="g", title="Data analyst", organisation="Globex", period="2021 - 2025"
)
INITECH = ExperienceEntry(
    id="i", title="Analyst", organisation="Initech", period="2018 - 2021"
)
CONSULTANCY = ExperienceEntry(
    id="c", title="Consultant", organisation="Accenture", period="2015 - now"
)


def other_cv() -> CVDocument:
    """An English CV with two employers and a consultancy."""
    return CVDocument(
        full_name="Sam de Vries",
        main=[ExperienceSection(id="xp", entries=[GLOBEX, INITECH, CONSULTANCY])],
    )


def backs(entry: ExperienceEntry, text: str, bullet: str) -> list[str] | None:
    """Ask whether one memory backs one new bullet under an entry of other_cv."""
    ledger = MemoryBullets([memory(text)], other_cv())
    return ledger.back(entry, [bullet], ["memory 4"])


@pytest.mark.parametrize(
    ("entry", "text", "bullet"),
    [
        (
            GLOBEX,
            "I did freelance data work for Bakkerij Bol in 2020, building their "
            "stock forecast.",
            "Built the stock forecast for Bakkerij Bol as a freelance data job in 2020",
        ),
        (
            GLOBEX,
            "I was a consultant for Hooli in 2019 and built a pricing model.",
            "Built a pricing model for Hooli as a consultant in 2019",
        ),
        (
            INITECH,
            "Ik deed in 2020 een freelance opdracht voor Bakkerij Bol en maakte een "
            "voorraadprognose.",
            "Maakte in 2020 als freelancer een voorraadprognose voor Bakkerij Bol",
        ),
        (
            GLOBEX,
            "I did freelance data work in 2020, building a stock forecast.",
            "Built a stock forecast as freelance data work in 2020",
        ),
        (
            INITECH,
            "I hold the Snowflake SnowPro Core certification since March 2024.",
            "Earned the Snowflake SnowPro Core certification in March 2024",
        ),
    ],
)
def test_work_for_another_organisation_or_outside_the_period_backs_nothing(
    entry: ExperienceEntry, text: str, bullet: str
) -> None:
    """A client, a side job or a later year is not work at this employer."""
    assert backs(entry, text, bullet) is None


@pytest.mark.parametrize(
    ("entry", "text", "bullet"),
    [
        (
            GLOBEX,
            "I did the reporting work for Marketing in 2022 and built dashboards.",
            "Built dashboards for Marketing in 2022",
        ),
        (
            GLOBEX,
            "At Globex I served 2000 customers with a new portal.",
            "Served 2000 customers with a new portal",
        ),
        (
            CONSULTANCY,
            "For client Ahold I built a demand forecast.",
            "Built a demand forecast for client Ahold",
        ),
    ],
)
def test_a_department_a_count_or_a_consultancys_client_still_backs(
    entry: ExperienceEntry, text: str, bullet: str
) -> None:
    """A department is no employer, 2000 is no year, a client is the work."""
    assert backs(entry, text, bullet) == ["memory 4"]


MIGRATION = "At Globex I migrated 120 SQL reports to dbt on Snowflake in 2023."


@pytest.mark.parametrize(
    "bullet",
    [
        "Led a team of five engineers migrating 120 SQL reports to dbt on Snowflake",
        "Migrated 120 SQL reports to dbt on Snowflake in 2021",
        "Migrated two hundred SQL reports to dbt on Snowflake",
        "Migrated 120 SQL reports to dbt, cutting costs by half",
    ],
)
def test_a_number_the_memory_does_not_hold_is_refused(bullet: str) -> None:
    """In digits or words, and a year from the period does not replace the memory's."""
    assert backs(GLOBEX, MIGRATION, bullet) is None


@pytest.mark.parametrize(
    "bullet",
    [
        "Migrated 120 SQL reports to dbt on Snowflake in 2023",
        "Migrated 120 SQL reports to dbt",
        "Migrated one hundred twenty SQL reports to dbt",
    ],
)
def test_a_faithful_bullet_keeps_its_memory(bullet: str) -> None:
    """The full and the shortened wording, and the number as words."""
    assert backs(GLOBEX, MIGRATION, bullet) == ["memory 4"]


def test_a_dutch_compound_number_is_read_as_a_number() -> None:
    """ "vijfentwintig" is 25, which the memory does not say."""
    text = "Bij Globex migreerde ik 120 SQL rapporten naar dbt."
    assert backs(GLOBEX, text, "Migreerde vijfentwintig SQL rapporten naar dbt") is None
    assert backs(GLOBEX, text, "Migreerde honderdtwintig SQL rapporten naar dbt") == [
        "memory 4"
    ]


@pytest.mark.parametrize(
    "bullet",
    [
        "Bracht in 2023 de maandafsluiting terug van 5 naar 2 dagen",
        "Cut the monthly close from five to two days in 2023",
        "Cut the monthly close from 5 to 2 days in 2023",
    ],
)
def test_numbers_written_as_words_meet_their_digits(bullet: str) -> None:
    """ "vijf naar twee" is "5 to 2" and "five to two", in either language."""
    text = (
        "Bij Tuinhuis Noord bracht ik in 2023 de maandafsluiting terug van vijf "
        "naar twee dagen."
    )
    assert back(text, bullet) == ["memory 4"]


def test_a_translation_without_shared_words_is_left_to_the_judge() -> None:
    """Code refuses it by itself; the caller asks the model, and a yes counts."""
    text = (
        "Ik heb bij Tuinhuis Noord het onboardingproces voor nieuwe analisten "
        "opgezet en begeleid."
    )
    bullet = "Set up and ran the onboarding process for new analysts"
    ledger = MemoryBullets([memory(text)], cv())

    [pair] = ledger.pending(WEBSHOP, [bullet], ["memory 4"])
    assert (pair.bullet, pair.label, pair.memory) == (bullet, "memory 4", text)
    assert ledger.back(WEBSHOP, [bullet], ["memory 4"]) is None
    ledger.accept([pair])
    assert ledger.back(WEBSHOP, [bullet], ["memory 4"]) == ["memory 4"]


def test_a_bullet_that_fails_a_rule_is_never_put_to_the_judge() -> None:
    """A number, a name or another employer: no judge can let it through."""
    text = "Ik heb bij Tuinhuis Noord het onboardingproces opgezet."
    ledger = MemoryBullets([memory(text)], cv())

    for bullet in ("Onboarded 12 analysts", "Set up onboarding with Workday"):
        assert ledger.pending(WEBSHOP, [bullet], ["memory 4"]) == []
