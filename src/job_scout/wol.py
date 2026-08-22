"""Wake the machine hosting the LLM, then wait for it to answer.

When job-scout runs on a NAS the model server usually lives somewhere else --
a workstation with a GPU that sleeps between runs. A scheduled run therefore
has to send a wake-on-LAN magic packet first and wait for the model server to
start accepting requests, otherwise every LLM call in the pipeline fails
against a host that was simply asleep.

Waking is best-effort by design: if the host is already up the packet is
harmless, and if it never comes up the caller decides whether to run anyway.
"""

from __future__ import annotations

import re
import socket
import time

import requests
from loguru import logger

# A magic packet is 6 bytes of 0xFF followed by the target MAC repeated 16x.
_SYNC_BYTES = b"\xff" * 6
_MAC_REPEATS = 16

# Port 9 (discard) is the conventional wake-on-LAN destination; 7 also works.
DEFAULT_WOL_PORT = 9
DEFAULT_BROADCAST = "255.255.255.255"

_MAC_SEPARATORS = re.compile(r"[:\-.]")
_MAC_HEX = re.compile(r"^[0-9a-fA-F]{12}$")


def normalise_mac(mac: str) -> bytes:
    """Convert a MAC address in any common notation to raw bytes.

    Args:
        mac: MAC address, e.g. ``2c:f0:5d:0e:b1:ef``, ``2C-F0-5D-0E-B1-EF``
            or ``2cf05d0eb1ef``.

    Returns:
        The six address bytes.

    Raises:
        ValueError: If the address is not twelve hex digits.
    """
    stripped = _MAC_SEPARATORS.sub("", mac.strip())
    if not _MAC_HEX.match(stripped):
        raise ValueError(f"Not a valid MAC address: {mac!r}")
    return bytes.fromhex(stripped)


def send_magic_packet(
    mac: str,
    *,
    broadcast: str = DEFAULT_BROADCAST,
    port: int = DEFAULT_WOL_PORT,
) -> None:
    """Send a wake-on-LAN magic packet to a MAC address.

    The packet is a UDP broadcast, so this only works on the same layer-2
    network as the target -- it cannot be routed over a VPN or the internet.

    Args:
        mac: Target MAC address.
        broadcast: Broadcast address to send to. Prefer the subnet broadcast
            (e.g. ``192.168.1.255``) when the container has host networking;
            some networks drop the all-ones address.
        port: UDP port; 9 by convention.

    Raises:
        ValueError: If the MAC address is malformed.
        OSError: If the packet could not be sent.
    """
    payload = _SYNC_BYTES + normalise_mac(mac) * _MAC_REPEATS
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(payload, (broadcast, port))
    logger.info(f"Sent wake-on-LAN packet to {mac} via {broadcast}:{port}")


def endpoint_ready(url: str, *, timeout: float = 5.0) -> bool:
    """Check whether an HTTP endpoint answers successfully.

    Args:
        url: URL to probe.
        timeout: Per-request timeout in seconds.

    Returns:
        True if the endpoint returned a 2xx response.
    """
    try:
        resp = requests.get(url, timeout=timeout)
    except requests.RequestException:
        return False
    return resp.ok


def wait_for_endpoint(
    url: str,
    *,
    timeout: float = 300.0,
    interval: float = 10.0,
    probe_timeout: float = 5.0,
) -> bool:
    """Poll an endpoint until it answers or the deadline passes.

    Args:
        url: URL to probe.
        timeout: Total seconds to keep trying before giving up.
        interval: Seconds between attempts.
        probe_timeout: Per-request timeout in seconds.

    Returns:
        True if the endpoint answered before the deadline.
    """
    deadline = time.monotonic() + timeout
    attempt = 0
    while True:
        attempt += 1
        if endpoint_ready(url, timeout=probe_timeout):
            logger.info(f"{url} answered after {attempt} attempt(s)")
            return True
        if time.monotonic() >= deadline:
            logger.warning(f"{url} did not answer within {timeout:.0f}s")
            return False
        time.sleep(interval)


def wake_and_wait(
    mac: str,
    url: str,
    *,
    broadcast: str = DEFAULT_BROADCAST,
    port: int = DEFAULT_WOL_PORT,
    timeout: float = 300.0,
    interval: float = 10.0,
) -> bool:
    """Wake a host and wait for its service to come up.

    Skips the packet entirely when the endpoint already answers, so this is
    cheap to call before every run.

    Args:
        mac: Target MAC address.
        url: URL that indicates the service is ready.
        broadcast: Broadcast address for the magic packet.
        port: UDP port for the magic packet.
        timeout: Total seconds to wait for the service after waking.
        interval: Seconds between readiness probes.

    Returns:
        True if the service is reachable.
    """
    if endpoint_ready(url):
        logger.info(f"{url} is already up — no wake needed")
        return True

    logger.info(f"{url} is down; waking {mac}")
    try:
        send_magic_packet(mac, broadcast=broadcast, port=port)
    except (ValueError, OSError) as exc:
        logger.error(f"Could not send wake-on-LAN packet: {exc}")
        return False

    return wait_for_endpoint(url, timeout=timeout, interval=interval)
