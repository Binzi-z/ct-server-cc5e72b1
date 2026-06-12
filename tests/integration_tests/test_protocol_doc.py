"""
Keeps ``doc/PROTOCOL.md`` honest.

The client-facing protocol document embeds example messages in fenced ``json``
code blocks. Blocks whose info string carries a ``name=<id>`` marker are
*verifiable*: this module extracts them and asserts they match what the live
in-process ``lobby_server`` actually sends. If a server message changes shape
without the doc being updated (or vice versa), these tests fail.

``test_doc_examples_are_valid_json`` needs no database and validates that every
``json`` block in the doc is syntactically valid JSON.
"""
import json
import re
from pathlib import Path

from tests.utils import fast_forward

from .conftest import (
    connect_and_sign_in,
    connect_client,
    get_session,
    perform_login,
    read_until_command
)

DOC_PATH = Path(__file__).parents[2] / "doc" / "PROTOCOL.md"

# Any ```json fenced block (info string after ```json is ignored here).
_ALL_JSON = re.compile(r"```json[^\n]*\n(.*?)```", re.DOTALL)
# Only ```json blocks tagged with `name=<id>` in their info string.
_NAMED_JSON = re.compile(r"```json\s+name=(\w+)\s*\n(.*?)```", re.DOTALL)


def _doc_text() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def _all_json_blocks() -> list[str]:
    return _ALL_JSON.findall(_doc_text())


def _named_examples() -> dict[str, dict]:
    return {
        name: json.loads(body)
        for name, body in _NAMED_JSON.findall(_doc_text())
    }


def test_doc_examples_are_valid_json():
    """Every ```json block in the doc must parse (no database needed)."""
    blocks = _all_json_blocks()
    assert blocks, f"No json examples found in {DOC_PATH}"
    for body in blocks:
        json.loads(body)


def test_doc_has_expected_named_examples():
    """The verifiable examples the live tests rely on must be present."""
    names = set(_named_examples())
    assert {
        "welcome_me",
        "social_rhiza",
        "party_invite",
        "update_party_two",
        "search_info_start",
        "search_info_stop",
        "error_invite_nonexistent",
    } <= names


async def test_doc_welcome_me(lobby_server):
    expected = _named_examples()["welcome_me"]

    proto = await connect_client(lobby_server)
    await get_session(proto)
    await perform_login(proto, ("Rhiza", "puff_the_magic_dragon"))

    msg = await read_until_command(proto, "welcome", timeout=120)
    assert msg["me"] == expected


async def test_doc_social(lobby_server):
    expected = _named_examples()["social_rhiza"]

    proto = await connect_client(lobby_server)
    await get_session(proto)
    await perform_login(proto, ("Rhiza", "puff_the_magic_dragon"))

    msg = await read_until_command(proto, "social", timeout=120)
    assert msg == expected


@fast_forward(60)
async def test_doc_party_invite_and_update(lobby_server):
    examples = _named_examples()

    test_id, _, proto = await connect_and_sign_in(
        ("test", "test_password"), lobby_server
    )
    rhiza_id, _, proto2 = await connect_and_sign_in(
        ("rhiza", "puff_the_magic_dragon"), lobby_server
    )

    await read_until_command(proto, "game_info")
    await read_until_command(proto2, "game_info")

    await proto.send_message({
        "command": "invite_to_party",
        "recipient_id": rhiza_id,
    })
    invite = await read_until_command(proto2, "party_invite")
    assert invite == examples["party_invite"]

    await proto2.send_message({
        "command": "accept_party_invite",
        "sender_id": test_id,
    })
    msg1 = await read_until_command(proto, "update_party")
    msg2 = await read_until_command(proto2, "update_party")
    assert msg1 == msg2
    assert msg1 == examples["update_party_two"]


@fast_forward(30)
async def test_doc_search_info(lobby_server):
    examples = _named_examples()

    _, _, proto = await connect_and_sign_in(("ladder1", "ladder1"), lobby_server)
    await read_until_command(proto, "game_info")

    await proto.send_message({
        "command": "game_matchmaking",
        "state": "start",
        "faction": "uef",
    })
    start = await read_until_command(proto, "search_info")
    assert start == examples["search_info_start"]

    await proto.send_message({
        "command": "game_matchmaking",
        "state": "stop",
    })
    stop = await read_until_command(proto, "search_info")
    assert stop == examples["search_info_stop"]


async def test_doc_invite_nonexistent_error(lobby_server):
    expected = _named_examples()["error_invite_nonexistent"]

    _, _, proto = await connect_and_sign_in(
        ("test", "test_password"), lobby_server
    )
    await read_until_command(proto, "game_info")

    await proto.send_message({
        "command": "invite_to_party",
        "recipient_id": 9999999,
    })
    msg = await read_until_command(proto, "notice")
    assert msg == expected
