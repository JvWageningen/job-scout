"""Tests for resolving career tracks into the searches the pipeline runs."""

from __future__ import annotations

from job_scout.models import CareerTrack, Config
from job_scout.tracks import (
    DEFAULT_TRACK_ID,
    blend_tracks,
    effective_description,
    effective_negative,
    is_multi_track,
    merged_keywords,
    merged_profile,
    resolve_tracks,
    slugify_track_id,
    standalone_tracks,
)


def _single_profile_config() -> Config:
    """Build a config in the original single-profile shape."""
    return Config(
        profile_description="CRO specialist, individual contributor, in-house.",
        negative_description="No team-lead roles. No agency work.",
        keywords_dutch=["CRO specialist"],
        keywords_english=["conversion rate optimization"],
    )


def _multi_track_config() -> Config:
    """Build a config with several standalone tracks plus one blend track."""
    return Config(
        profile_description="legacy blended text",
        negative_description="No hour-tracking on client projects.",
        career_tracks=[
            CareerTrack(
                id="quality",
                name="Quality & Efficiency",
                description="Quality and efficiency improvement.",
                keywords_dutch=["kwaliteits engineer"],
                keywords_english=["quality engineer"],
            ),
            CareerTrack(
                id="rnd",
                name="R&D",
                description="Research and product development.",
                negative_description="Not a pure lab role.",
                keywords_english=["R&D engineer", "quality engineer"],
            ),
            CareerTrack(
                id="ai",
                name="AI & coding",
                description="building AI tools and writing some code",
                mode="blend",
            ),
        ],
    )


class TestSingleProfileUnchanged:
    """A user who never defines tracks must behave exactly as before."""

    def test_resolves_to_one_implicit_track(self) -> None:
        """The profile description becomes a single default track."""
        tracks = resolve_tracks(_single_profile_config())
        assert len(tracks) == 1
        assert tracks[0].id == DEFAULT_TRACK_ID
        assert (
            tracks[0].description == "CRO specialist, individual contributor, in-house."
        )

    def test_not_reported_as_multi_track(self) -> None:
        """One implicit track is not multi-track."""
        assert is_multi_track(_single_profile_config()) is False

    def test_merged_profile_is_the_profile_description_verbatim(self) -> None:
        """Screening text is unchanged, so screening behaviour is unchanged."""
        config = _single_profile_config()
        assert merged_profile(config) == config.profile_description

    def test_merged_keywords_are_the_config_keywords_verbatim(self) -> None:
        """Scraping keywords are unchanged, so scrape volume is unchanged."""
        config = _single_profile_config()
        assert merged_keywords(config) == (
            config.keywords_dutch,
            config.keywords_english,
        )

    def test_effective_description_has_no_blend_clause(self) -> None:
        """With no blend tracks the description passes through untouched."""
        config = _single_profile_config()
        track = resolve_tracks(config)[0]
        assert effective_description(track, []) == config.profile_description


class TestStandaloneAndBlendSplit:
    """Blend tracks colour other searches instead of being searched alone."""

    def test_blend_track_is_not_searched_alone(self) -> None:
        """Blend tracks are excluded from the standalone search set."""
        names = [t.name for t in standalone_tracks(_multi_track_config())]
        assert names == ["Quality & Efficiency", "R&D"]

    def test_blend_tracks_are_reported_separately(self) -> None:
        """Blend tracks are still available for folding in."""
        assert [t.name for t in blend_tracks(_multi_track_config())] == ["AI & coding"]

    def test_blend_folds_into_each_standalone_description(self) -> None:
        """Every standalone track gets the blend clause appended."""
        config = _multi_track_config()
        blends = blend_tracks(config)
        for track in standalone_tracks(config):
            text = effective_description(track, blends)
            assert track.description in text
            assert "building AI tools" in text

    def test_optional_blend_is_marked_as_a_bonus_not_a_job(self) -> None:
        """An optional blend must not read as its own target role."""
        config = _multi_track_config()
        text = effective_description(standalone_tracks(config)[0], blend_tracks(config))
        assert "bonus" in text.lower()
        assert "not what they want" in text.lower()

    def test_required_blend_is_stated_as_a_requirement(self) -> None:
        """A required blend reads as a hard requirement instead."""
        track = CareerTrack(id="q", name="Q", description="Quality work.")
        blend = CareerTrack(
            id="ai", name="AI", description="AI tooling", mode="blend", required=True
        )
        text = effective_description(track, [blend])
        assert "MUST" in text
        assert "requirement" in text.lower()

    def test_only_blend_tracks_still_yields_something_to_search(self) -> None:
        """A config of nothing but blend tracks must not search nothing."""
        config = Config(
            career_tracks=[
                CareerTrack(id="ai", name="AI", description="AI", mode="blend")
            ]
        )
        assert len(standalone_tracks(config)) == 1

    def test_disabled_tracks_are_excluded(self) -> None:
        """Disabled tracks drop out of resolution entirely."""
        config = Config(
            career_tracks=[
                CareerTrack(id="a", name="A", description="A"),
                CareerTrack(id="b", name="B", description="B", enabled=False),
            ]
        )
        assert [t.id for t in resolve_tracks(config)] == ["a"]


class TestMergedSearchInputs:
    """Screening and scraping run once across all tracks, not once per track."""

    def test_merged_profile_covers_every_standalone_track(self) -> None:
        """The screening profile names each direction."""
        text = merged_profile(_multi_track_config())
        assert "Quality & Efficiency" in text
        assert "R&D" in text
        assert "ANY of these directions" in text

    def test_merged_profile_includes_blend_clause(self) -> None:
        """Screening also knows about the cross-cutting interest."""
        assert "building AI tools" in merged_profile(_multi_track_config())

    def test_merged_keywords_union_and_dedupe(self) -> None:
        """Keywords are unioned across tracks without duplicates."""
        nl, en = merged_keywords(_multi_track_config())
        assert nl == ["kwaliteits engineer"]
        assert en == ["quality engineer", "R&D engineer"]  # duplicate dropped

    def test_is_multi_track_true_for_several_tracks(self) -> None:
        """Several configured tracks report as multi-track."""
        assert is_multi_track(_multi_track_config()) is True


class TestEffectiveNegative:
    """Track negatives extend the shared dealbreakers rather than replacing them."""

    def test_combines_profile_and_track_negatives(self) -> None:
        """Both the global and per-track negatives appear."""
        config = _multi_track_config()
        rnd = next(t for t in standalone_tracks(config) if t.id == "rnd")
        text = effective_negative(rnd, config)
        assert "hour-tracking" in text
        assert "pure lab role" in text

    def test_falls_back_to_profile_negative_only(self) -> None:
        """A track without its own negatives still inherits the shared ones."""
        config = _multi_track_config()
        quality = next(t for t in standalone_tracks(config) if t.id == "quality")
        assert effective_negative(quality, config) == config.negative_description


class TestSlugifyTrackId:
    """Track ids derived from display names stay readable and stable."""

    def test_lowercases_and_hyphenates(self) -> None:
        """Spaces and case are normalised."""
        assert slugify_track_id("Quality & Efficiency") == "quality-efficiency"

    def test_strips_punctuation(self) -> None:
        """Punctuation is removed rather than encoded."""
        assert slugify_track_id("R&D / Product Dev!") == "rd-product-dev"

    def test_falls_back_for_unusable_names(self) -> None:
        """A name with nothing usable still yields an id."""
        assert slugify_track_id("!!!") == "track"

    def test_is_stable_across_calls(self) -> None:
        """The same name always yields the same id."""
        assert slugify_track_id("Production") == slugify_track_id("Production")
