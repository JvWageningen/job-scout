"""Tests for public web-search-based person profile enrichment."""

from __future__ import annotations

import json
from unittest.mock import patch

from job_scout.models import CvRole, PersonSearchResult
from job_scout.person_search import (
    _as_roles,
    _as_str_list,
    _build_person_prompt,
    _evidence_queries,
    _opt_str,
    gather_person_evidence,
    search_person,
)
from job_scout.websearch import SearchResult


class FakeLLMClient:
    """Minimal LLM client stub returning canned responses."""

    def __init__(self, responses: list[str], repeat_last: bool = True) -> None:
        """Initialise the stub.

        Args:
            responses: Responses to return, in order.
            repeat_last: Whether to repeat the last response once exhausted.
        """
        self.responses = responses
        self.repeat_last = repeat_last
        self.calls: list[tuple[str, str]] = []

    def complete(self, prompt: str, purpose: str = "") -> str:
        """Return the next canned response, or raise when exhausted.

        Args:
            prompt: The prompt (recorded for assertions).
            purpose: The routing purpose (recorded for assertions).

        Returns:
            The next canned response.

        Raises:
            LLMError: When no response is available.
        """
        from job_scout.llm.base import LLMError

        self.calls.append((prompt, purpose))
        if not self.responses:
            raise LLMError("no response")
        if len(self.calls) <= len(self.responses):
            return self.responses[len(self.calls) - 1]
        if self.repeat_last:
            return self.responses[-1]
        raise LLMError("exhausted")


def test_evidence_queries_include_context_when_given() -> None:
    """A known_context appends an extra disambiguating query."""
    without = _evidence_queries("Jane Doe", None)
    with_ctx = _evidence_queries("Jane Doe", "Acme Corp")
    assert len(with_ctx) == len(without) + 1
    assert any("Acme Corp" in q for q in with_ctx)


def test_gather_person_evidence_dedupes_repeated_snippets() -> None:
    """Identical snippets across queries are only recorded once."""
    results = [SearchResult(url="https://a.nl", title="A", snippet="Same snippet")]
    with patch("job_scout.person_search.web_search", return_value=results):
        snippets, sources = gather_person_evidence("Jane Doe")
    assert snippets == ["A — Same snippet"]
    assert sources == ["https://a.nl"]


def test_build_person_prompt_includes_disambiguation_rules() -> None:
    """The prompt tells the LLM not to guess across same-name collisions."""
    prompt = _build_person_prompt("Jane Doe", "- some evidence", "Acme Corp")
    assert "Jane Doe" in prompt
    assert "Acme Corp" in prompt
    assert "different person" in prompt.lower()


def test_search_person_empty_name_skips_llm_and_search() -> None:
    """An empty name short-circuits without calling the LLM."""
    client = FakeLLMClient([])
    result = search_person("", client=client)
    assert result.full_name == ""
    assert result.notes == "No name given."
    assert client.calls == []


def test_search_person_parses_llm_response() -> None:
    """A well-formed LLM response is parsed into a PersonSearchResult."""
    response = json.dumps(
        {
            "skills": ["Python", "SQL"],
            "education": ["TU Delft (2018-2022)"],
            "past_roles": [
                {
                    "title": "Data Engineer",
                    "company": "Acme",
                    "start_date": "2022",
                    "end_date": None,
                    "description": None,
                }
            ],
            "summary": "Data engineer at Acme.",
            "confidence": "medium",
            "notes": "",
        }
    )
    client = FakeLLMClient([response])
    with patch(
        "job_scout.person_search.web_search",
        return_value=[SearchResult(url="https://a.nl", title="A", snippet="s")],
    ):
        result = search_person("Jane Doe", known_context="Acme", client=client)

    assert result.full_name == "Jane Doe"
    assert "Python" in result.skills
    assert result.education == ["TU Delft (2018-2022)"]
    assert len(result.past_roles) == 1
    assert result.past_roles[0].title == "Data Engineer"
    assert result.confidence == "medium"
    assert result.sources == ["https://a.nl"]
    assert client.calls[0][1] == "evaluation"


def test_search_person_handles_llm_error() -> None:
    """A failed LLM call yields an empty, low-confidence result, not a crash."""
    with patch("job_scout.person_search.web_search", return_value=[]):
        client = FakeLLMClient([], repeat_last=False)
        result = search_person("Jane Doe", client=client)
    assert result.skills == []
    assert result.past_roles == []
    assert result.confidence == "low"


def test_search_person_handles_malformed_json() -> None:
    """A non-JSON LLM response degrades gracefully instead of raising."""
    client = FakeLLMClient(["this is not json"])
    with patch("job_scout.person_search.web_search", return_value=[]):
        result = search_person("Jane Doe", client=client)
    assert result.skills == []
    assert result.confidence == "low"


def test_as_str_list_rejects_non_list_and_blanks() -> None:
    """_as_str_list returns [] for non-lists and drops blank entries."""
    assert _as_str_list("not a list") == []
    assert _as_str_list([" ", "Python", ""]) == ["Python"]


def test_as_roles_skips_entries_missing_title_or_company() -> None:
    """_as_roles drops malformed role entries instead of raising."""
    roles = _as_roles(
        [
            {"title": "Engineer", "company": "Acme"},
            {"title": "Missing company"},
            {"company": "Missing title"},
            "not a dict",
        ]
    )
    assert len(roles) == 1
    assert isinstance(roles[0], CvRole)
    assert roles[0].title == "Engineer"


def test_as_roles_rejects_non_list() -> None:
    """_as_roles returns [] when the value is not a list."""
    assert _as_roles("nope") == []


def test_opt_str_normalises_blank_and_none() -> None:
    """_opt_str maps blank/None values to None."""
    assert _opt_str(None) is None
    assert _opt_str("   ") is None
    assert _opt_str("2020") == "2020"


def test_person_search_result_defaults() -> None:
    """PersonSearchResult defaults to empty and low confidence."""
    result = PersonSearchResult(full_name="Jane Doe")
    assert result.skills == []
    assert result.past_roles == []
    assert result.confidence == "low"
    assert result.sources == []
