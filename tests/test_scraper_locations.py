"""LinkedIn area labels are recovered, so a real vacancy is not dropped as placeless.

jobspy parses "City, State, Country" and returns nothing for the area labels
LinkedIn uses on part of its vacancies ("Amsterdam Area", "Rotterdam and The
Hague"). Those listings reached the commute filter with no location at all and
were rejected before anything scored them.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from job_scout.models import JobListing
from job_scout.scraper import normalise_area, recover_missing_locations

PAGE = (
    '<h4 class="topcard__flavor-row">'
    '<span class="topcard__flavor topcard__flavor--bullet">  {label}  </span>'
    '<span class="topcard__flavor topcard__flavor--bullet">27 applicants</span>'
    "</h4>"
)


def job(
    title: str = "Test Engineer",
    location: str | None = None,
    source: str = "linkedin",
    url: str = "https://www.linkedin.com/jobs/view/1",
) -> JobListing:
    """Build a scraped listing."""
    return JobListing(
        title=title,
        company="Example B.V.",
        location=location,
        url=url,
        source=source,
        description="",
        seen_at=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    ("label", "place"),
    [
        ("Amsterdam Area", "Amsterdam"),
        ("Greater Enschede Area", "Enschede"),
        ("  Eindhoven   Area ", "Eindhoven"),
        ("Rotterdam and The Hague", "Rotterdam"),
        ("Utrecht & Amersfoort", "Utrecht"),
        ("Amsterdam/Haarlem", "Amsterdam"),
        ("Netherlands", "Netherlands"),
        (
            "Amsterdam, North Holland, Netherlands",
            "Amsterdam, North Holland, Netherlands",
        ),
        ("s-Hertogenbosch Area", "s-Hertogenbosch"),
        ("", ""),
    ],
)
def test_an_area_label_becomes_a_place(label: str, place: str) -> None:
    assert normalise_area(label) == place


def test_a_listing_without_a_location_gets_the_one_from_its_page() -> None:
    listings = [job(title="Test Engineer")]

    recovered = recover_missing_locations(
        listings, fetch=lambda _url: PAGE.format(label="Eindhoven Area")
    )

    assert recovered == 1
    assert listings[0].location == "Eindhoven"


def test_a_nationwide_vacancy_keeps_its_country() -> None:
    """A vacancy for the whole country is location independent, not placeless."""
    listings = [job()]

    recover_missing_locations(
        listings, fetch=lambda _url: PAGE.format(label="Netherlands")
    )

    assert listings[0].location == "Netherlands"


def test_the_applicant_count_is_not_a_location() -> None:
    page = (
        '<span class="topcard__flavor topcard__flavor--bullet">27 applicants</span>'
        '<span class="topcard__flavor topcard__flavor--bullet">Delft</span>'
    )
    listings = [job()]

    recover_missing_locations(listings, fetch=lambda _url: page)

    assert listings[0].location == "Delft"


def test_listings_that_already_have_a_location_are_left_alone() -> None:
    asked: list[str] = []

    def fetch(url: str) -> str:
        asked.append(url)
        return PAGE.format(label="Amsterdam Area")

    listings = [job(location="Utrecht, Netherlands"), job(source="indeed")]

    assert recover_missing_locations(listings, fetch=fetch) == 0
    assert asked == []
    assert listings[0].location == "Utrecht, Netherlands"
    assert listings[1].location is None


def test_a_page_that_cannot_be_read_leaves_the_listing_as_it_was() -> None:
    listings = [job()]

    assert recover_missing_locations(listings, fetch=lambda _url: "") == 0
    assert listings[0].location is None


def test_the_number_of_lookups_is_bounded() -> None:
    asked: list[str] = []

    def fetch(url: str) -> str:
        asked.append(url)
        return PAGE.format(label="Breda Area")

    listings = [job(url=f"https://www.linkedin.com/jobs/view/{n}") for n in range(8)]

    recovered = recover_missing_locations(listings, fetch=fetch, limit=3)

    assert recovered == 3
    assert len(asked) == 3
    assert [item.location for item in listings[3:]] == [None] * 5


def test_the_run_looks_up_the_location_of_new_listings_only(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The lookup is capped, so it must not be spent on jobs already stored."""
    from unittest.mock import patch

    from click.testing import CliRunner

    import job_scout.config as config
    from job_scout.cli import cli
    from job_scout.database import Database

    config.DATA_DIR = tmp_path
    config.CONFIG_PATH = tmp_path / "config.yaml"
    config.write_global_config({"llm_provider": "local"})
    config.save_user_config("Alex", {"profile_description": "CRO specialist"})
    known = job(title="Already stored", url="https://www.linkedin.com/jobs/view/9")
    Database(config.user_db_path("Alex")).save_job(known)
    fresh = job(title="Brand new", url="https://www.linkedin.com/jobs/view/10")
    asked: list[list[str]] = []

    def spy(jobs: list[JobListing], **kwargs: object) -> int:
        asked.append([item.title for item in jobs])
        return 0

    with (
        patch("job_scout.cli.check_llm_available", return_value=(True, None)),
        patch("job_scout.cli.scrape_all_jobs", return_value=[known, fresh]),
        patch("job_scout.cli.recover_missing_locations", spy),
        patch("job_scout.cli.screen_job_titles", return_value=([], 1)),
    ):
        result = CliRunner().invoke(cli, ["run", "--dry-run", "--user", "Alex"])

    assert result.exit_code == 0, result.output
    assert asked == [["Brand new"]]
