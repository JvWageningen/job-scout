"""Generating and sharing an ntfy topic that is not trivially guessable.

An ntfy topic is the whole access control: anyone who knows the name receives
the notifications, and anyone can publish to it. A memorable topic like
"job-scout-alerts" is therefore effectively public -- it collides with other
people's topics and can be found by guessing.

Topics here are ``job-scout-<name>-<random>``: readable enough to recognise in
the phone app, with enough entropy on the end that guessing it is hopeless.
"""

from __future__ import annotations

import io
import re
import secrets

import segno

# ntfy accepts [A-Za-z0-9_-] in topic names.
_ALLOWED = re.compile(r"[^a-z0-9]+")
_SUFFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"

# 16 chars from a 36-symbol alphabet is ~82 bits: far past guessable, while
# still short enough to read off a screen if someone has to type it.
SUFFIX_LENGTH = 16

PREFIX = "job-scout"


def slugify_name(name: str) -> str:
    """Reduce a person's name to the readable part of a topic.

    Args:
        name: User name, which may contain spaces, accents or punctuation.

    Returns:
        A lowercase hyphen-free slug, or an empty string if nothing survives.
    """
    slug = _ALLOWED.sub("-", name.strip().lower()).strip("-")
    return slug.replace("--", "-")


def random_suffix(length: int = SUFFIX_LENGTH) -> str:
    """Return a cryptographically random lowercase-alphanumeric string.

    Args:
        length: Number of characters to generate.

    Returns:
        The random suffix.
    """
    return "".join(secrets.choice(_SUFFIX_ALPHABET) for _ in range(length))


def generate_topic(name: str) -> str:
    """Build a secure, recognisable ntfy topic for a user.

    Args:
        name: User name to embed in the topic.

    Returns:
        A topic of the form ``job-scout-<name>-<random>``. The name is
        omitted when it slugifies to nothing, rather than leaving a double
        hyphen in the middle.
    """
    slug = slugify_name(name)
    parts = [PREFIX, slug, random_suffix()] if slug else [PREFIX, random_suffix()]
    return "-".join(parts)


def is_secure_topic(topic: str) -> bool:
    """Report whether a topic looks randomised rather than hand-picked.

    Used to warn in the dashboard when the configured topic is still a
    guessable default.

    Args:
        topic: The configured topic.

    Returns:
        True if the topic ends in a long random-looking segment.
    """
    if not topic:
        return False
    last = topic.rsplit("-", 1)[-1]
    if len(last) < SUFFIX_LENGTH:
        return False
    # A real word of this length is possible but a digit somewhere is not,
    # and the generator's alphabet makes one overwhelmingly likely.
    return bool(re.fullmatch(r"[a-z0-9]+", last)) and any(c.isdigit() for c in last)


def subscribe_url(topic: str, server: str = "https://ntfy.sh") -> str:
    """Return the URL a phone should open to subscribe to the topic.

    Args:
        topic: The ntfy topic.
        server: Base URL of the ntfy server.

    Returns:
        The topic URL. Opening it on a phone with the ntfy app installed
        offers to subscribe; otherwise it opens the web client.
    """
    return f"{server.rstrip('/')}/{topic}"


def app_subscribe_url(topic: str, server: str = "https://ntfy.sh") -> str:
    """Return the deep link that opens the ntfy app straight at the topic.

    ntfy documents ``ntfy://<host>/<topic>`` for this and recommends it over
    an https link, because Android's http/https deep linking is unreliable --
    an https URL usually just opens the browser instead of the app.

    Args:
        topic: The ntfy topic.
        server: Base URL of the ntfy server.

    Returns:
        An ``ntfy://`` deep link. Self-hosted servers reached over plain HTTP
        get ``?secure=false`` appended, which is how the app is told not to
        upgrade the connection.
    """
    trimmed = server.rstrip("/")
    insecure = trimmed.startswith("http://")
    host = trimmed.removeprefix("https://").removeprefix("http://")
    url = f"ntfy://{host}/{topic}"
    return f"{url}?secure=false" if insecure else url


def qr_svg(url: str, *, scale: int = 5, dark: str = "#1f2d3d") -> str:
    """Render a URL as an inline SVG QR code.

    SVG rather than PNG so it stays sharp on any screen and can be styled by
    the page. The background is left transparent so it sits on whatever the
    panel uses.

    Args:
        url: The URL to encode.
        scale: Pixels per QR module.
        dark: Colour for the dark modules.

    Returns:
        The SVG document as a string.
    """
    # Error correction 'm' tolerates ~15% damage, which is plenty for a screen
    # and keeps the code sparse enough to scan quickly from a phone.
    qr = segno.make(url, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=scale, dark=dark, light=None, border=2)
    return buf.getvalue().decode("utf-8")
