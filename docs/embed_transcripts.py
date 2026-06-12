#!/usr/bin/env python3
"""
Embed verified transcript JSON files into the protocol flow documentation.

Usage::

    python docs/embed_transcripts.py

This script reads golden transcript files from
``tests/integration_tests/transcripts/*.json`` and injects them into
``docs/protocol-flow.md`` at positions marked with HTML comments::

    <!-- transcript: login -->
    ```json
    { ... auto-generated content ... }
    ```
    <!-- /transcript -->

Run this after generating transcripts with::

    pytest tests/integration_tests/test_protocol_docs.py --generate-transcripts

In CI, verify that the documentation is up-to-date with::

    python docs/embed_transcripts.py
    git diff --exit-code docs/protocol-flow.md
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Project root
ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPT_DIR = ROOT / "tests" / "integration_tests" / "transcripts"
DOC_FILE = ROOT / "docs" / "protocol-flow.md"

# Regex to find transcript markers in the markdown
MARKER_RE = re.compile(
    r"(<!--\s*transcript:\s*(\S+)\s*-->)\s*"
    r"(```json\n.*?```)"
    r"\s*(<!--\s*/transcript\s*-->)",
    re.DOTALL,
)


def load_transcript(label: str) -> dict | None:
    """Load a golden transcript JSON file by label."""
    path = TRANSCRIPT_DIR / f"{label}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def format_transcript(data: dict) -> str:
    """
    Format a transcript as a readable JSON code block.

    The output includes directional arrows (-> / <-) to make the
    client/server exchange easy to follow.
    """
    lines = []
    for entry in data.get("messages", []):
        direction = entry.get("direction", "?")
        message = entry.get("message", {})
        arrow = "->" if direction == "send" else "<-"
        command = message.get("command", "?")
        lines.append(f"{arrow} {command}")
        # Pretty-print the full message
        lines.append(json.dumps(message, indent=2, ensure_ascii=False))
        lines.append("")

    return "\n".join(lines).rstrip()


def embed_transcripts(doc_text: str) -> str:
    """
    Replace all transcript blocks in the document with current golden file
    content.
    """
    missing = []

    def _replace(match):
        open_marker = match.group(1)
        label = match.group(2)
        close_marker = match.group(4)

        data = load_transcript(label)
        if data is None:
            missing.append(label)
            # Leave the existing block unchanged
            return match.group(0)

        formatted = format_transcript(data)
        return (
            f"{open_marker}\n"
            f"```json\n"
            f"{formatted}\n"
            f"```\n"
            f"{close_marker}"
        )

    result = MARKER_RE.sub(_replace, doc_text)

    if missing:
        print(
            f"WARNING: Missing transcript files for: {', '.join(missing)}",
            file=sys.stderr,
        )
        print(
            "Run: pytest tests/integration_tests/test_protocol_docs.py "
            "--generate-transcripts",
            file=sys.stderr,
        )

    return result


def main():
    if not DOC_FILE.exists():
        print(f"ERROR: {DOC_FILE} does not exist", file=sys.stderr)
        sys.exit(1)

    doc_text = DOC_FILE.read_text(encoding="utf-8")
    updated = embed_transcripts(doc_text)

    if updated == doc_text:
        print("No changes needed.")
    else:
        DOC_FILE.write_text(updated, encoding="utf-8")
        print(f"Updated {DOC_FILE}")


if __name__ == "__main__":
    main()
