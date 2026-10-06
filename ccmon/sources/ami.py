"""Asterisk AMI: packet parsing + a read-only socket client.

Only read-only actions are ever sent: Login, QueueStatus, CoreShowChannels,
PJSIPShowEndpoints / SIPshowpeers, Logoff. Originate/Command are deliberately
absent (least privilege, see docs/security.md).
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

Packet = dict[str, Any]


def parse_packets(raw: str) -> list[Packet]:
    """Split an AMI response body into packets on blank lines.

    Keys are lowercased. Repeated keys inside one packet become lists.
    """
    packets: list[Packet] = []
    current: Packet = {}
    last_key: str | None = None
    for line in raw.replace("\r\n", "\n").split("\n"):
        if not line.strip():
            if current:
                packets.append(current)
                current = {}
                last_key = None
            continue
        if line[0] in (" ", "\t") and last_key is not None:
            prev = current[last_key]
            if isinstance(prev, list):
                prev[-1] = f"{prev[-1]} {line.strip()}"
            else:
                current[last_key] = f"{prev} {line.strip()}"
            continue
        if ":" not in line:
            last_key = None
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key in current:
            existing = current[key]
            if not isinstance(existing, list):
                current[key] = [existing]
            current[key].append(value)
        else:
            current[key] = value
        last_key = key
    if current:
        packets.append(current)
    return packets


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def extract_queue_summaries(packets: list[Packet]) -> list[dict]:
    """Queue waiting/active counts from QueueStatus response packets."""
    out = []
    for p in packets:
        event = str(p.get("event", "")).lower()
        if event not in ("queue", "queuesummary", "status"):
            continue
        if "queue" not in p or "calls" not in p:
            continue
        out.append(
            {
                "queue": str(p["queue"]),
                "calls": _as_int(p.get("calls")),
                "calls_taken": _as_int(p.get("callstaken")),
                "strategy": str(p.get("strategy", "")),
            }
        )
    return out


def extract_queue_members(packets: list[Packet]) -> list[dict]:
    """Member paused flags from QueueStatus response packets."""
    out = []
    for p in packets:
        if str(p.get("event", "")).lower() != "queuemember":
            continue
        paused_raw = str(p.get("paused", "")).lower()
        out.append(
            {
                "queue": str(p.get("queue", "")),
                "name": str(p.get("name", "")),
                "paused": paused_raw in ("1", "yes", "true"),
            }
        )
    return out


def extract_channels(packets: list[Packet]) -> list[dict]:
    """Live channels from CoreShowChannels response packets."""
    out = []
    for p in packets:
        event = str(p.get("event", "")).lower()
        if event not in ("coreshowchannel", "dial", "newchannel"):
            continue
        channel = str(p.get("channel", ""))
        if not channel:
            continue
        out.append(
            {
                "channel": channel,
                "state": str(p.get("channelstatedesc", p.get("state", ""))),
                "exten": str(p.get("exten", "")),
                "caller": str(p.get("calleridnum", p.get("callerid", ""))),
                "application": str(p.get("application", "")),
                "data": str(p.get("data", "")),
            }
        )
    return out


_NEGATIVE = (
    "unregistered", "unregist", "failed", "nonexistent", "unreachable",
    "rejected", "expired", "unavailable", "no contact",
)
_POSITIVE = ("registered", "online", "reachable", "ok", "success")


def classify_registration(status_text: str) -> str:
    s = status_text.lower()
    if any(neg in s for neg in _NEGATIVE):
        return "unregistered"
    if any(pos in s for pos in _POSITIVE):
        return "registered"
    return "unknown"


_PEER_KEYS = ("endpoint", "objectname", "object", "peer", "name", "mailbox")
_STATUS_KEYS = ("status", "registration", "state", "reachability")


def extract_trunks(packets: list[Packet]) -> list[dict]:
    """Peer registration state from PJSIPShowEndpoints / SIPshowpeers replies.

    Field names differ across Asterisk versions; we accept the known aliases
    and skip packets that do not look like a peer+status pair.
    """
    out: dict[str, dict] = {}
    for p in packets:
        event = str(p.get("event", "")).lower()
        if not event or event.endswith("complete") or event in ("response", "success"):
            continue
        peer = next((str(p[k]) for k in _PEER_KEYS if k in p), "")
        status_text = next((str(p[k]) for k in _STATUS_KEYS if k in p), "")
        if not peer or not status_text or isinstance(peer, list):
            continue
        if "/" in peer and len(peer) > 64:  # looks like a channel, not a peer
            continue
        out[peer] = {
            "peer": peer,
            "status": classify_registration(status_text),
            "detail": status_text[:120],
            "tech": (
                "PJSIP"
                if "pjsip" in str(p.get("channeltype", "")).lower() or "pjsip" in peer.lower()
                else "SIP"
            ),
        }
    return list(out.values())


class AmiError(RuntimeError):
    """AMI failure with a token-free message."""


class AmiClient:
    """Synchronous, read-only AMI client (call from a worker thread)."""

    def __init__(self, settings) -> None:
        self._settings = settings
        self._sock = None
        self._buffer = b""
        self._action_seq = 0

    # -- low level ---------------------------------------------------------
    def _send(self, pairs: dict[str, str]) -> None:
        assert self._sock is not None
        payload = "".join(f"{k}: {v}\r\n" for k, v in pairs.items()) + "\r\n"
        self._sock.sendall(payload.encode("utf-8"))

    def _next_packets(self, deadline: float) -> list[Packet]:
        """Return newly completed packets from the socket (buffered)."""
        assert self._sock is not None
        import time as _time

        packets: list[Packet] = []
        while _time.monotonic() < deadline:
            idx = self._buffer.find(b"\r\n\r\n")
            if idx >= 0:
                block = self._buffer[:idx]
                self._buffer = self._buffer[idx + 4 :]
                packets.extend(parse_packets(block.decode("utf-8", "replace")))
                return packets
            try:
                chunk = self._sock.recv(65536)
            except (TimeoutError, OSError):
                break
            if not chunk:
                break
            self._buffer += chunk
        return packets

    def _stopped(self, packet: Packet, stop: str | None) -> bool:
        event = str(packet.get("event", "")).lower()
        if stop is None:
            return str(packet.get("response", "")).lower() == "success"
        if stop == "*complete":
            return event.endswith("complete")
        return event == stop.lower()

    def _request(self, action: str, params: dict[str, str], stop: str | None) -> list[Packet]:
        import time

        assert self._sock is not None
        self._action_seq += 1
        action_id = f"ccmon-{self._action_seq}"
        self._send({"Action": action, "ActionID": action_id, **params})
        deadline = time.monotonic() + self._settings.ami_timeout
        collected: list[Packet] = []
        done = False
        while time.monotonic() < deadline and not done:
            for packet in self._next_packets(deadline):
                collected.append(packet)
                response = str(packet.get("response", "")).lower()
                if response and response != "success":
                    message = str(packet.get("message", ""))[:160]
                    raise AmiError(f"AMI {action} rejected: {message or response}")
                if self._stopped(packet, stop):
                    done = True
                    break
        if not done:
            raise AmiError(f"AMI {action} timed out or incomplete")
        return collected

    def connect(self) -> None:
        import socket
        import time

        if not self._settings.ami_enabled:
            raise AmiError("AMI disabled in configuration")
        try:
            self._sock = socket.create_connection(
                (self._settings.ami_host, self._settings.ami_port),
                timeout=self._settings.ami_timeout,
            )
            self._sock.settimeout(self._settings.ami_timeout)
        except OSError as exc:
            raise AmiError(f"AMI connect failed: {exc}") from None
        self._buffer = b""
        # Consume the server greeting if it already arrived.
        self._next_packets(time.monotonic() + 0.2)
        self._request(
            "Login",
            {
                "Username": self._settings.ami_username,
                "Secret": self._settings.ami_secret.get_secret_value(),
                "Events": "off",
            },
            None,
        )

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            self._send({"Action": "Logoff"})
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass
        self._sock = None
        self._buffer = b""

    def __enter__(self) -> AmiClient:
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- read-only operations --------------------------------------------
    def snapshot(self) -> dict:
        """Queues + members + channels + trunk state in one connection."""
        out: dict = {"queues": [], "members": [], "channels": [], "trunks": [], "error": ""}
        with self:
            queue_packets = self._request("QueueStatus", {}, "*complete")
            out["queues"] = extract_queue_summaries(queue_packets)
            out["members"] = extract_queue_members(queue_packets)
            out["channels"] = extract_channels(self._request("CoreShowChannels", {}, "*complete"))
            try:
                out["trunks"] = extract_trunks(
                    self._request("PJSIPShowEndpoints", {}, "*complete")
                )
            except AmiError:
                try:
                    out["trunks"] = extract_trunks(self._request("SIPshowpeers", {}, "*complete"))
                except AmiError as exc:
                    out["error"] = str(exc)
        return out
