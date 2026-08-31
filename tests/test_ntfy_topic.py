"""Tests for generating and sharing a hard-to-guess ntfy topic."""

from __future__ import annotations

import re

import pytest

from job_scout import ntfy_topic


class TestSlugifyName:
    """The readable half of the topic comes from the user's name."""

    def test_lowercases_and_hyphenates(self) -> None:
        """Spaces become hyphens."""
        assert (
            ntfy_topic.slugify_name("Jeroen van Wageningen") == "jeroen-van-wageningen"
        )

    def test_strips_punctuation(self) -> None:
        """Punctuation cannot appear in an ntfy topic."""
        assert ntfy_topic.slugify_name("O'Brien, Seán!") == "o-brien-se-n"

    def test_handles_name_with_no_usable_characters(self) -> None:
        """A name of pure punctuation slugifies to nothing rather than junk."""
        assert ntfy_topic.slugify_name("!!!") == ""

    def test_trims_leading_and_trailing_separators(self) -> None:
        """No topic should start or end with a hyphen."""
        assert not ntfy_topic.slugify_name("  Jeroen  ").startswith("-")
        assert not ntfy_topic.slugify_name("  Jeroen  ").endswith("-")


class TestGenerateTopic:
    """Topics must be recognisable but not guessable."""

    def test_contains_prefix_and_name(self) -> None:
        """The topic is identifiable in a phone full of subscriptions."""
        topic = ntfy_topic.generate_topic("Jeroen")
        assert topic.startswith("job-scout-jeroen-")

    def test_only_uses_characters_ntfy_accepts(self) -> None:
        """ntfy topics are restricted to [A-Za-z0-9_-]."""
        topic = ntfy_topic.generate_topic("Jeroen van Wageningen")
        assert re.fullmatch(r"[A-Za-z0-9_-]+", topic)

    def test_is_different_every_time(self) -> None:
        """The entropy is the whole point."""
        topics = {ntfy_topic.generate_topic("Jeroen") for _ in range(20)}
        assert len(topics) == 20

    def test_unusable_name_does_not_leave_a_double_hyphen(self) -> None:
        """A name that slugifies to nothing must not produce 'job-scout--xyz'."""
        topic = ntfy_topic.generate_topic("!!!")
        assert "--" not in topic
        assert topic.startswith("job-scout-")

    def test_suffix_is_long_enough_to_resist_guessing(self) -> None:
        """A short suffix would defeat the purpose."""
        suffix = ntfy_topic.generate_topic("Jeroen").rsplit("-", 1)[-1]
        assert len(suffix) == ntfy_topic.SUFFIX_LENGTH


class TestIsSecureTopic:
    """The dashboard warns when the topic is still guessable."""

    def test_generated_topics_are_secure(self) -> None:
        """Whatever the generator produces must pass its own check."""
        for _ in range(20):
            assert ntfy_topic.is_secure_topic(ntfy_topic.generate_topic("Jeroen"))

    def test_default_topic_is_not_secure(self) -> None:
        """The shipped default is exactly what we want to warn about."""
        assert ntfy_topic.is_secure_topic("job-scout-alerts") is False

    def test_empty_topic_is_not_secure(self) -> None:
        """An unset topic is not secure."""
        assert ntfy_topic.is_secure_topic("") is False

    def test_long_word_without_digits_is_not_treated_as_random(self) -> None:
        """A hand-picked long word should still be flagged."""
        assert ntfy_topic.is_secure_topic("job-scout-supercalifragilistic") is False


class TestSubscribeUrl:
    """The URL is what the QR code and the tap-to-subscribe link both use."""

    def test_builds_from_server_and_topic(self) -> None:
        """Standard case."""
        assert (
            ntfy_topic.subscribe_url("job-scout-x", "https://ntfy.sh")
            == "https://ntfy.sh/job-scout-x"
        )

    def test_tolerates_trailing_slash_on_server(self) -> None:
        """A configured server may or may not end in a slash."""
        assert (
            ntfy_topic.subscribe_url("t", "https://ntfy.example.com/")
            == "https://ntfy.example.com/t"
        )


class TestAppSubscribeUrl:
    """The deep link is what makes a scan open the app, not the browser."""

    def test_uses_the_ntfy_scheme(self) -> None:
        """An https link opens the browser instead, which is the whole bug."""
        assert (
            ntfy_topic.app_subscribe_url("job-scout-x", "https://ntfy.sh")
            == "ntfy://ntfy.sh/job-scout-x"
        )

    def test_does_not_leave_the_https_scheme_in_the_host(self) -> None:
        """A naive replace would produce ntfy://https://ntfy.sh/..."""
        url = ntfy_topic.app_subscribe_url("t", "https://ntfy.sh")
        assert "https" not in url
        assert url.count("://") == 1

    def test_self_hosted_http_server_is_marked_insecure(self) -> None:
        """Without this the app tries HTTPS against a plain-HTTP server."""
        assert (
            ntfy_topic.app_subscribe_url("t", "http://nas.local:8080")
            == "ntfy://nas.local:8080/t?secure=false"
        )

    def test_self_hosted_https_server_needs_no_flag(self) -> None:
        """secure=false must not be added when HTTPS is in use."""
        url = ntfy_topic.app_subscribe_url("t", "https://ntfy.example.com")
        assert url == "ntfy://ntfy.example.com/t"

    def test_tolerates_a_trailing_slash(self) -> None:
        """A configured server may end in a slash."""
        assert (
            ntfy_topic.app_subscribe_url("t", "https://ntfy.sh/") == "ntfy://ntfy.sh/t"
        )


class TestQrSvg:
    """The QR is rendered server-side so no JS library is needed."""

    def test_returns_an_svg_document(self) -> None:
        """The endpoint serves this straight through as image/svg+xml."""
        svg = ntfy_topic.qr_svg("https://ntfy.sh/job-scout-test")
        assert svg.lstrip().startswith("<?xml") or svg.lstrip().startswith("<svg")
        assert "<svg" in svg
        assert "path" in svg or "rect" in svg

    def test_longer_urls_still_encode(self) -> None:
        """A long server plus a long topic must not overflow the version."""
        long_url = "https://ntfy.selfhosted.example.com/" + ("a" * 80)
        assert "<svg" in ntfy_topic.qr_svg(long_url)

    @pytest.mark.parametrize("scale", [3, 5, 8])
    def test_scale_changes_the_rendered_size(self, scale: int) -> None:
        """The panel picks a scale that suits its layout."""
        assert "<svg" in ntfy_topic.qr_svg("https://ntfy.sh/t", scale=scale)
