"""Memories: the model, the store, the migration, selection and prompt material.

Every person, employer and project here is invented. Nothing here calls a model.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

import job_scout.config as config
from job_scout.database import Database
from job_scout.memories import (
    ALL_USES,
    MAX_TAGS,
    MAX_TEXT_CHARS,
    MEMORY_CV_RULE,
    MEMORY_GUIDE,
    Memory,
    MemoryContent,
    MemoryDraft,
    MemoryKind,
    MemorySource,
    MemoryStoreError,
    MemoryUse,
    add_memories,
    add_memory,
    delete_all_memories,
    delete_memory,
    describe_origin,
    eligible_memories,
    find_duplicate,
    get_memory,
    list_memories,
    memories_payload,
    memory_label,
    memory_labels,
    memory_matches,
    normalise_tags,
    rank_memories,
    relevance,
    same_fact,
    select_memories,
    select_memories_payload,
    update_memory,
)
from tests.style_checks import assert_plain

USER = "tester"
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
VACANCY = (
    "Conversion specialist (CRO) bij Voorbeeldwinkel. Je draait A/B-tests met "
    "Optimizely en rapporteert in Looker. Ervaring met experimentation is een pre."
)


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate every test in its own data directory holding one user."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    config.save_user_config(USER, {"name": USER})
    return tmp_path


def _memory(memory_id: int, text: str, **fields: object) -> Memory:
    """Build a stored memory without touching a database."""
    moment = fields.pop("updated_at", NOW)
    return Memory.model_validate(
        {
            "id": memory_id,
            "text": text,
            "created_at": moment,
            "updated_at": moment,
            **fields,
        }
    )


# -- The model --------------------------------------------------------------------


class TestMemoryContent:
    """What a memory accepts and how it tidies what it is given."""

    def test_defaults_allow_every_use_and_are_not_sensitive(self) -> None:
        """A memory added with only a text may be used everywhere."""
        memory = MemoryContent(text="Ik spreek vloeiend Duits.")
        assert memory.use_in == list(ALL_USES)
        assert memory.kind is MemoryKind.OTHER
        assert memory.sensitive is False
        assert memory.tags == []

    def test_text_and_hint_are_kept_on_one_line(self) -> None:
        """Whitespace a form or a paste adds is collapsed, the words are kept."""
        memory = MemoryContent(
            text="  I led the\n migration   of 40 stores. ", hint=" Use for\tretail "
        )
        assert memory.text == "I led the migration of 40 stores."
        assert memory.hint == "Use for retail"

    def test_an_empty_or_overlong_text_is_refused(self) -> None:
        """A memory states something, and within the limit."""
        with pytest.raises(ValidationError):
            MemoryContent(text="   ")
        with pytest.raises(ValidationError):
            MemoryContent(text="x" * (MAX_TEXT_CHARS + 1))

    def test_tags_are_lower_case_deduplicated_and_capped(self) -> None:
        """Tags are keywords: case, a leading hash and repeats do not matter."""
        tags = ["CRO", "#cro", " A/B  Testing ", "", *[f"t{i}" for i in range(20)]]
        memory = MemoryContent(text="Fact.", tags=tags)
        assert memory.tags[:2] == ["cro", "a/b testing"]
        assert len(memory.tags) == MAX_TAGS

    def test_tags_may_be_given_as_one_comma_separated_string(self) -> None:
        """A form field or a CLI option gives tags as one string."""
        assert normalise_tags("retail, POS,,pos") == ["retail", "pos"]
        assert MemoryContent(text="Fact.", tags="a, b").tags == ["a", "b"]

    def test_an_overlong_tag_is_dropped(self) -> None:
        """A sentence in the tag field is not a keyword."""
        assert normalise_tags(["ok", "x" * 41]) == ["ok"]

    def test_uses_are_deduplicated_and_kept_in_one_order(self) -> None:
        """However they are given, the uses read cv, letter, interview."""
        memory = MemoryContent(text="Fact.", use_in=["interview", "cv", "cv"])
        assert memory.use_in == [MemoryUse.CV, MemoryUse.INTERVIEW]
        assert MemoryContent(text="Fact.", use_in="letter,cv").use_in == [
            MemoryUse.CV,
            MemoryUse.LETTER,
        ]

    def test_no_use_at_all_keeps_the_memory_unused(self) -> None:
        """Unticking every use is allowed: the memory is kept, not sent."""
        assert MemoryContent(text="Fact.", use_in=[]).use_in == []

    def test_an_unknown_use_or_kind_is_refused(self) -> None:
        """What a person types must be one of the known values."""
        with pytest.raises(ValidationError):
            MemoryContent(text="Fact.", use_in=["website"])
        with pytest.raises(ValidationError):
            MemoryContent.model_validate({"text": "Fact.", "kind": "hobby"})

    def test_the_origin_is_cut_rather_than_refused(self) -> None:
        """source_detail is only a label; a long employer name must not fail."""
        draft = MemoryDraft(text="Fact.", source_detail="notes " * 100)
        assert len(draft.source_detail) == 200

    def test_a_vacancy_id_must_be_positive(self) -> None:
        """job_id refers to a vacancy, and vacancy ids start at 1."""
        with pytest.raises(ValidationError):
            MemoryDraft(text="Fact.", job_id=0)

    def test_the_label_is_stable_and_names_the_id(self) -> None:
        """A citation names the memory by its id, across generations."""
        assert _memory(7, "Fact.").label == "memory 7" == memory_label(7)

    def test_content_returns_only_the_editable_part(self) -> None:
        """content() is what an edit starts from."""
        memory = _memory(3, "Fact.", tags=["a"], sensitive=True, job_id=12)
        content = memory.content()
        assert type(content) is MemoryContent
        assert content.tags == ["a"]
        assert content.sensitive is True


# -- The store --------------------------------------------------------------------


class TestStore:
    """Adding, reading, changing and deleting a user's memories."""

    def test_a_new_user_has_no_memories(self) -> None:
        """The table exists from the first open and starts empty."""
        assert list_memories(USER) == []

    def test_add_and_read_back_every_field(self) -> None:
        """What is stored comes back as it was given, with an id and times."""
        draft = MemoryDraft(
            text="Ik heb bij Voorbeeld B.V. 40 winkels naar een nieuw kassasysteem "
            "gemigreerd.",
            kind=MemoryKind.PROJECT,
            tags=["retail", "migratie"],
            hint="Gebruik voor retail IT rollen",
            use_in=[MemoryUse.LETTER, MemoryUse.INTERVIEW],
            sensitive=False,
            source=MemorySource.LETTER_NOTES,
            source_detail="notes for the Findwhere letter",
            job_id=42,
        )
        stored = add_memory(USER, draft)
        assert stored.id > 0
        assert stored.created_at.tzinfo is not None
        again = get_memory(USER, stored.id)
        assert again == stored
        dumped = again.model_dump(exclude={"id", "created_at", "updated_at"})
        assert dumped == draft.model_dump()

    def test_several_are_added_in_order_and_listed_newest_first(self) -> None:
        """add_memories keeps the given order; the list shows the last first."""
        stored = add_memories(
            USER, [MemoryDraft(text="First."), MemoryDraft(text="Second.")]
        )
        assert [m.text for m in stored] == ["First.", "Second."]
        assert [m.text for m in list_memories(USER)] == ["Second.", "First."]

    def test_adding_nothing_stores_nothing(self) -> None:
        """An empty selection is not an error."""
        assert add_memories(USER, []) == []

    def test_an_edit_replaces_the_content_and_keeps_the_origin(self) -> None:
        """Where a memory came from and when it was made do not change."""
        stored = add_memory(
            USER,
            MemoryDraft(
                text="Old.", source=MemorySource.TEXT_IMPORT, job_id=5, sensitive=True
            ),
        )
        changed = update_memory(
            USER,
            stored.id,
            MemoryContent(text="New.", tags=["x"], use_in=["cv"], sensitive=False),
        )
        assert changed is not None
        assert changed.text == "New."
        assert changed.tags == ["x"]
        assert changed.use_in == [MemoryUse.CV]
        assert changed.sensitive is False
        assert changed.source is MemorySource.TEXT_IMPORT
        assert changed.job_id == 5
        assert changed.created_at == stored.created_at
        assert changed.updated_at >= stored.updated_at

    def test_editing_or_deleting_a_missing_memory_says_so(self) -> None:
        """No row, no change, and no error."""
        assert update_memory(USER, 999, MemoryContent(text="New.")) is None
        assert delete_memory(USER, 999) is False
        assert get_memory(USER, 999) is None

    def test_delete_one_and_delete_all(self) -> None:
        """Deleting removes exactly what was asked for."""
        first, second, third = add_memories(
            USER, [MemoryDraft(text=t) for t in ("One.", "Two.", "Three.")]
        )
        assert delete_memory(USER, second.id) is True
        assert [m.id for m in list_memories(USER)] == [third.id, first.id]
        assert delete_all_memories(USER) == 2
        assert list_memories(USER) == []

    @pytest.mark.parametrize("name", ["", "..", "all", "a/b", "nobody"])
    def test_an_unknown_or_unsafe_user_is_refused(self, name: str) -> None:
        """No database path is built from a name that is not a user."""
        with pytest.raises(MemoryStoreError):
            list_memories(name)
        with pytest.raises(MemoryStoreError):
            add_memory(name, MemoryDraft(text="Fact."))

    def test_users_do_not_see_each_others_memories(self) -> None:
        """Each user has their own database."""
        config.save_user_config("other", {"name": "other"})
        add_memory(USER, MemoryDraft(text="Mine."))
        assert list_memories("other") == []

    def test_a_row_with_values_this_version_does_not_know_is_still_read(
        self,
    ) -> None:
        """A newer kind or use degrades to "other" and is dropped, not fatal."""
        stored = add_memory(USER, MemoryDraft(text="Fact."))
        conn = sqlite3.connect(config.user_db_path(USER))
        conn.execute(
            "UPDATE memories SET kind = 'hobby', source = 'future', "
            """use_in_json = '["cv", "website"]' WHERE id = ?""",
            (stored.id,),
        )
        conn.commit()
        conn.close()
        memory = get_memory(USER, stored.id)
        assert memory is not None
        assert memory.kind is MemoryKind.OTHER
        assert memory.source is MemorySource.MANUAL
        assert memory.use_in == [MemoryUse.CV]


class TestDatabase:
    """The tables themselves, and databases made before they existed."""

    def test_a_database_from_before_memories_gains_both_tables(
        self, tmp_path: Path
    ) -> None:
        """An existing install opens with its data intact and memories empty."""
        path = tmp_path / "old.db"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE star_stories (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "situation TEXT NOT NULL, task TEXT NOT NULL, action TEXT NOT NULL, "
            "result TEXT NOT NULL, keywords TEXT NOT NULL, created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO star_stories VALUES "
            "(1, 's', 't', 'a', 'r', '[]', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        conn.close()

        db = Database(path)
        assert db.get_memories() == []
        assert db.get_notes_capture("abc") is None
        assert len(db.get_star_stories()) == 1

    def test_a_memories_table_missing_later_columns_gains_them(
        self, tmp_path: Path
    ) -> None:
        """A row written before a column existed reads with that column's default."""
        path = tmp_path / "partial.db"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "text TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO memories (text, created_at, updated_at) VALUES "
            "('Kept.', '2026-09-01T10:00:00+00:00', '2026-09-01T10:00:00+00:00')"
        )
        conn.commit()
        conn.close()

        rows = Database(path).get_memories()
        assert len(rows) == 1
        row = rows[0]
        assert row["text"] == "Kept."
        assert row["kind"] == "other"
        assert row["tags"] == []
        assert row["use_in"] == ["cv", "letter", "interview"]
        assert row["sensitive"] is False
        assert row["source"] == "manual"
        assert row["job_id"] is None

    def test_opening_twice_changes_nothing(self, tmp_path: Path) -> None:
        """The creation and migration are safe to run on every open."""
        path = tmp_path / "twice.db"
        Database(path).add_memories(
            [
                {
                    "text": "Once.",
                    "kind": "skill",
                    "tags": ["a"],
                    "hint": "",
                    "use_in": ["cv"],
                    "sensitive": True,
                    "source": "manual",
                    "source_detail": "",
                    "job_id": None,
                }
            ]
        )
        rows = Database(path).get_memories()
        assert [(r["text"], r["sensitive"], r["use_in"]) for r in rows] == [
            ("Once.", True, ["cv"])
        ]

    def test_an_update_replaces_the_content_and_ignores_the_origin(
        self, tmp_db: Database
    ) -> None:
        """Only the editable fields are written; all of them are required."""
        [memory_id] = tmp_db.add_memories(
            [
                {
                    "text": "Old.",
                    "kind": "other",
                    "tags": [],
                    "hint": "",
                    "use_in": ["cv"],
                    "sensitive": False,
                    "source": "letter_notes",
                    "source_detail": "notes",
                    "job_id": 3,
                }
            ]
        )
        content = {
            "text": "New.",
            "kind": "skill",
            "tags": ["caf\u00e9"],
            "hint": "Hint.",
            "use_in": [],
            "sensitive": True,
            "source": "manual",
        }
        assert tmp_db.update_memory(memory_id, content) is True
        row = tmp_db.get_memory(memory_id)
        assert row is not None
        assert (row["text"], row["tags"], row["use_in"]) == ("New.", ["caf\u00e9"], [])
        assert row["sensitive"] is True
        assert (row["source"], row["job_id"]) == ("letter_notes", 3)
        with pytest.raises(KeyError):
            tmp_db.update_memory(memory_id, {"text": "Only text."})

    def test_a_claim_is_taken_once_until_it_is_released_or_stale(
        self, tmp_db: Database
    ) -> None:
        """Two captures of the same notes cannot both run."""
        stale = datetime.now(UTC) - timedelta(minutes=30)
        assert tmp_db.claim_notes_capture(
            "h", source="letter_notes", job_id=1, stale_before=stale
        )
        assert not tmp_db.claim_notes_capture(
            "h", source="letter_notes", job_id=1, stale_before=stale
        )
        tmp_db.release_notes_capture("h")
        assert tmp_db.claim_notes_capture(
            "h", source="letter_notes", job_id=1, stale_before=stale
        )
        future = datetime.now(UTC) + timedelta(minutes=1)
        assert tmp_db.claim_notes_capture(
            "h", source="interview_notes", job_id=2, stale_before=future
        )

    def test_a_completed_capture_is_never_claimed_again(self, tmp_db: Database) -> None:
        """Not by a later attempt and not by a stale takeover."""
        future = datetime.now(UTC) + timedelta(days=1)
        assert tmp_db.claim_notes_capture(
            "h", source="letter_notes", job_id=None, stale_before=future
        )
        tmp_db.complete_notes_capture("h", 3)
        assert not tmp_db.claim_notes_capture(
            "h", source="letter_notes", job_id=None, stale_before=future
        )
        tmp_db.release_notes_capture("h")
        record = tmp_db.get_notes_capture("h")
        assert record is not None
        assert record["memories_added"] == 3

    def test_deleting_all_memories_keeps_the_record_of_captured_notes(
        self, tmp_db: Database
    ) -> None:
        """Otherwise the same notes would bring deleted memories back."""
        stale = datetime.now(UTC) - timedelta(minutes=30)
        tmp_db.claim_notes_capture("h", source="x", job_id=None, stale_before=stale)
        tmp_db.complete_notes_capture("h", 0)
        tmp_db.delete_all_memories()
        assert tmp_db.get_notes_capture("h") is not None


# -- Comparing texts ----------------------------------------------------------------


class TestSameFact:
    """What counts as a memory that is already known."""

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            ("I speak German fluently.", "i speak german fluently"),
            ("Ik heb Zo\u00eb geholpen.", "ik heb Zoe geholpen"),
            (
                "I led the migration of 40 stores to a new POS system in 2022.",
                "I led the migrations of 40 stores to a new POS system in 2022",
            ),
            (
                "I ran 120 A/B tests in Optimizely for a webshop.",
                "For a webshop I ran 120 A/B tests in Optimizely.",
            ),
        ],
    )
    def test_the_same_fact_in_other_words_or_case_is_a_duplicate(
        self, first: str, second: str
    ) -> None:
        """Case, accents, punctuation, word order and a plural do not matter."""
        assert same_fact(first, second)

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            (
                "I have worked in retail since 2019.",
                "I have worked in retail since 2020.",
            ),
            ("I led the migration of 40 stores.", "I led the migration of 45 stores."),
            ("I speak German.", "I am studying for my PMP certificate."),
            ("", "Something."),
        ],
    )
    def test_a_different_number_or_fact_is_new(self, first: str, second: str) -> None:
        """A different number is a different claim."""
        assert not same_fact(first, second)

    def test_find_duplicate_returns_the_memory_that_says_it(self) -> None:
        """The caller can say which memory already holds the fact."""
        known = [_memory(1, "I speak German."), _memory(2, "I hold a driving licence.")]
        found = find_duplicate("I hold a driving licence", known)
        assert found is not None
        assert found.id == 2
        assert find_duplicate("I play the cello.", known) is None


# -- Selection ----------------------------------------------------------------------


class TestSelection:
    """Which memories a generator receives, and in what order."""

    def test_only_memories_allowed_for_the_purpose_are_eligible(self) -> None:
        """A memory kept off the CV never reaches CV tailoring."""
        on_cv = _memory(1, "On the CV.", use_in=["cv"])
        not_on_cv = _memory(2, "Not on the CV.", use_in=["letter", "interview"])
        unused = _memory(3, "Used nowhere.", use_in=[])
        memories = [on_cv, not_on_cv, unused]
        assert eligible_memories(memories, "cv") == [on_cv]
        assert eligible_memories(memories, MemoryUse.LETTER) == [not_on_cv]
        assert eligible_memories(memories, "interview") == [not_on_cv]

    def test_a_sensitive_memory_is_never_eligible(self) -> None:
        """Not for any purpose, until the applicant clears the flag."""
        private = _memory(1, "Private.", sensitive=True)
        for use in ALL_USES:
            assert eligible_memories([private], use) == []
            assert rank_memories([private], use, VACANCY) == []

    def test_an_unknown_purpose_is_refused(self) -> None:
        """A typo in a caller must not silently select nothing."""
        with pytest.raises(ValueError):
            eligible_memories([], "website")

    def test_memories_whose_tags_fit_the_vacancy_rank_first(self) -> None:
        """A matching tag outweighs a word shared by accident."""
        unrelated = _memory(1, "I restored a sailing boat.", tags=["sailing"])
        tagged = _memory(
            2, "I set up an experimentation programme.", tags=["cro", "optimizely"]
        )
        worded = _memory(3, "I report weekly in Looker dashboards.")
        ranked = rank_memories([unrelated, tagged, worded], "letter", VACANCY)
        assert [m.id for m in ranked] == [2, 3, 1]

    def test_a_multi_word_tag_matches_as_a_phrase_or_all_its_words(self) -> None:
        """ "a/b tests" matches a vacancy asking for A/B-tests."""
        memory = _memory(1, "Fact.", tags=["a/b tests", "conversion specialist"])
        assert relevance(memory, VACANCY) == 6

    def test_words_in_the_hint_count_and_stopwords_do_not(self) -> None:
        """The hint says when a memory applies; "ervaring" says nothing."""
        hinted = _memory(1, "Fact.", hint="Use for experimentation roles")
        common = _memory(2, "Ervaring met een team.")
        assert relevance(hinted, VACANCY) >= 1
        assert relevance(common, VACANCY) == 0

    def test_ties_go_to_the_most_recently_changed(self) -> None:
        """With nothing to tell them apart, the newer statement comes first."""
        older = _memory(1, "Old fact.", updated_at=NOW - timedelta(days=3))
        newer = _memory(2, "New fact.", updated_at=NOW)
        assert [m.id for m in rank_memories([older, newer], "cv", "")] == [2, 1]

    def test_the_selection_is_trimmed_to_the_limit(self) -> None:
        """Many memories must not crowd out the CV."""
        memories = [_memory(i, f"Fact number {i}.") for i in range(1, 41)]
        assert len(rank_memories(memories, "letter", VACANCY)) == 25
        assert len(rank_memories(memories, "letter", VACANCY, limit=5)) == 5
        assert rank_memories(memories, "letter", VACANCY, limit=0) == []

    def test_with_few_memories_all_eligible_ones_are_returned(self) -> None:
        """Even without any overlap; the model decides with the hints."""
        memories = [_memory(1, "I restored a sailing boat."), _memory(2, "Other.")]
        assert len(rank_memories(memories, "letter", VACANCY)) == 2

    def test_select_memories_reads_the_users_store(self) -> None:
        """select_memories is rank_memories over the stored memories."""
        add_memories(
            USER,
            [
                MemoryDraft(text="Private.", sensitive=True),
                MemoryDraft(text="Letter only.", use_in=["letter"]),
                MemoryDraft(text="I ran A/B tests.", tags=["cro"]),
            ],
        )
        assert [m.text for m in select_memories(USER, "cv", VACANCY)] == [
            "I ran A/B tests."
        ]
        letter = [m.text for m in select_memories(USER, "letter", VACANCY)]
        assert letter == ["I ran A/B tests.", "Letter only."]


# -- Prompt material ---------------------------------------------------------------


class TestPayload:
    """What a generator's prompt receives about each memory."""

    def test_each_memory_is_labelled_with_its_kind_hint_and_tags(self) -> None:
        """The label is what an interview answer cites."""
        memory = _memory(
            4,
            "I ran 120 A/B tests.",
            kind="achievement",
            tags=["cro"],
            hint="Use for CRO roles",
            source="letter_notes",
            source_detail="notes for the Findwhere letter",
            job_id=9,
        )
        assert memories_payload([memory]) == [
            {
                "label": "memory 4",
                "kind": "achievement",
                "text": "I ran 120 A/B tests.",
                "noted_on": "2026-09-19",
                "hint": "Use for CRO roles",
                "tags": ["cro"],
            }
        ]

    def test_an_empty_hint_and_no_tags_are_left_out(self) -> None:
        """Nothing empty is sent."""
        entry = memories_payload([_memory(1, "Fact.")])[0]
        assert "hint" not in entry
        assert "tags" not in entry

    def test_where_a_memory_came_from_never_reaches_the_prompt(self) -> None:
        """Another employer's name has no place in this vacancy's letter."""
        memory = _memory(1, "Fact.", source_detail="notes for the Findwhere letter")
        assert "Findwhere" not in str(memories_payload([memory]))

    def test_a_sensitive_memory_is_left_out_whoever_passes_it(self) -> None:
        """The payload is the last gate before the model."""
        payload = memories_payload(
            [_memory(1, "Private.", sensitive=True), _memory(2, "Open.")]
        )
        assert [entry["label"] for entry in payload] == ["memory 2"]

    def test_the_labels_can_be_checked_against_a_citation(self) -> None:
        """Citations are validated the way STAR story citations are."""
        payload = memories_payload([_memory(3, "A."), _memory(7, "B.")])
        assert memory_labels(payload) == {"memory 3", "memory 7"}

    def test_select_memories_payload_combines_both_steps(self) -> None:
        """One call for the generators that wire memories in."""
        stored = add_memory(USER, MemoryDraft(text="I ran A/B tests.", tags=["cro"]))
        payload = select_memories_payload(USER, "interview", VACANCY)
        assert [entry["label"] for entry in payload] == [stored.label]

    def test_the_guide_and_the_cv_rule_follow_the_house_style(self) -> None:
        """Prompt text must not carry the dashes it forbids."""
        assert_plain(MEMORY_GUIDE)
        assert_plain(MEMORY_CV_RULE)


# -- Showing memories --------------------------------------------------------------


def test_the_origin_is_described_in_words() -> None:
    """The Memories list says where each memory came from."""
    assert describe_origin(MemoryDraft(text="A.")) == "added by hand"
    draft = MemoryDraft(
        text="A.", source="letter_notes", source_detail="notes for the Findwhere letter"
    )
    assert describe_origin(draft) == (
        "taken from letter notes (notes for the Findwhere letter)"
    )


def test_a_search_matches_text_tags_hint_and_kind_ignoring_case() -> None:
    """Every word must occur somewhere in the memory."""
    memory = _memory(1, "Ik heb 40 winkels gemigreerd.", tags=["Retail"], hint="POS")
    assert memory_matches(memory, "")
    assert memory_matches(memory, "RETAIL winkels")
    assert memory_matches(memory, "other")
    assert not memory_matches(memory, "retail python")
