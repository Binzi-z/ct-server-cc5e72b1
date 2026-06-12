"""
Message recorder for capturing protocol transcripts during integration tests.

The `MessageRecorder` wraps a `Protocol` instance and transparently records
all messages sent and received. Recorded transcripts can be serialized to JSON
golden files and later embedded into the protocol documentation.

Usage::

    proto = await connect_client(lobby_server)
    recorder = MessageRecorder(proto, "login_flow")
    # Use recorder just like proto
    await recorder.send_message({"command": "ask_session", ...})
    msg = await recorder.read_message()
    # ...
    transcript = recorder.to_transcript()
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from server.protocol import Protocol

# Directory where golden transcript JSON files are stored
TRANSCRIPT_DIR = Path(__file__).parent


class MessageRecorder:
    """
    Transparent wrapper around a `Protocol` that records all messages.

    Implements the subset of the `Protocol` interface used by test helpers
    (`send_message`, `read_message`, `close`, `is_connected`, etc.),
    forwarding every call to the underlying protocol while appending to an
    internal message log.
    """

    def __init__(self, proto: Protocol, label: str) -> None:
        self.proto = proto
        self.label = label
        self._messages: list[dict[str, Any]] = []
        self._start_time = time.monotonic()

    # ---- Protocol-compatible interface ----

    async def send_message(self, message: dict) -> None:
        self._record("send", message)
        await self.proto.send_message(message)

    async def read_message(self, timeout: float = 60) -> dict:
        msg = await self.proto.read_message()
        self._record("recv", msg)
        return msg

    def is_connected(self) -> bool:
        return self.proto.is_connected()

    async def close(self) -> None:
        await self.proto.close()

    @property
    def reader(self):
        return self.proto.reader

    @property
    def writer(self):
        return self.proto.writer

    # ---- Recording helpers ----

    def _record(self, direction: str, message: dict) -> None:
        elapsed = round(time.monotonic() - self._start_time, 4)
        self._messages.append({
            "t": elapsed,
            "direction": direction,
            "message": _normalize_message(message),
        })

    def clear(self) -> None:
        """Discard all recorded messages and reset the timer."""
        self._messages.clear()
        self._start_time = time.monotonic()

    # ---- Transcript serialization ----

    def to_transcript(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "messages": list(self._messages),
        }

    def save_transcript(self, path: Path | None = None) -> Path:
        """
        Write the transcript to a JSON file.

        If *path* is ``None``, writes to
        ``TRANSCRIPT_DIR / f"{self.label}.json"``.
        """
        path = path or (TRANSCRIPT_DIR / f"{self.label}.json")
        data = self.to_transcript()
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path

    @staticmethod
    def load_transcript(label: str, path: Path | None = None) -> dict:
        """Load a golden transcript from disk."""
        path = path or (TRANSCRIPT_DIR / f"{label}.json")
        return json.loads(path.read_text(encoding="utf-8"))

    def compare_with_golden(self, golden: dict) -> list[str]:
        """
        Compare the recorded transcript with a golden file.

        Returns a list of human-readable diff strings. Empty list means
        the transcripts match.
        """
        diffs: list[str] = []
        recorded = self._messages
        expected = golden.get("messages", [])

        if len(recorded) != len(expected):
            diffs.append(
                f"Message count mismatch: recorded {len(recorded)}, "
                f"expected {len(expected)}"
            )

        for i, (rec, exp) in enumerate(zip(recorded, expected)):
            rec_msg = rec.get("message")
            exp_msg = exp.get("message")
            rec_dir = rec.get("direction")
            exp_dir = exp.get("direction")

            if rec_dir != exp_dir:
                diffs.append(
                    f"[{i}] direction mismatch: {rec_dir!r} != {exp_dir!r}"
                )
            if rec_msg != exp_msg:
                diffs.append(
                    f"[{i}] message mismatch:\n"
                    f"  recorded: {json.dumps(rec_msg, sort_keys=True)}\n"
                    f"  expected: {json.dumps(exp_msg, sort_keys=True)}"
                )

        return diffs


def _normalize_message(message: dict) -> dict:
    """
    Normalize a message for stable JSON comparison.

    Strips volatile fields that change between test runs (timestamps,
    UUIDs, etc.) and sorts keys for deterministic output.
    """
    normalized = {}
    for key in sorted(message.keys()):
        value = message[key]
        # Replace volatile fields with placeholders
        if key == "current_time":
            value = "<CURRENT_TIME>"
        elif key == "session" and isinstance(value, str) and len(value) > 20:
            value = "<SESSION_ID>"
        normalized[key] = value
    return normalized
