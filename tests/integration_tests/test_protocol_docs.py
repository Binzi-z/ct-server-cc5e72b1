"""
Protocol documentation tests.

This module serves two purposes:

1. **Transcript tests** — run complete protocol flows while recording every
   message exchanged, then either write the recording to a golden JSON file
   (``--generate-transcripts``) or compare against an existing golden file.
   The golden files are consumed by ``docs/embed_transcripts.py`` to inject
   verified examples into ``docs/protocol-flow.md``.

2. **Behavioral tests** — verify specific edge-case behaviours that are easy
   to get wrong when implementing a client: silent ignores, disconnect
   cleanup, duplicate request handling, etc.

Running::

    # Verify transcripts match golden files (default in CI)
    pytest tests/integration_tests/test_protocol_docs.py -v

    # Regenerate golden transcript files
    pytest tests/integration_tests/test_protocol_docs.py --generate-transcripts -v
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

import pytest

from server.protocol import Protocol
from tests.utils import fast_forward

from .conftest import (
    connect_and_sign_in,
    connect_client,
    perform_login,
    read_until,
    read_until_command,
)
from .transcripts.recorder import MessageRecorder

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _drain_until(proto, command, timeout=10):
    """Read messages until *command* is found, returning all read messages."""
    collected = []
    while True:
        msg = await asyncio.wait_for(proto.read_message(), timeout=timeout)
        collected.append(msg)
        if msg.get("command") == command:
            return collected


async def _open_fa(proto):
    """Simulate the FA game process opening (Idle -> Lobby)."""
    await proto.send_message({
        "target": "game",
        "command": "GameState",
        "args": ["Idle"],
    })
    await proto.send_message({
        "target": "game",
        "command": "GameState",
        "args": ["Lobby"],
    })


async def _host_game(proto, *, title="Test Game", mod="faf", visibility="public"):
    """Send game_host and wait for game_launch. Return the game uid."""
    await proto.send_message({
        "command": "game_host",
        "mod": mod,
        "visibility": visibility,
        "title": title,
    })
    msg = await read_until_command(proto, "game_launch")
    return int(msg["uid"])


async def _join_game(proto, uid):
    """Send game_join and wait for game_launch."""
    await proto.send_message({
        "command": "game_join",
        "uid": uid,
    })
    await read_until_command(proto, "game_launch", timeout=10)


def _assert_or_generate(recorder: MessageRecorder, generate: bool, label: str):
    """Either generate a transcript or compare against the golden file."""
    if generate:
        path = recorder.save_transcript()
        log.info("Generated transcript: %s", path)
    else:
        golden = recorder.load_transcript(label)
        diffs = recorder.compare_with_golden(golden)
        assert not diffs, (
            f"Transcript mismatch for '{label}':\n" + "\n".join(diffs)
            + "\n\nRun with --generate-transcripts to update golden files."
        )


# ---------------------------------------------------------------------------
# Transcript tests — each test records a flow and compares with golden file
# ---------------------------------------------------------------------------

@fast_forward(30)
async def test_transcript_login(lobby_server, generate_transcripts, fixed_time):
    """Record the full login sequence: ask_session -> hello -> welcome."""
    proto = await connect_client(lobby_server)
    rec = MessageRecorder(proto, "login")

    # ask_session
    await rec.send_message({
        "command": "ask_session",
        "user_agent": "faf-client",
        "version": "0.11.16",
    })
    session_msg = await rec.read_message()
    assert session_msg["command"] == "session"

    # hello (login)
    pw_hash = hashlib.sha256(b"puff_the_magic_dragon").hexdigest()
    await rec.send_message({
        "command": "hello",
        "version": "1.0.0-dev",
        "user_agent": "faf-client",
        "login": "Rhiza",
        "password": pw_hash,
        "unique_id": "some_id",
    })

    # welcome
    welcome = await rec.read_message()
    assert welcome["command"] == "welcome"

    # player_info
    player_info = await rec.read_message()
    assert player_info["command"] == "player_info"

    # social
    social = await rec.read_message()
    assert social["command"] == "social"

    _assert_or_generate(rec, generate_transcripts, "login")


@fast_forward(30)
async def test_transcript_login_duplicate(lobby_server, generate_transcripts, fixed_time):
    """Record what happens when the same account logs in twice."""
    # First login
    proto1 = await connect_client(lobby_server)
    rec1 = MessageRecorder(proto1, "login_duplicate_old")
    await rec1.send_message({
        "command": "ask_session",
        "user_agent": "faf-client",
        "version": "0.11.16",
    })
    await rec1.read_message()  # session
    pw_hash = hashlib.sha256(b"puff_the_magic_dragon").hexdigest()
    await rec1.send_message({
        "command": "hello",
        "version": "1.0.0-dev",
        "user_agent": "faf-client",
        "login": "Rhiza",
        "password": pw_hash,
        "unique_id": "some_id",
    })
    await rec1.read_message()  # welcome
    await rec1.read_message()  # player_info
    await rec1.read_message()  # social

    # Second login (same account)
    proto2 = await connect_client(lobby_server)
    rec2 = MessageRecorder(proto2, "login_duplicate_new")
    await rec2.send_message({
        "command": "ask_session",
        "user_agent": "faf-client",
        "version": "0.11.16",
    })
    await rec2.read_message()  # session
    await rec2.send_message({
        "command": "hello",
        "version": "1.0.0-dev",
        "user_agent": "faf-client",
        "login": "Rhiza",
        "password": pw_hash,
        "unique_id": "some_id",
    })
    await rec2.read_message()  # welcome
    await rec2.read_message()  # player_info
    await rec2.read_message()  # social

    # The old connection should receive a kick notice
    kick_msg = await asyncio.wait_for(rec1.read_message(), timeout=10)
    assert kick_msg["command"] == "notice"
    assert kick_msg["style"] == "kick"

    _assert_or_generate(rec1, generate_transcripts, "login_duplicate_old")
    _assert_or_generate(rec2, generate_transcripts, "login_duplicate_new")


@fast_forward(30)
async def test_transcript_game_host(lobby_server, generate_transcripts, fixed_time):
    """Record the game hosting flow end-to-end."""
    _, _, proto = await connect_and_sign_in(
        ("test", "test_password"), lobby_server
    )
    rec = MessageRecorder(proto, "game_host")
    await read_until_command(rec, "game_info")

    # Host a game
    await rec.send_message({
        "command": "game_host",
        "mod": "faf",
        "visibility": "public",
        "title": "My Test Game",
    })
    launch = await rec.read_message()
    assert launch["command"] == "game_launch"

    # Open FA
    await _open_fa(rec)

    # Should receive HostGame
    host_game_msg = await read_until_command(rec, "HostGame", target="game")
    assert host_game_msg["target"] == "game"

    # game_info broadcast should arrive with the new game
    game_info = await read_until_command(rec, "game_info")
    assert game_info["host"] == "test"

    _assert_or_generate(rec, generate_transcripts, "game_host")


@fast_forward(30)
async def test_transcript_game_join_success(
    lobby_server, generate_transcripts, fixed_time
):
    """Record a successful game join flow."""
    # Host creates a game
    host_id, _, host_proto = await connect_and_sign_in(
        ("test", "test_password"), lobby_server
    )
    await read_until_command(host_proto, "game_info")
    game_id = await _host_game(host_proto)
    await _open_fa(host_proto)
    await read_until_command(host_proto, "HostGame", target="game")
    await read_until_command(host_proto, "game_info")

    # Guest joins
    guest_id, _, guest_proto = await connect_and_sign_in(
        ("Rhiza", "puff_the_magic_dragon"), lobby_server
    )
    rec = MessageRecorder(guest_proto, "game_join_success")
    await read_until_command(rec, "game_info")

    await rec.send_message({
        "command": "game_join",
        "uid": game_id,
    })
    launch = await rec.read_message()
    assert launch["command"] == "game_launch"

    await _open_fa(rec)

    _assert_or_generate(rec, generate_transcripts, "game_join_success")


@fast_forward(30)
async def test_transcript_game_join_not_found(
    lobby_server, generate_transcripts, fixed_time
):
    """Record what happens when joining a non-existent game."""
    _, _, proto = await connect_and_sign_in(
        ("test", "test_password"), lobby_server
    )
    rec = MessageRecorder(proto, "game_join_not_found")
    await read_until_command(rec, "game_info")

    await rec.send_message({
        "command": "game_join",
        "uid": 999999,
    })

    # Should get game_join_failed
    msg = await rec.read_message()
    assert msg["command"] == "game_join_failed"
    assert msg["reason"] == "host_left_game"

    # Deprecated notice also sent
    notice = await rec.read_message()
    assert notice["command"] == "notice"

    _assert_or_generate(rec, generate_transcripts, "game_join_not_found")


@fast_forward(30)
async def test_transcript_game_join_bad_password(
    lobby_server, generate_transcripts, fixed_time
):
    """Record what happens when joining with wrong password."""
    # Host creates password-protected game
    host_id, _, host_proto = await connect_and_sign_in(
        ("test", "test_password"), lobby_server
    )
    await read_until_command(host_proto, "game_info")
    await host_proto.send_message({
        "command": "game_host",
        "mod": "faf",
        "visibility": "public",
        "title": "Private Game",
        "password": "secret123",
    })
    game_id_msg = await read_until_command(host_proto, "game_launch")
    game_id = int(game_id_msg["uid"])
    await _open_fa(host_proto)
    await read_until_command(host_proto, "HostGame", target="game")

    # Guest tries to join with wrong password
    guest_id, _, guest_proto = await connect_and_sign_in(
        ("Rhiza", "puff_the_magic_dragon"), lobby_server
    )
    rec = MessageRecorder(guest_proto, "game_join_bad_password")
    await read_until_command(rec, "game_info")

    await rec.send_message({
        "command": "game_join",
        "uid": game_id,
        "password": "wrong_password",
    })

    msg = await rec.read_message()
    assert msg["command"] == "game_join_failed"
    assert msg["reason"] == "bad_password"

    notice = await rec.read_message()
    assert notice["command"] == "notice"

    _assert_or_generate(rec, generate_transcripts, "game_join_bad_password")


@fast_forward(60)
async def test_transcript_matchmaking_start_stop(
    lobby_server, generate_transcripts, fixed_time
):
    """Record starting and then cancelling a matchmaking search."""
    _, _, proto = await connect_and_sign_in(
        ("ladder1", "ladder1"), lobby_server
    )
    rec = MessageRecorder(proto, "matchmaking_start_stop")
    await read_until_command(rec, "game_info")

    # Start search
    await rec.send_message({
        "command": "game_matchmaking",
        "state": "start",
        "faction": "uef",
        "queue_name": "ladder1v1",
    })
    search_info = await read_until_command(rec, "search_info", state="start")
    assert search_info["queue_name"] == "ladder1v1"

    # Stop search
    await rec.send_message({
        "command": "game_matchmaking",
        "state": "stop",
        "queue_name": "ladder1v1",
    })
    # The server sends no explicit "stop" acknowledgement — it just stops.
    # We wait a brief moment and then record that nothing came.
    await asyncio.sleep(0.5)

    _assert_or_generate(rec, generate_transcripts, "matchmaking_start_stop")


@fast_forward(30)
async def test_transcript_matchmaking_non_owner(
    lobby_server, generate_transcripts, party_service, fixed_time
):
    """Record what happens when a non-owner tries to queue for matchmaking."""
    # Create a party: test is owner, rhiza is member
    test_id, _, test_proto = await connect_and_sign_in(
        ("test", "test_password"), lobby_server
    )
    rhiza_id, _, rhiza_proto = await connect_and_sign_in(
        ("Rhiza", "puff_the_magic_dragon"), lobby_server
    )
    await read_until_command(test_proto, "game_info")
    await read_until_command(rhiza_proto, "game_info")

    # test invites rhiza
    await test_proto.send_message({
        "command": "invite_to_party",
        "recipient_id": rhiza_id,
    })
    await read_until_command(rhiza_proto, "party_invite")

    # rhiza accepts
    await rhiza_proto.send_message({
        "command": "accept_party_invite",
        "sender_id": test_id,
    })
    await read_until_command(test_proto, "update_party")
    await read_until_command(rhiza_proto, "update_party")

    # rhiza (non-owner) tries to queue
    rec = MessageRecorder(rhiza_proto, "matchmaking_non_owner")
    await rec.send_message({
        "command": "game_matchmaking",
        "state": "start",
        "faction": "uef",
        "queue_name": "ladder1v1",
    })

    # Should get an error notice
    msg = await rec.read_message()
    assert msg["command"] == "notice"
    assert msg["style"] == "error"

    _assert_or_generate(rec, generate_transcripts, "matchmaking_non_owner")


@fast_forward(30)
async def test_transcript_party_invite_accept(
    lobby_server, generate_transcripts, party_service, fixed_time
):
    """Record the full party invite + accept workflow."""
    test_id, _, test_proto = await connect_and_sign_in(
        ("test", "test_password"), lobby_server
    )
    rhiza_id, _, rhiza_proto = await connect_and_sign_in(
        ("Rhiza", "puff_the_magic_dragon"), lobby_server
    )
    await read_until_command(test_proto, "game_info")
    await read_until_command(rhiza_proto, "game_info")

    rec_owner = MessageRecorder(test_proto, "party_invite_accept_owner")
    rec_invitee = MessageRecorder(rhiza_proto, "party_invite_accept_invitee")

    # Invite
    await rec_owner.send_message({
        "command": "invite_to_party",
        "recipient_id": rhiza_id,
    })

    # Invitee receives party_invite
    invite_msg = await read_until_command(rec_invitee, "party_invite")
    assert invite_msg["sender"] == test_id

    # Invitee accepts
    await rec_invitee.send_message({
        "command": "accept_party_invite",
        "sender_id": test_id,
    })

    # Both receive update_party
    owner_update = await read_until_command(rec_owner, "update_party")
    invitee_update = await read_until_command(rec_invitee, "update_party")
    assert owner_update["command"] == "update_party"
    assert invitee_update["command"] == "update_party"

    _assert_or_generate(rec_owner, generate_transcripts, "party_invite_accept_owner")
    _assert_or_generate(rec_invitee, generate_transcripts, "party_invite_accept_invitee")


@fast_forward(30)
async def test_transcript_party_kick(
    lobby_server, generate_transcripts, party_service, fixed_time
):
    """Record the party owner kicking a member."""
    test_id, _, test_proto = await connect_and_sign_in(
        ("test", "test_password"), lobby_server
    )
    rhiza_id, _, rhiza_proto = await connect_and_sign_in(
        ("Rhiza", "puff_the_magic_dragon"), lobby_server
    )
    await read_until_command(test_proto, "game_info")
    await read_until_command(rhiza_proto, "game_info")

    # Form party
    await test_proto.send_message({
        "command": "invite_to_party",
        "recipient_id": rhiza_id,
    })
    await read_until_command(rhiza_proto, "party_invite")
    await rhiza_proto.send_message({
        "command": "accept_party_invite",
        "sender_id": test_id,
    })
    await read_until_command(test_proto, "update_party")
    await read_until_command(rhiza_proto, "update_party")

    rec_owner = MessageRecorder(test_proto, "party_kick_owner")
    rec_kicked = MessageRecorder(rhiza_proto, "party_kick_kicked")

    # Kick
    await rec_owner.send_message({
        "command": "kick_player_from_party",
        "kicked_player_id": rhiza_id,
    })

    # Owner gets update_party, kicked gets kicked_from_party
    owner_update = await read_until_command(rec_owner, "update_party")
    assert owner_update["command"] == "update_party"

    kicked_msg = await read_until_command(rec_kicked, "kicked_from_party")
    assert kicked_msg["command"] == "kicked_from_party"

    _assert_or_generate(rec_owner, generate_transcripts, "party_kick_owner")
    _assert_or_generate(rec_kicked, generate_transcripts, "party_kick_kicked")


@fast_forward(30)
async def test_transcript_reconnect_game_gone(
    lobby_server, generate_transcripts, fixed_time
):
    """Record what happens when restoring a game that no longer exists."""
    _, _, proto = await connect_and_sign_in(
        ("test", "test_password"), lobby_server
    )
    rec = MessageRecorder(proto, "reconnect_game_gone")
    await read_until_command(rec, "game_info")

    await rec.send_message({
        "command": "restore_game_session",
        "game_id": 999999,
    })

    msg = await rec.read_message()
    assert msg["command"] == "notice"
    assert "no longer exists" in msg["text"]

    _assert_or_generate(rec, generate_transcripts, "reconnect_game_gone")


@fast_forward(30)
async def test_transcript_non_host_option_ignored(
    lobby_server, generate_transcripts, fixed_time
):
    """
    Record that a non-host sending GameOption gets NO response
    (silently ignored).
    """
    # Host creates game
    host_id, _, host_proto = await connect_and_sign_in(
        ("test", "test_password"), lobby_server
    )
    await read_until_command(host_proto, "game_info")
    game_id = await _host_game(host_proto)
    await _open_fa(host_proto)
    await read_until_command(host_proto, "HostGame", target="game")

    # Guest joins
    guest_id, _, guest_proto = await connect_and_sign_in(
        ("Rhiza", "puff_the_magic_dragon"), lobby_server
    )
    await read_until_command(guest_proto, "game_info")
    await _join_game(guest_proto, game_id)
    await _open_fa(guest_proto)

    rec = MessageRecorder(guest_proto, "non_host_option_ignored")

    # Guest (non-host) tries to set a game option
    await rec.send_message({
        "target": "game",
        "command": "GameOption",
        "args": ["Title", "Hacked Title"],
    })

    # Wait a bit — no response should come
    got_response = False
    try:
        await asyncio.wait_for(rec.read_message(), timeout=1.0)
        got_response = True
    except asyncio.TimeoutError:
        pass

    assert not got_response, "Non-host GameOption should be silently ignored"

    _assert_or_generate(rec, generate_transcripts, "non_host_option_ignored")


@fast_forward(30)
async def test_transcript_ping_pong(
    lobby_server, generate_transcripts, fixed_time
):
    """Record a simple ping/pong exchange (keepalive)."""
    _, _, proto = await connect_and_sign_in(
        ("test", "test_password"), lobby_server
    )
    rec = MessageRecorder(proto, "ping_pong")
    await read_until_command(rec, "game_info")

    await rec.send_message({"command": "ping"})
    pong = await rec.read_message()
    assert pong["command"] == "pong"

    _assert_or_generate(rec, generate_transcripts, "ping_pong")


# ---------------------------------------------------------------------------
# Behavioral tests — verify edge cases that trip up client developers
# ---------------------------------------------------------------------------

@fast_forward(60)
async def test_disconnect_during_matchmaking_cancels_search(
    lobby_server, ladder_service, player_service
):
    """
    Verify that disconnecting while in a matchmaking queue cancels the
    search. The player should be removed from the queue.
    """
    player_id, _, proto = await connect_and_sign_in(
        ("ladder1", "ladder1"), lobby_server
    )
    await read_until_command(proto, "game_info")

    # Start matchmaking
    await proto.send_message({
        "command": "game_matchmaking",
        "state": "start",
        "faction": "uef",
        "queue_name": "ladder1v1",
    })
    await read_until_command(proto, "search_info", state="start")

    # Verify player is searching
    player = player_service.get_player(player_id)
    assert player is not None
    assert player in ladder_service._searches

    # Disconnect
    await proto.close()
    # Allow server to process disconnect
    await asyncio.sleep(1)

    # Player should no longer be in the search dict
    assert player not in ladder_service._searches


@fast_forward(60)
async def test_disconnect_during_party_removes_from_party(
    lobby_server, party_service, player_service
):
    """
    Verify that disconnecting while in a party removes the player from
    the party. If the owner disconnects, the party is disbanded.
    """
    test_id, _, test_proto = await connect_and_sign_in(
        ("test", "test_password"), lobby_server
    )
    rhiza_id, _, rhiza_proto = await connect_and_sign_in(
        ("Rhiza", "puff_the_magic_dragon"), lobby_server
    )
    await read_until_command(test_proto, "game_info")
    await read_until_command(rhiza_proto, "game_info")

    # Form party
    await test_proto.send_message({
        "command": "invite_to_party",
        "recipient_id": rhiza_id,
    })
    await read_until_command(rhiza_proto, "party_invite")
    await rhiza_proto.send_message({
        "command": "accept_party_invite",
        "sender_id": test_id,
    })
    await read_until_command(test_proto, "update_party")
    await read_until_command(rhiza_proto, "update_party")

    # Verify party exists with 2 members
    test_player = player_service.get_player(test_id)
    party = party_service.player_parties[test_player]
    assert len(party) == 2

    # Disconnect the member (rhiza)
    await rhiza_proto.close()
    await asyncio.sleep(1)

    # test's party should now only have test (1 member)
    msg = await read_until(
        test_proto,
        lambda m: (
            m.get("command") == "update_party"
            and len(m.get("members", [])) == 1
        ),
        timeout=10,
    )
    assert len(msg["members"]) == 1
    assert msg["members"][0]["player"] == test_id


@fast_forward(30)
async def test_non_host_game_options_no_response(lobby_server):
    """
    Verify that a non-host sending GameOption, GameMods, PlayerOption,
    AIOption, or ClearSlot receives absolutely no response.
    """
    # Host creates game
    host_id, _, host_proto = await connect_and_sign_in(
        ("test", "test_password"), lobby_server
    )
    await read_until_command(host_proto, "game_info")
    game_id = await _host_game(host_proto)
    await _open_fa(host_proto)
    await read_until_command(host_proto, "HostGame", target="game")

    # Guest joins
    guest_id, _, guest_proto = await connect_and_sign_in(
        ("Rhiza", "puff_the_magic_dragon"), lobby_server
    )
    await read_until_command(guest_proto, "game_info")
    await _join_game(guest_proto, game_id)
    await _open_fa(guest_proto)

    # Guest tries each host-only command
    host_only_commands = [
        {"target": "game", "command": "GameOption", "args": ["Title", "X"]},
        {"target": "game", "command": "GameMods", "args": ["activated", "0"]},
        {"target": "game", "command": "PlayerOption", "args": [host_id, "Team", 2]},
        {"target": "game", "command": "AIOption", "args": ["AI1", "Team", 2]},
        {"target": "game", "command": "ClearSlot", "args": [1]},
    ]

    for cmd in host_only_commands:
        await guest_proto.send_message(cmd)

    # None of these should produce a response
    got_response = False
    try:
        await asyncio.wait_for(guest_proto.read_message(), timeout=2.0)
        got_response = True
    except asyncio.TimeoutError:
        pass

    assert not got_response, (
        "Non-host commands should be silently ignored with no response"
    )


@fast_forward(30)
async def test_matchmaking_cancel_nonexistent_is_silent(lobby_server):
    """
    Verify that canceling a matchmaking queue you were never in produces
    no error response.
    """
    _, _, proto = await connect_and_sign_in(
        ("ladder1", "ladder1"), lobby_server
    )
    await read_until_command(proto, "game_info")

    # Cancel a search we never started
    await proto.send_message({
        "command": "game_matchmaking",
        "state": "stop",
        "queue_name": "ladder1v1",
    })

    # No error should come back
    got_response = False
    try:
        await asyncio.wait_for(proto.read_message(), timeout=1.0)
        got_response = True
    except asyncio.TimeoutError:
        pass

    assert not got_response, (
        "Canceling a non-existent search should be silently ignored"
    )


@fast_forward(30)
async def test_duplicate_game_host_replaces_connection(lobby_server, fixed_time):
    """
    Verify that hosting a game while already hosting aborts the old
    game connection and creates a new one.
    """
    _, _, proto = await connect_and_sign_in(
        ("test", "test_password"), lobby_server
    )
    await read_until_command(proto, "game_info")

    # Host first game
    game_id_1 = await _host_game(proto, title="Game 1")
    await _open_fa(proto)
    await read_until_command(proto, "HostGame", target="game")
    await read_until_command(proto, "game_info")

    # Host second game (without leaving first)
    await proto.send_message({
        "command": "game_host",
        "mod": "faf",
        "visibility": "public",
        "title": "Game 2",
    })
    launch = await read_until_command(proto, "game_launch")
    game_id_2 = int(launch["uid"])

    assert game_id_1 != game_id_2


@fast_forward(30)
async def test_game_host_leave_ends_game(lobby_server, fixed_time):
    """
    Verify that when the host disconnects from a lobby (not yet launched),
    the game ends and other players receive game_info updates.
    """
    # Host
    host_id, _, host_proto = await connect_and_sign_in(
        ("test", "test_password"), lobby_server
    )
    await read_until_command(host_proto, "game_info")
    game_id = await _host_game(host_proto)
    await _open_fa(host_proto)
    await read_until_command(host_proto, "HostGame", target="game")
    await read_until_command(host_proto, "game_info")

    # Guest joins
    guest_id, _, guest_proto = await connect_and_sign_in(
        ("Rhiza", "puff_the_magic_dragon"), lobby_server
    )
    await read_until_command(guest_proto, "game_info")
    await _join_game(guest_proto, game_id)
    await _open_fa(guest_proto)

    # Host disconnects
    await host_proto.close()

    # Guest should receive game_info indicating game ended
    msg = await read_until(
        guest_proto,
        lambda m: (
            m.get("command") == "game_info"
            and m.get("uid") == game_id
            and m.get("state") == "closed"
        ),
        timeout=10,
    )
    assert msg["state"] == "closed"
