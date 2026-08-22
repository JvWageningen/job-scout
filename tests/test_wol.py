"""Tests for waking the LLM host over the LAN."""

from __future__ import annotations

import socket

import pytest
import requests

from job_scout import wol


class _FakeSocket:
    """Records what would have been sent, instead of touching the network."""

    sent: list[tuple[bytes, tuple[str, int]]] = []
    opts: list[tuple[int, int, int]] = []

    def __enter__(self) -> _FakeSocket:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def setsockopt(self, level: int, opt: int, value: int) -> None:
        _FakeSocket.opts.append((level, opt, value))

    def sendto(self, payload: bytes, addr: tuple[str, int]) -> None:
        _FakeSocket.sent.append((payload, addr))


@pytest.fixture()
def fake_socket(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSocket]:
    """Replace socket.socket with a recorder."""
    _FakeSocket.sent = []
    _FakeSocket.opts = []
    monkeypatch.setattr(wol.socket, "socket", lambda *_a, **_kw: _FakeSocket())
    return _FakeSocket


class TestNormaliseMac:
    """MAC addresses arrive in several notations."""

    def test_colon_separated(self) -> None:
        """The common notation parses to six bytes."""
        assert wol.normalise_mac("2c:f0:5d:0e:b1:ef") == bytes.fromhex("2cf05d0eb1ef")

    def test_dash_separated_and_uppercase(self) -> None:
        """Windows-style notation parses identically."""
        assert wol.normalise_mac("2C-F0-5D-0E-B1-EF") == bytes.fromhex("2cf05d0eb1ef")

    def test_bare_hex(self) -> None:
        """An unseparated address is accepted."""
        assert wol.normalise_mac("2cf05d0eb1ef") == bytes.fromhex("2cf05d0eb1ef")

    def test_rejects_wrong_length(self) -> None:
        """A too-short address is an error, not a silent truncation."""
        with pytest.raises(ValueError, match="Not a valid MAC"):
            wol.normalise_mac("2c:f0:5d")

    def test_rejects_non_hex(self) -> None:
        """Non-hex characters are rejected."""
        with pytest.raises(ValueError, match="Not a valid MAC"):
            wol.normalise_mac("zz:f0:5d:0e:b1:ef")


class TestMagicPacket:
    """The packet must match the wake-on-LAN wire format exactly."""

    def test_payload_is_sync_plus_sixteen_repeats(
        self, fake_socket: type[_FakeSocket]
    ) -> None:
        """6x 0xFF followed by the MAC sixteen times."""
        wol.send_magic_packet("2c:f0:5d:0e:b1:ef")
        payload, _addr = fake_socket.sent[0]
        assert len(payload) == 6 + 6 * 16
        assert payload[:6] == b"\xff" * 6
        assert payload[6:] == bytes.fromhex("2cf05d0eb1ef") * 16

    def test_broadcast_option_is_enabled(self, fake_socket: type[_FakeSocket]) -> None:
        """Without SO_BROADCAST the send fails on most systems."""
        wol.send_magic_packet("2c:f0:5d:0e:b1:ef")
        assert (socket.SOL_SOCKET, socket.SO_BROADCAST, 1) in fake_socket.opts

    def test_uses_given_broadcast_and_port(
        self, fake_socket: type[_FakeSocket]
    ) -> None:
        """A subnet broadcast can be targeted explicitly."""
        wol.send_magic_packet("2c:f0:5d:0e:b1:ef", broadcast="192.168.1.255", port=7)
        assert fake_socket.sent[0][1] == ("192.168.1.255", 7)

    def test_bad_mac_raises_before_sending(
        self, fake_socket: type[_FakeSocket]
    ) -> None:
        """A malformed MAC must not produce a bogus packet."""
        with pytest.raises(ValueError, match="Not a valid MAC"):
            wol.send_magic_packet("nonsense")
        assert fake_socket.sent == []


class TestEndpointReady:
    """Readiness probing must never raise into the caller."""

    def test_true_on_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A 2xx response means ready."""
        monkeypatch.setattr(
            wol.requests, "get", lambda *_a, **_kw: type("R", (), {"ok": True})()
        )
        assert wol.endpoint_ready("http://x/v1/models") is True

    def test_false_on_error_status(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-2xx response means not ready."""
        monkeypatch.setattr(
            wol.requests, "get", lambda *_a, **_kw: type("R", (), {"ok": False})()
        )
        assert wol.endpoint_ready("http://x/v1/models") is False

    def test_false_on_connection_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A host that is asleep refuses the connection; that is not a crash."""

        def boom(*_a: object, **_kw: object) -> None:
            raise requests.ConnectionError("asleep")

        monkeypatch.setattr(wol.requests, "get", boom)
        assert wol.endpoint_ready("http://x/v1/models") is False


class TestWaitForEndpoint:
    """Polling stops as soon as the service answers, or at the deadline."""

    def test_returns_true_once_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Comes up on the third probe."""
        calls = {"n": 0}

        def probe(_url: str, *, timeout: float = 5.0) -> bool:
            calls["n"] += 1
            return calls["n"] >= 3

        monkeypatch.setattr(wol, "endpoint_ready", probe)
        monkeypatch.setattr(wol.time, "sleep", lambda _s: None)
        assert wol.wait_for_endpoint("http://x", timeout=60, interval=0) is True
        assert calls["n"] == 3

    def test_returns_false_at_deadline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A host that never comes up gives up rather than hanging forever."""
        monkeypatch.setattr(wol, "endpoint_ready", lambda *_a, **_kw: False)
        monkeypatch.setattr(wol.time, "sleep", lambda _s: None)
        assert wol.wait_for_endpoint("http://x", timeout=0, interval=0) is False


class TestWakeAndWait:
    """The convenience wrapper used before every scheduled run."""

    def test_skips_packet_when_already_up(
        self, monkeypatch: pytest.MonkeyPatch, fake_socket: type[_FakeSocket]
    ) -> None:
        """No point waking a machine that is already awake."""
        monkeypatch.setattr(wol, "endpoint_ready", lambda *_a, **_kw: True)
        assert wol.wake_and_wait("2c:f0:5d:0e:b1:ef", "http://x") is True
        assert fake_socket.sent == []

    def test_sends_packet_then_waits(
        self, monkeypatch: pytest.MonkeyPatch, fake_socket: type[_FakeSocket]
    ) -> None:
        """A sleeping host gets a packet and then a wait."""
        monkeypatch.setattr(wol, "endpoint_ready", lambda *_a, **_kw: False)
        monkeypatch.setattr(wol, "wait_for_endpoint", lambda *_a, **_kw: True)
        assert wol.wake_and_wait("2c:f0:5d:0e:b1:ef", "http://x") is True
        assert len(fake_socket.sent) == 1

    def test_bad_mac_reports_failure_without_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A misconfigured MAC must not abort the scheduled run."""
        monkeypatch.setattr(wol, "endpoint_ready", lambda *_a, **_kw: False)
        assert wol.wake_and_wait("nonsense", "http://x") is False
