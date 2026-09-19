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
