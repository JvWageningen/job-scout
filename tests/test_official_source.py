"""Tests for web search parsing and official-source selection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from job_scout.official_source import (
    OfficialSource,
    _is_job_board,
    _score_result,
    _select_official,
    find_official_source,
)
from job_scout.pruner import PruneCheck, PruneOutcome
from job_scout.websearch import SearchResult, _parse_results, web_search

_DDG_HTML = """
<div class="result">
  <a class="result__a" href="https://werkenbijpraxis.nl/vacatures/cro">CRO - Praxis</a>
  <a class="result__snippet">Jouw rol als CRO Specialist bij Praxis.</a>
</div>
<div class="result">
  <a class="result__a" href="https://nl.linkedin.com/jobs/view/123">CRO at Praxis</a>
  <a class="result__snippet">LinkedIn listing.</a>
</div>
<div class="result">
  <a class="result__a" href="https://duckduckgo.com/y.js?ad_domain=x">Ad</a>
</div>
"""


def test_parse_results_extracts_organic_and_skips_ads() -> None:
    """Organic results are parsed with snippets; ad/DDG links are skipped."""
    results = _parse_results(_DDG_HTML, max_results=8)
    urls = [r.url for r in results]
    assert "https://werkenbijpraxis.nl/vacatures/cro" in urls
    assert "https://nl.linkedin.com/jobs/view/123" in urls
    assert all("duckduckgo.com" not in u for u in urls)
    assert results[0].snippet.startswith("Jouw rol")


def test_web_search_retries_then_returns_empty_when_blocked() -> None:
    """When the endpoint yields nothing, web_search backs off and returns []."""
    with (
        patch("job_scout.websearch._fetch", return_value="") as fetch,
        patch("job_scout.websearch._sleep"),
    ):
        assert web_search("anything") == []
    assert fetch.call_count == 3  # retried up to _MAX_ATTEMPTS


def test_web_search_uses_brave_when_api_key_present() -> None:
    """With an API key, web_search queries Brave and parses its JSON."""
    resp = MagicMock()
    resp.json.return_value = {
        "web": {"results": [{"url": "https://x.nl", "title": "X", "description": "s"}]}
    }
    with patch("job_scout.websearch.requests.get", return_value=resp) as get:
        results = web_search("q", api_key="brave-key")
    assert [r.url for r in results] == ["https://x.nl"]
    assert get.call_args.kwargs["headers"]["X-Subscription-Token"] == "brave-key"


def test_web_search_falls_back_to_ddg_when_brave_empty() -> None:
    """A Brave miss falls through to the keyless DuckDuckGo path."""
    results = [SearchResult(url="https://ddg.nl", title="d")]
    with (
        patch("job_scout.websearch._brave_search", return_value=[]),
        patch("job_scout.websearch._fetch", return_value="<html></html>"),
        patch("job_scout.websearch._parse_results", return_value=results),
    ):
        out = web_search("q", api_key="brave-key")
    assert out == results


def test_is_job_board() -> None:
    """Known aggregators are recognised; employer domains are not."""
    assert _is_job_board("nl.linkedin.com")
    assert _is_job_board("indeed.com")
    assert not _is_job_board("werkenbijpraxis.nl")
    assert not _is_job_board("extreme-cashmere.homerun.co")


def test_score_prefers_company_domain_then_ats() -> None:
    """Company-in-domain scores highest; ATS next; job boards score zero."""
    company_page = SearchResult(url="https://werkenbijpraxis.nl/vac/x", title="")
    ats_page = SearchResult(url="https://acme.homerun.co/job", title="")
    board = SearchResult(url="https://nl.linkedin.com/jobs/view/1", title="")
    assert _score_result(company_page, "praxis") > _score_result(ats_page, "praxis")
    assert _score_result(ats_page, "praxis") > 0
    assert _score_result(board, "praxis") == 0


def test_select_official_picks_employer_over_board() -> None:
    """_select_official skips boards and returns the employer page."""
    results = [
        SearchResult(url="https://nl.linkedin.com/jobs/view/1", title="board"),
        SearchResult(url="https://werkenbijpraxis.nl/vac/cro", title="official"),
    ]
    chosen = _select_official(results, "Praxis")
    assert chosen is not None
    assert "werkenbijpraxis.nl" in chosen.url


def test_select_official_none_when_only_boards() -> None:
    """No official page → None (feature degrades gracefully)."""
    results = [SearchResult(url="https://indeed.com/x", title="board")]
    assert _select_official(results, "Praxis") is None


def test_find_official_source_reports_availability() -> None:
    """find_official_source returns the employer URL and availability."""
    results = [SearchResult(url="https://werkenbijpraxis.nl/vac/cro", title="CRO")]
    with (
        patch("job_scout.official_source.web_search", return_value=results),
        patch(
            "job_scout.official_source.check_vacancy_open",
            return_value=PruneCheck(outcome=PruneOutcome.OPEN, reason="open"),
        ),
    ):
        source = find_official_source("CRO Specialist", "Praxis")
    assert isinstance(source, OfficialSource)
    assert source.url == "https://werkenbijpraxis.nl/vac/cro"
    assert source.available is True


def test_find_official_source_none_when_no_results() -> None:
    """No search results → empty OfficialSource, availability unknown."""
    with patch("job_scout.official_source.web_search", return_value=[]):
        source = find_official_source("CRO Specialist", "Obscure Co")
    assert source.url is None
    assert source.available is None
