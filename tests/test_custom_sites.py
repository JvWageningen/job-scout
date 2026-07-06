"""Tests for custom-site scraping: HTML extraction, LLM parsing, CLI commands."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner

from job_scout.llm.base import LLMClient
from job_scout.models import Config, CustomSite

# ---------------------------------------------------------------------------
# _html_to_text_and_links
# ---------------------------------------------------------------------------


def test_html_to_text_extracts_visible_text() -> None:
    """_html_to_text_and_links strips tags and returns visible text."""
    from job_scout.scraper import _html_to_text_and_links

    html = "<html><body><h1>Hello</h1><p>World</p></body></html>"
    text, _ = _html_to_text_and_links(html)
    assert "Hello" in text
    assert "World" in text


def test_html_to_text_skips_script_and_style() -> None:
    """Script and style content is excluded from extracted text."""
    from job_scout.scraper import _html_to_text_and_links

    html = (
        "<html><head><style>body{}</style></head>"
        "<body><script>alert(1)</script><p>Visible</p></body></html>"
    )
    text, _ = _html_to_text_and_links(html)
    assert "alert" not in text
    assert "body{}" not in text
    assert "Visible" in text


def test_html_to_text_collects_links() -> None:
    """_html_to_text_and_links returns href values from anchor tags."""
    from job_scout.scraper import _html_to_text_and_links

    html = '<a href="/jobs/1">Job</a><a href="https://example.com/job/2">Job2</a>'
    _, links = _html_to_text_and_links(html)
    assert "/jobs/1" in links
    assert "https://example.com/job/2" in links


# ---------------------------------------------------------------------------
# _scrape_custom_site
# ---------------------------------------------------------------------------


def _make_llm(response: str) -> LLMClient:
    mock = MagicMock(spec=LLMClient)
    mock.complete.return_value = response
    return mock


def _mock_response(text: str = "", status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.status_code = status_code
    if status_code >= 400:
        import requests

        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    else:
        resp.raise_for_status.return_value = None
    return resp


def test_scrape_custom_site_returns_jobs() -> None:
    """_scrape_custom_site maps LLM extraction response to JobListings."""
    from job_scout.scraper import _scrape_custom_site

    site = CustomSite(name="acme", url="https://acme.example.com/careers")
    html = '<html><body><a href="/jobs/eng">Engineer</a></body></html>'
    llm_response = json.dumps(
        {
            "jobs": [
                {
                    "title": "Engineer",
                    "company": "Acme",
                    "location": "Amsterdam",
                    "url": "/jobs/eng",
                    "description": "Great role",
                }
            ]
        }
    )
    client = _make_llm(llm_response)
    config = Config()
    with patch("requests.get", return_value=_mock_response(html)):
        jobs = _scrape_custom_site(site, config, client)

    assert len(jobs) == 1
    assert jobs[0].title == "Engineer"
    assert jobs[0].source == "acme"
    assert jobs[0].url.startswith("https://acme.example.com")


def test_scrape_custom_site_returns_empty_on_fetch_error() -> None:
    """Returns [] and logs warning when the site is unreachable."""
    from job_scout.scraper import _scrape_custom_site

    site = CustomSite(name="broken", url="https://broken.example.com/jobs")
    client = _make_llm("{}")
    with patch("requests.get", return_value=_mock_response(status_code=503)):
        jobs = _scrape_custom_site(site, Config(), client)
    assert jobs == []


def test_scrape_custom_site_returns_empty_on_llm_failure() -> None:
    """Returns [] when LLM returns unparseable output."""
    from job_scout.scraper import _scrape_custom_site

    site = CustomSite(name="bad-llm", url="https://example.com/jobs")
    client = _make_llm("NOT JSON AT ALL")
    html = "<html><body>jobs</body></html>"
    with patch("requests.get", return_value=_mock_response(html)):
        jobs = _scrape_custom_site(site, Config(), client)
    assert jobs == []


def test_scrape_custom_site_skips_disabled() -> None:
    """Disabled sites are skipped inside scrape_all_jobs."""
    from job_scout.scraper import scrape_all_jobs

    site = CustomSite(name="off", url="https://example.com/jobs", enabled=False)
    config = Config(custom_sites=[site], keywords_dutch=[], keywords_english=[])
    client = _make_llm("{}")

    with (
        patch("job_scout.scraper._scrape_jobspy", return_value=[]),
        patch("job_scout.scraper._scrape_nvb", return_value=[]),
    ):
        jobs = scrape_all_jobs(config, client)

    assert client.complete.call_count == 0
    assert jobs == []


# ---------------------------------------------------------------------------
# CLI: sites add / list / remove
# ---------------------------------------------------------------------------


def _reload_config_module(monkeypatch: pytest.MonkeyPatch, data_dir: Path):
    monkeypatch.setenv("JOB_SCOUT_DATA_DIR", str(data_dir))
    import job_scout.config as cfg

    importlib.reload(cfg)
    return cfg


def test_sites_add_list_remove_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sites add → sites list → sites remove round-trip."""
    from job_scout.cli import cli

    _reload_config_module(monkeypatch, tmp_path)
    user_dir = tmp_path / "users" / "alice"
    user_dir.mkdir(parents=True)
    (user_dir / "config.yaml").write_text(
        yaml.dump({"ntfy_topic": "alice-topic"}), encoding="utf-8"
    )

    runner = CliRunner()

    # Add
    result = runner.invoke(
        cli,
        [
            "sites",
            "add",
            "https://acme.example.com/careers",
            "--name",
            "Acme",
            "--user",
            "alice",
        ],
    )
    assert result.exit_code == 0, result.output

    # List
    result = runner.invoke(cli, ["sites", "list", "--user", "alice"])
    assert result.exit_code == 0
    assert "Acme" in result.output
    assert "acme.example.com" in result.output

    # Remove
    result = runner.invoke(cli, ["sites", "remove", "Acme", "--user", "alice"])
    assert result.exit_code == 0

    # List again → empty
    result = runner.invoke(cli, ["sites", "list", "--user", "alice"])
    assert result.exit_code == 0
    assert "Acme" not in result.output


def test_sites_add_default_name_is_hostname(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sites add without --name uses the URL hostname as label."""
    from job_scout.cli import cli

    _reload_config_module(monkeypatch, tmp_path)
    user_dir = tmp_path / "users" / "alice"
    user_dir.mkdir(parents=True)
    (user_dir / "config.yaml").write_text("", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["sites", "add", "https://careers.example.com/jobs", "--user", "alice"],
    )
    assert result.exit_code == 0, result.output

    # Config should contain the hostname as name
    data = yaml.safe_load((user_dir / "config.yaml").read_text())
    sites = data.get("custom_sites", [])
    assert len(sites) == 1
    assert sites[0]["name"] == "careers.example.com"
