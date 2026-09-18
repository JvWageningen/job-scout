"""Tests for deriving, saving and loading the letter style guide.

Every letter, name and employer here is fictional.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

import job_scout.config
from job_scout.config import user_letters_dir
from job_scout.letters.models import ExampleLetter, LetterLanguage
from job_scout.letters.style import (
    STYLE_GUIDE_HEADINGS,
    StyleError,
    derive_style_guide,
    load_style_guide,
    save_style_guide,
    style_guide_path,
)
from job_scout.llm.base import CallPurpose, LLMError
from tests.helpers import FakeLLMClient
from tests.style_checks import GENERATED, PLAIN, assert_styled_prompt

DUTCH_LETTER = """\
Beste wervingsteam,

Met deze brief solliciteer ik op de functie van data-analist. Ik zoek een plek \
waar ik vaker met de metingen zelf werk.

Wat ik meebreng:
- vijf jaar ervaring met meetreeksen
- rapportages die direct gebruikt worden

Een certificaat in cloudplatforms heb ik nog niet; dat haal ik dit najaar.
Ik bespreek graag hoe u het team wilt inrichten.
"""

ENGLISH_LETTER = """\
Dear hiring team,

I am applying for the position of optics engineer because I want to move from \
testing lenses to designing them.

In my current role I calibrate measurement benches every week, and I would like \
to discuss how design and test work together in your team.
"""

GOOD_GUIDE = """\
# Letter style guide

## Voice and register
- Write plainly, in the first person.

## Structure
- Open with the role and the reason for applying.

## Dutch letters
- Address the reader with "u". Aim for 220 words.

## English letters
- Keep the Dutch directness. Aim for 200 words.

## Keep doing
- Name a gap once, with how you will close it.

## Improve
- Instead of "I am very motivated", write what you want to work on.

## Never
- Never write "uitdagende functie".
"""


def _example(
    name: str, text: str, language: LetterLanguage = LetterLanguage.NL
) -> ExampleLetter:
    """Build an example letter the way list_examples would return it.

    Args:
        name: Stored file name.
        text: Letter text.
        language: Letter language.

    Returns:
        The example letter.
    """
    return ExampleLetter(
        name=name,
        language=language,
        text=text,
        words=len(text.split()),
        modified=datetime(2026, 3, 1, tzinfo=UTC),
    )


def _prompt(client: FakeLLMClient) -> str:
    """Return the single prompt sent to the fake client.

    Args:
        client: The fake client after one derive call.

    Returns:
        The prompt text.
    """
    assert len(client.calls) == 1
    return client.calls[0][0]


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point job-scout at a temporary data directory.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The temporary data directory.
    """
    monkeypatch.setattr(job_scout.config, "DATA_DIR", tmp_path)
    return tmp_path


@pytest.fixture()
def examples() -> list[ExampleLetter]:
    """Return one Dutch and one English example letter.

    Returns:
        The example letters, newest first.
    """
    return [
        _example("deltameet-2026.pdf", DUTCH_LETTER),
        _example("polderlicht-2025.docx", ENGLISH_LETTER, LetterLanguage.EN),
    ]


class TestDeriveStyleGuide:
    """derive_style_guide validates and cleans the model's response."""

    def test_refuses_empty_examples(self) -> None:
        """No examples means no call and a StyleError."""
        client = FakeLLMClient([GOOD_GUIDE])

        with pytest.raises(StyleError, match="example"):
            derive_style_guide([], client)

        assert client.calls == []

    def test_accepts_well_formed_guide(self, examples: list[ExampleLetter]) -> None:
        """A complete guide comes back stripped, from a cover_letter call."""
        client = FakeLLMClient(["\n\n" + GOOD_GUIDE + "\n\n"])

        guide = derive_style_guide(examples, client)

        assert guide == GOOD_GUIDE.strip()
        purpose: CallPurpose = client.calls[0][1]
        assert purpose == "cover_letter"

    def test_unwraps_fenced_guide(self, examples: list[ExampleLetter]) -> None:
        """A guide wrapped in a markdown code fence is unwrapped."""
        client = FakeLLMClient(["```markdown\n" + GOOD_GUIDE + "```\n"])

        assert derive_style_guide(examples, client) == GOOD_GUIDE.strip()

    def test_drops_preamble_around_fenced_guide(
        self, examples: list[ExampleLetter]
    ) -> None:
        """Chatter before a fenced guide is removed along with the fence."""
        response = "Here is the style guide:\n\n```md\n" + GOOD_GUIDE + "```"
        client = FakeLLMClient([response])

        assert derive_style_guide(examples, client) == GOOD_GUIDE.strip()

    def test_refuses_guide_missing_headings(
        self, examples: list[ExampleLetter]
    ) -> None:
        """Missing headings are refused, and the message names each of them."""
        broken = GOOD_GUIDE.replace("## Improve\n", "").replace("## Never\n", "")
        client = FakeLLMClient([broken])

        with pytest.raises(StyleError) as excinfo:
            derive_style_guide(examples, client)

        message = str(excinfo.value)
        assert "Improve" in message
        assert "Never" in message
        assert "Structure" not in message

    def test_refuses_guide_missing_title(self, examples: list[ExampleLetter]) -> None:
        """A guide without its title is refused."""
        client = FakeLLMClient([GOOD_GUIDE.replace("# Letter style guide\n", "")])

        with pytest.raises(StyleError, match="Letter style guide"):
            derive_style_guide(examples, client)

    def test_refuses_empty_response(self, examples: list[ExampleLetter]) -> None:
        """A blank response is refused."""
        client = FakeLLMClient(["   \n"])

        with pytest.raises(StyleError, match="empty"):
            derive_style_guide(examples, client)

    def test_guide_wording_comes_back_plain_and_keeps_its_structure(
        self, examples: list[ExampleLetter]
    ) -> None:
        """The writer copies the guide's punctuation, so the guide loses its dashes.

        Headings, bullets and the indentation of a nested bullet stay; only the
        wording inside them changes.
        """
        dirty = GOOD_GUIDE.replace(
            "- Write plainly, in the first person.",
            f"- {GENERATED}\n  - **Kort** \u2014 en direct.",
        )
        client = FakeLLMClient([dirty])

        guide = derive_style_guide(examples, client)

        expected = GOOD_GUIDE.replace(
            "- Write plainly, in the first person.",
            f"- {PLAIN}\n  - Kort, en direct.",
        )
        assert guide == expected.strip()

    def test_model_failure_becomes_style_error(
        self, examples: list[ExampleLetter]
    ) -> None:
        """An LLMError from the client surfaces as a StyleError."""
        client = FakeLLMClient([], repeat_last=False)

        with pytest.raises(StyleError) as excinfo:
            derive_style_guide(examples, client)

        assert isinstance(excinfo.value.__cause__, LLMError)


class TestDerivationPrompt:
    """The prompt carries the letters and the instructions that keep facts out."""

    def test_contains_every_example_with_labels(
        self, examples: list[ExampleLetter]
    ) -> None:
        """Each letter appears in full, labelled with language and word count."""
        client = FakeLLMClient([GOOD_GUIDE])

        derive_style_guide(examples, client)

        prompt = _prompt(client)
        assert DUTCH_LETTER.strip() in prompt
        assert ENGLISH_LETTER.strip() in prompt
        assert f'language="Dutch" words="{examples[0].words}"' in prompt
        assert f'language="English" words="{examples[1].words}"' in prompt

    def test_contains_headings_in_order(self, examples: list[ExampleLetter]) -> None:
        """The title and every required heading are listed, in order."""
        client = FakeLLMClient([GOOD_GUIDE])

        derive_style_guide(examples, client)

        prompt = _prompt(client)
        expected = ["# Letter style guide", *(f"## {h}" for h in STYLE_GUIDE_HEADINGS)]
        assert "\n".join(expected) in prompt

    def test_contains_no_specifics_instruction(
        self, examples: list[ExampleLetter]
    ) -> None:
        """The prompt forbids specifics, asks for placeholders and explains why."""
        client = FakeLLMClient([GOOD_GUIDE])

        derive_style_guide(examples, client)

        prompt = _prompt(client)
        assert "STYLE ONLY" in prompt
        assert "[employer]" in prompt
        assert "[project]" in prompt
        assert "every future letter mention that project" in prompt

    def test_contains_directness_and_section_guidance(
        self, examples: list[ExampleLetter]
    ) -> None:
        """Directness, imperative instructions and numbers are asked for."""
        client = FakeLLMClient([GOOD_GUIDE])

        derive_style_guide(examples, client)

        prompt = _prompt(client)
        assert "DIRECT" in prompt
        assert "imperative" in prompt
        assert "target body length in words for each language" in prompt

    def test_carries_the_house_style(self, examples: list[ExampleLetter]) -> None:
        """The guide must not teach the writer what the house style forbids."""
        client = FakeLLMClient([GOOD_GUIDE])

        derive_style_guide(examples, client)

        prompt = _prompt(client)
        assert_styled_prompt(prompt)
        assert "whether bulleted lists are used" not in prompt

    def test_leaves_out_file_names(self, examples: list[ExampleLetter]) -> None:
        """File names often name the employer, so they stay out of the prompt."""
        client = FakeLLMClient([GOOD_GUIDE])

        derive_style_guide(examples, client)

        prompt = _prompt(client)
        assert "deltameet-2026.pdf" not in prompt
        assert "polderlicht-2025.docx" not in prompt

    def test_truncates_long_letters(self) -> None:
        """A letter beyond 700 words is cut, keeping its opening and line breaks."""
        words = [f"woord{i}" for i in range(1000)]
        text = "\n\n".join(" ".join(words[i : i + 50]) for i in range(0, 1000, 50))
        client = FakeLLMClient([GOOD_GUIDE])

        derive_style_guide([_example("lang.txt", text)], client)

        prompt = _prompt(client)
        assert "woord699" in prompt
        assert "woord700" not in prompt
        assert "woord999" not in prompt
        assert "woord649\n\nwoord650" in prompt
        assert "truncated after 700 of 1000 words" in prompt

    def test_short_letter_not_marked_truncated(
        self, examples: list[ExampleLetter]
    ) -> None:
        """Letters within the limit carry no truncation marker."""
        client = FakeLLMClient([GOOD_GUIDE])

        derive_style_guide(examples, client)

        assert "truncated after" not in _prompt(client)

    def test_notes_a_missing_language(self) -> None:
        """With only Dutch letters, the prompt says not to invent English rules."""
        client = FakeLLMClient([GOOD_GUIDE])

        derive_style_guide([_example("brief.txt", DUTCH_LETTER)], client)

        prompt = _prompt(client)
        assert "No English letters were provided" in prompt
        assert "Do not invent English-specific rules" in prompt
        assert "1 Dutch letter(s)" in prompt
        assert "No Dutch letters were provided" not in prompt


class TestStyleGuideStorage:
    """save_style_guide and load_style_guide keep the guide on disk."""

    def test_path_is_under_user_letters_dir(self, data_dir: Path) -> None:
        """The guide lives at letters/style.md in the user's data directory."""
        assert style_guide_path("sam") == user_letters_dir("sam") / "style.md"
        assert style_guide_path("sam") == data_dir / "users/sam/letters/style.md"

    def test_round_trip(self, data_dir: Path) -> None:
        """A saved guide loads back unchanged, creating directories as needed."""
        save_style_guide("sam", GOOD_GUIDE)

        assert style_guide_path("sam").is_file()
        assert load_style_guide("sam") == GOOD_GUIDE.strip()

    def test_save_replaces_previous_guide(self, data_dir: Path) -> None:
        """Saving again overwrites the earlier guide."""
        save_style_guide("sam", GOOD_GUIDE)
        save_style_guide("sam", "# Letter style guide\n\n## Never\n- Flatter.\n")

        assert load_style_guide("sam") == "# Letter style guide\n\n## Never\n- Flatter."

    def test_save_writes_utf8(self, data_dir: Path) -> None:
        """Non-ASCII text is stored as UTF-8."""
        save_style_guide("sam", "# Letter style guide\n- Schrijf “zó” direct.")

        raw = style_guide_path("sam").read_bytes()
        assert "“zó”".encode() in raw

    def test_blank_save_refused(self, data_dir: Path) -> None:
        """A blank guide is refused and nothing is written."""
        with pytest.raises(StyleError, match="blank"):
            save_style_guide("sam", "  \n\t")

        assert not style_guide_path("sam").exists()

    def test_load_missing_returns_none(self, data_dir: Path) -> None:
        """No file means no guide."""
        assert load_style_guide("sam") is None

    def test_load_blank_file_returns_none(self, data_dir: Path) -> None:
        """A file holding only whitespace counts as no guide."""
        path = style_guide_path("sam")
        path.parent.mkdir(parents=True)
        path.write_text("\n  \n", encoding="utf-8")

        assert load_style_guide("sam") is None

    def test_load_tolerates_byte_order_mark(self, data_dir: Path) -> None:
        """A hand edit saved with a BOM loads without it."""
        path = style_guide_path("sam")
        path.parent.mkdir(parents=True)
        path.write_bytes("﻿# Letter style guide\n".encode())

        assert load_style_guide("sam") == "# Letter style guide"
