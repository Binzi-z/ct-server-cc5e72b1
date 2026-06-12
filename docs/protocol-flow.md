# FAF Lobby Protocol — Client Developer Guide

This document describes the message protocol that third-party clients use to
communicate with the FAF lobby server. It covers the complete lifecycle from
initial connection through authentication, matchmaking, game hosting/joining,
and disconnect/reconnect handling.

> **Wire formats:** The server accepts two wire formats on different ports.
> Most clients use **SimpleJsonProtocol** (JSON lines on port 8002). The
> legacy **QDataStreamProtocol** (Qt binary on port 8001) is documented in
> `README.md` and is not covered in detail here.

All messages are JSON objects with a `"command"` field that identifies the
message type. Server-to-client messages also use the same envelope.

```
Client → Server:  {"command": "<name>", ...fields...}
Server → Client:  {"command": "<name>", ...fields...}
```

---

## 1. Connection and Authentication

The authentication flow has two steps: first request a session token, then
submit credentials.

### Step 1 — Request a session

Send `ask_session` immediately after connecting. The server responds with a
`session` message containing a unique session identifier.

| Direction | Command | Key Fields |
|-----------|---------|------------|
| Client → Server | `ask_session` | `user_agent`, `version` |
| Server → Client | `session` | `session` (UUID string) |

### Step 2 — Authenticate

Send either `hello` (username/password, deprecated and disabled by default)
or `auth` (OAuth token). On success the server sends four messages in strict
order:

1. **`welcome`** — your player profile and the current server time
2. **`player_info`** — list of all currently online players
3. **`social`** — your friends, foes, clan channels, and admin power level
4. **`game_info`** — list of all currently visible open games

After this point you are fully authenticated and may use all other commands.

<!-- transcript: login -->
```json
(placeholder — run embed_transcripts.py to populate)
```
<!-- /transcript -->

### Duplicate login

If the same account logs in from a second connection, the **old** connection
receives a `notice` with `"style": "kick"` and `"text"` explaining that
another sign-in occurred. The old connection is then closed. The new
connection proceeds normally.

<!-- transcript: login_duplicate_old -->
```json
(placeholder — run embed_transcripts.py to populate)
```
<!-- /transcript -->

### Authentication failures

| Scenario | Server response | Connection |
|----------|----------------|------------|
| Wrong credentials | `authentication_failed` with `text` | Kept open (retry) |
| Banned account | `notice` style `"error"` with ban details | **Closed** |
| `hello` disabled | `notice` style `"error"`, recoverable=False | **Closed** |
| Account not found | `authentication_failed` | Kept open (retry) |

---

## 2. Online Player Synchronization

After login, the server broadcasts `player_info` messages whenever a player's
state changes (login, logout, state change, rating update). Each broadcast
contains a `"players"` array with one or more player objects.

```json
{
  "command": "player_info",
  "players": [
    {
      "id": 123,
      "login": "PlayerName",
      "clan": "ABC",
      "country": "US",
      "ratings": {
        "global": {"rating": [1500.0, 100.0], "number_of_games": 42},
        "ladder_1v1": {"rating": [1500.0, 100.0], "number_of_games": 10}
      },
      "global_rating": [1500.0, 100.0],
      "ladder_rating": [1500.0, 100.0],
      "number_of_games": 42
    }
  ]
}
```

> **Important:** Process the initial `player_info` (sent right after
> `welcome`) before handling any game or social messages. Other players'
> clients may try to interact with you immediately after seeing your
> `player_info` broadcast.

---

## 3. Keepalive — Ping/Pong

Send `ping` periodically to keep the connection alive. The server replies
with `pong`. The server may also send `ping` to you; reply with `pong`.

<!-- transcript: ping_pong -->
```json
(placeholder — run embed_transcripts.py to populate)
```
<!-- /transcript -->

---

## 4. Party System

Parties group players for team matchmaking. Every player implicitly has a
party of one. Party operations are governed by an **owner** model: only the
party owner can invite, kick, or enter matchmaking.

### Invite a player

Only the **owner** can send invitations.

| Direction | Command | Key Fields |
|-----------|---------|------------|
| Owner → Server | `invite_to_party` | `recipient_id` |
| Server → Recipient | `party_invite` | `sender` (player ID) |

If a non-owner tries to invite, the server returns:
```json
{"command": "notice", "style": "error", "text": "You do not own this party."}
```

### Accept an invitation

The invited player sends `accept_party_invite` with the inviter's player ID.
Both parties then receive `update_party`.

| Direction | Command | Key Fields |
|-----------|---------|------------|
| Invitee → Server | `accept_party_invite` | `sender_id` |
| Server → All members | `update_party` | `owner`, `members[]` |

<!-- transcript: party_invite_accept_owner -->
```json
(placeholder — run embed_transcripts.py to populate)
```
<!-- /transcript -->

### update_party message shape

```json
{
  "command": "update_party",
  "owner": 1,
  "members": [
    {"player": 1, "factions": ["uef", "aeon", "cybran", "seraphim"]},
    {"player": 3, "factions": ["uef", "aeon", "cybran", "seraphim"]}
  ]
}
```

### Set factions

Any party member can set their own faction preferences:

```json
{"command": "set_party_factions", "factions": ["uef", "cybran"]}
```

All members receive an updated `update_party`.

### Kick a member

Only the **owner** can kick. The kicked player receives `kicked_from_party`.

| Direction | Command | Key Fields |
|-----------|---------|------------|
| Owner → Server | `kick_player_from_party` | `kicked_player_id` |
| Server → Owner | `update_party` | Updated member list |
| Server → Kicked | `kicked_from_party` | _(no fields)_ |

<!-- transcript: party_kick_owner -->
```json
(placeholder — run embed_transcripts.py to populate)
```
<!-- /transcript -->

### Leave a party

Any member can leave:

```json
{"command": "leave_party"}
```

If the **owner** leaves, the party is **disbanded** and all remaining members
receive `kicked_from_party`.

---

## 5. Matchmaking

### Starting a search

Only the **party owner** can enter the party into a matchmaking queue. Send:

```json
{
  "command": "game_matchmaking",
  "state": "start",
  "faction": "uef",
  "queue_name": "ladder1v1"
}
```

The server confirms with:

```json
{
  "command": "search_info",
  "queue_name": "ladder1v1",
  "state": "start"
}
```

<!-- transcript: matchmaking_start_stop -->
```json
(placeholder — run embed_transcripts.py to populate)
```
<!-- /transcript -->

### Match found

When a match is found, **all** matched players receive:

```json
{"command": "match_found", "queue_name": "ladder1v1"}
```

Then each player receives a `game_launch` message (see section 7). Any other
active searches for those players are automatically canceled, and a
`search_info` with `"state": "stop"` is sent for each canceled queue.

### Canceling a search

Send:

```json
{
  "command": "game_matchmaking",
  "state": "stop",
  "queue_name": "ladder1v1"
}
```

The server responds with:

```json
{
  "command": "search_info",
  "queue_name": "ladder1v1",
  "state": "stop"
}
```

> **Silent ignore:** If you cancel a queue you were never in, the server
> silently ignores the request — no error, no response.

### Search timeout

If no match is found within the queue's time limit, the server sends:

```json
{
  "command": "search_timeout",
  "queue_name": "ladder1v1",
  "queue_pop_time": 60
}
```

Followed by `search_info` with `"state": "stop"`.

### Error cases

| Scenario | Server response |
|----------|----------------|
| Non-owner tries to queue | `notice` style `"error"`: "Only the party owner may enter the party into a queue." |
| Party too large for queue | `notice` style `"error"`: "Your party is too large to join that queue!" |
| Player not IDLE | `notice` style `"error"`: "Can't search for a match while in state ..." |

<!-- transcript: matchmaking_non_owner -->
```json
(placeholder — run embed_transcripts.py to populate)
```
<!-- /transcript -->

---

## 6. Game Hosting

### Creating a game

You must be in `IDLE` state. Send:

```json
{
  "command": "game_host",
  "mod": "faf",
  "visibility": "public",
  "title": "My Game",
  "mapname": "scmp_007",
  "password": null
}
```

The server responds with `game_launch`:

```json
{
  "command": "game_launch",
  "args": ["/numgames", 42],
  "uid": 12345,
  "mod": "faf",
  "name": "My Game",
  "init_mode": 0,
  "game_type": "custom",
  "rating_type": "global"
}
```

<!-- transcript: game_host -->
```json
(placeholder — run embed_transcripts.py to populate)
```
<!-- /transcript -->

### Opening the game client

After receiving `game_launch`, your client should start the game process.
Once the game process is ready, send two `GameState` messages:

```json
{"target": "game", "command": "GameState", "args": ["Idle"]}
{"target": "game", "command": "GameState", "args": ["Lobby"]}
```

The server then responds with `HostGame` (targeted to the game connection):

```json
{"target": "game", "command": "HostGame", "args": ["<map>"]}
```

### Host-only commands

While in the lobby, the **host** can configure the game using these
game-targeted commands:

| Command | Args | Description |
|---------|------|-------------|
| `GameOption` | `[key, value]` | Set game option (Title, Map, etc.) |
| `GameMods` | `[mode, uids]` | Set active mods |
| `PlayerOption` | `[player_id, key, value]` | Set player slot options |
| `AIOption` | `[ai_name, key, value]` | Configure AI players |
| `ClearSlot` | `[slot_number]` | Remove a player/AI from a slot |

> **Silently ignored:** If a non-host player sends any of these commands, the
> server **silently drops** them. No error message is sent back. Your client
> should not send these unless it is the host.

<!-- transcript: non_host_option_ignored -->
```json
(placeholder — run embed_transcripts.py to populate)
```
<!-- /transcript -->

### Launching the game

When all players are ready, the host sends:

```json
{"target": "game", "command": "GameState", "args": ["Launching"]}
```

The server will broadcast `game_info` with `"launched_at"` set, and all
connected players transition to the playing state.

### Title validation

- Title must contain only ASCII characters
- Title must not be empty (whitespace only)
- Invalid titles return `notice` style `"error"` (recoverable)

---

## 7. Game Joining

### Joining a game

You must be in `IDLE` state. Send:

```json
{
  "command": "game_join",
  "uid": 12345,
  "password": null
}
```

**Success:** The server responds with `game_launch` (same shape as hosting).
Then open the game process and send `GameState: Idle` + `GameState: Lobby`
just like the host. The server responds with `JoinGame`.

**Failure:** The server responds with `game_join_failed`:

```json
{
  "command": "game_join_failed",
  "reason": "host_left_game",
  "uid": 12345
}
```

Followed by a deprecated `notice` style `"info"` for backwards compatibility.

<!-- transcript: game_join_success -->
```json
(placeholder — run embed_transcripts.py to populate)
```
<!-- /transcript -->

### Failure reasons

| `reason` value | When it occurs |
|----------------|---------------|
| `host_left_game` | Game does not exist (ID not found in game service) |
| `game_not_ready` | Game exists but is not in `LOBBY` state (already launched or ended) |
| `bad_password` | Password does not match (case sensitive) |

<!-- transcript: game_join_not_found -->
```json
(placeholder — run embed_transcripts.py to populate)
```
<!-- /transcript -->

<!-- transcript: game_join_bad_password -->
```json
(placeholder — run embed_transcripts.py to populate)
```
<!-- /transcript -->

### Additional join errors (ClientError)

| Scenario | Response | Connection |
|----------|----------|------------|
| You are on the host's foe list | `notice` style `"error"`: "You cannot join games hosted by this player." | Kept |
| Game uses non-standard init mode | `notice` style `"error"`: "The game cannot be joined in this way." | Kept |
| You are not IDLE | `notice` style `"error"`: "Can't join a game while in state ..." | Kept |

---

## 8. In-Game Messages

Once the game is running, clients communicate with the server through
game-targeted messages (`"target": "game"`).

### Game state transitions

| State | Meaning |
|-------|---------|
| `Idle` | Game process has started, entering lobby |
| `Lobby` | Game lobby is ready for configuration |
| `Launching` | Game is about to start (host only triggers this) |
| `Ended` | Game has finished |

### Reporting game results

Each player reports their results when the game ends:

```json
{
  "target": "game",
  "command": "GameResult",
  "args": [army_index, "score 12345"]
}
```

### ICE messages (peer connectivity)

ICE messages are forwarded between players for NAT traversal:

```json
{
  "target": "game",
  "command": "IceMsg",
  "args": [receiver_player_id, "<ice_offer_or_answer>"]
}
```

> **Silently ignored:** If the recipient player is unknown, disconnected,
> or has no game connection, the ICE message is dropped without any
> response.

### Other in-game commands

| Command | Direction | Notes |
|---------|-----------|-------|
| `JsonStats` | Client → Server | Game statistics JSON |
| `EnforceRating` | Client → Server | Force rating calculation |
| `Desync` | Client → Server | Report a desync event |
| `TeamkillReport` | Client → Server | Report a teamkill |
| `OperationComplete` | Client → Server | Co-op mission completion |
| `Chat` | Client → Server | **Ignored** by server |
| `GameFull` | Client → Server | **Ignored** by server |
| `Rehost` | Client → Server | **Ignored** by server |
| `LaunchStatus` | Client → Server | **Ignored** by server |

---

## 9. Disconnect and Reconnect

### What happens when you disconnect

When the server detects a lost connection, it runs this cleanup chain:

1. **Player service**: Removes the player from the online player list and
   broadcasts an updated `player_info` to all connected clients.
2. **Ladder service**: Cancels **all** active matchmaking searches for the
   player and sends `search_info` state `"stop"` for each.
3. **Party service**: Removes the player from their party. If they were the
   owner, the party is disbanded and all members receive `kicked_from_party`.
4. **Game connection**: If the player had an active game connection, it is
   aborted. If the player was the **host** and the game was still in the
   lobby (not yet launched), the **entire game ends** — all other players
   receive `game_info` with `"state": "closed"`.

### Reconnecting to a game

If you disconnect and reconnect, you can restore your game session. After
logging in again, send:

```json
{
  "command": "restore_game_session",
  "game_id": 12345
}
```

**Success:** The server restores your game connection. Your player state
becomes `PLAYING`.

**Failure cases** (all return `notice` style `"info"`, connection kept open):

| Scenario | Warning text |
|----------|-------------|
| Game does not exist | "The game you were connected to no longer exists" |
| Game has ended | "The game you were connected to is no longer available" |
| Not a participant (LIVE games only) | "You are not part of this game" |

<!-- transcript: reconnect_game_gone -->
```json
(placeholder — run embed_transcripts.py to populate)
```
<!-- /transcript -->

> **Important:** You can only reconnect to a `LIVE` game if you were
> originally a participant. For `LOBBY` state games, any player can restore.

---

## 10. Error Handling Reference

### Error response types

| Exception | Response command | Connection | Notes |
|-----------|-----------------|------------|-------|
| `ClientError` (recoverable) | `notice` style `"error"` | Kept open | Most common error |
| `ClientError` (fatal) | `notice` style `"error"` | **Closed** | Used for disabled features, duplicate login warnings |
| `AuthenticationError` | `authentication_failed` | Kept open | Wrong credentials |
| `BanError` | `notice` style `"error"` | **Closed** | Banned account |
| `DisabledError` | `disabled` with `request` field | Kept open | Server shutting down |
| Unknown command | `invalid` | **Closed** | Command name not recognized |
| Garbage data | _(none)_ | **Closed** | KeyError/ValueError in message parsing |
| Database error | `notice` style `"error"` | **Closed** | "Unable to connect to database" |
| Unexpected exception | `invalid` | **Closed** | Server-side bug |

### Operations that are silently ignored

These operations produce **no response at all** — the server drops them
without sending any message back:

| Operation | Condition | Source |
|-----------|-----------|--------|
| `GameOption` | Sender is not the host | `gameconnection.py` |
| `GameMods` | Sender is not the host | `gameconnection.py` |
| `PlayerOption` | Sender is not the host | `gameconnection.py` |
| `AIOption` | Sender is not the host | `gameconnection.py` |
| `ClearSlot` | Sender is not the host | `gameconnection.py` |
| `IceMsg` | Recipient unknown or disconnected | `gameconnection.py` |
| `game_matchmaking` stop | Not currently searching | `lobbyconnection.py` |
| `social_add` | Duplicate friend/foe entry | `lobbyconnection.py` |
| `TeamkillReport` | Target is an AI player | `gameconnection.py` |
| `Chat` | Always | `gameconnection.py` |
| `GameFull` | Always | `gameconnection.py` |
| `Rehost` | Always | `gameconnection.py` |
| `LaunchStatus` | Always | `gameconnection.py` |
| Game-targeted message | No game connection exists | `lobbyconnection.py` |

### Player state guards

Most game-related commands require the player to be in `IDLE` state. If the
player is in any other state, the server returns:

```json
{
  "command": "notice",
  "style": "error",
  "text": "Can't <action> while in state <current_state>"
}
```

Commands with IDLE guards: `game_host`, `game_join`, `restore_game_session`.

---

## 11. Complete State Machine

```
                     ┌──────────┐
                     │  IDLE    │ ◄─────────────────────┐
                     └────┬─────┘                       │
                          │                             │
              ┌───────────┼───────────┐                 │
              │           │           │                 │
              ▼           ▼           ▼                 │
     ┌──────────────┐ ┌───────┐ ┌──────────┐           │
     │ STARTING_    │ │SEARCH-│ │ STARTING_ │           │
     │ GAME         │ │ING_   │ │ AUTOMATCH │           │
     └──────┬───────┘ │LADDER │ └─────┬─────┘           │
            │         └───┬───┘       │                 │
       ┌────┴────┐        │      ┌────┴────┐            │
       ▼         ▼        │      ▼         │            │
  ┌─────────┐ ┌──────┐   │ ┌──────────┐   │            │
  │ HOSTING │ │JOINING│   │ │STARTING_ │   │            │
  └────┬────┘ └──┬───┘   │ │GAME      │   │            │
       │         │        │ └────┬─────┘   │            │
       │         │        │      │         │            │
       └────┬────┘        │      │         │            │
            │             │      ▼         │            │
            │             │ ┌─────────┐    │            │
            │             │ │ PLAYING │    │            │
            │             │ └────┬────┘    │            │
            │             │      │         │            │
            └─────────────┴──────┴─────────┴────────────┘
                         (disconnect / game ends)
```

| State | Value | Description |
|-------|-------|-------------|
| `IDLE` | 1 | Default state, can start any action |
| `PLAYING` | 2 | In an active game |
| `HOSTING` | 3 | Hosting a game lobby |
| `JOINING` | 4 | Joined someone else's game lobby |
| `SEARCHING_LADDER` | 5 | In a matchmaking queue |
| `STARTING_AUTOMATCH` | 6 | Match found, game launching |
| `STARTING_GAME` | 7 | Game process starting up |
