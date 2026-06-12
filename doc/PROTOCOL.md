# 大厅服务协议流程（面向客户端开发者）

本文把一个第三方客户端从 **连接登录 → 在线玩家同步 → 组队/匹配 → 游戏创建加入 → 掉线清理** 的完整消息时序串起来讲清楚，
并明确说明 **哪些异常会静默忽略、哪些会返回错误、哪些会直接断开连接**。

> 本文中带 `name=` 标记的 JSON 示例不是手写的假数据，而是由集成测试
> [`tests/integration_tests/test_protocol_doc.py`](../tests/integration_tests/test_protocol_doc.py)
> 用进程内真实服务器跑出来逐字段校验过的。一旦服务端行为变了而本文没改，这些测试就会失败。
> 详见末尾「[文档如何保持正确](#文档如何保持正确)」。

---

## 0. 约定与前提

### 0.1 线格式（wire format）

* 每条消息是一个 **JSON 对象 + 一个 ASCII 换行符 `\n`**。
* 服务端支持两种封帧协议：`QDataStreamProtocol`（旧客户端）与 `SimpleJsonProtocol`（推荐新接入用）。
  生产环境前面通常还套了一层 websocket 桥（`ws_bridge_rs`）。两种协议承载的 **应用层消息完全一致**，本文只讨论应用层。
* 每条消息必须有 `command` 字段。**客户端→服务端** 的 `command` 会被分发到 `LobbyConnection` 上的 `command_<name>` 方法；
  **服务端→客户端** 既有请求/响应，也有异步广播（`*_info` 一类）。

### 0.2 向前兼容（非常重要）

服务端会随版本新增消息类型和字段。**客户端必须忽略自己不认识的 `command` 和不认识的字段**，不要因此报错或断开。
本文给出的示例只列出当前存在的字段；遇到多出来的字段属正常现象。

### 0.3 客户端状态机

很多命令是否被允许，取决于玩家当前所处的状态。状态在 `server/players.py` 的 `PlayerState` 中定义：

| 状态 | 含义 |
| --- | --- |
| `IDLE` | 空闲，可发起建房/进房/匹配/组队邀请 |
| `HOSTING` | 正在建房 |
| `JOINING` | 正在加入一局游戏 |
| `PLAYING` | 游戏进行中 |
| `SEARCHING_LADDER` | 正在天梯排队 |
| `STARTING_AUTOMATCH` | 匹配成功、正在拉起对局 |
| `STARTING_GAME` | 正在拉起自建对局 |

许多「为什么没反应/为什么报错」的问题都源于状态不对，见各节的状态守卫说明与 [§6 异常总表](#6-异常处理总表)。

---

## 1. 连接与登录

登录是一个三段式握手：`ask_session` → `hello`/`auth` → 服务端推送一组初始化消息。

### 1.1 取会话号

```json
{
  "command": "ask_session",
  "user_agent": "faf-client",
  "version": "1.0.0-dev"
}
```

服务端回 `session`（`session` 是一个随机整数，下面的 12345 只是示例）：

```json
{
  "command": "session",
  "session": 12345
}
```

> 若 `user_agent` 不含 `downlords-faf-client`，服务端只会记一条 warning，**不影响登录**。

### 1.2 登录：口令 或 令牌

**口令登录**（`hello`，`password` 为口令的 sha256 十六进制）：

```json
{
  "command": "hello",
  "version": "1.0.0-dev",
  "user_agent": "faf-client",
  "login": "Rhiza",
  "password": "<sha256-hex>",
  "unique_id": "some_id"
}
```

**令牌登录**（`auth`，OAuth 流程）：

```json
{
  "command": "auth",
  "version": "1.0.0-dev",
  "user_agent": "faf-client",
  "token": "<jwt>",
  "unique_id": "some_id"
}
```

> 令牌登录会 **先** 收到一条 `irc_password`（其值已废弃，固定为 `"deprecated"`），口令登录则不会。

### 1.3 登录成功后服务端推送的消息（有固定顺序）

成功后按以下顺序收到：（令牌登录在最前多一条 `irc_password`）

1. `welcome`
2. `player_info`（包含全部在线玩家，含自己）
3. `social`
4. `game_info`（当前游戏列表，见 [§4](#4-游戏创建与加入)）

同时服务端会把「你上线了」广播给其他人（见 [§2](#2-在线玩家同步)）。

`welcome` 的外层信封如下（`current_time` 是服务器当前时间，会变化；`id`/`login` 与 `me.id`/`me.login` 一致）：

```json
{
  "command": "welcome",
  "current_time": "1970-01-01T00:00:00+00:00",
  "id": 3,
  "login": "Rhiza",
  "me": {}
}
```

其中 `me` 是玩家自描述对象，结构稳定，已逐字段校验（注意 `rating` 是 `[mean, deviation]`，
`global_rating`/`ladder_rating` 为 **已废弃** 的旧字段，新客户端请读 `ratings`）：

```json name=welcome_me
{
  "id": 3,
  "login": "Rhiza",
  "clan": "123",
  "country": "",
  "ratings": {
    "global": {
      "rating": [1650.0, 62.52],
      "number_of_games": 2
    },
    "ladder_1v1": {
      "rating": [1650.0, 62.52],
      "number_of_games": 2
    }
  },
  "global_rating": [1650.0, 62.52],
  "ladder_rating": [1650.0, 62.52],
  "number_of_games": 2
}
```

`social` 给出自动加入的频道、好友/黑名单、以及权限等级（`power`：0 普通 / 1 版主 / 2 管理）：

```json name=social_rhiza
{
  "command": "social",
  "autojoin": ["#123_clan"],
  "channels": ["#123_clan"],
  "friends": [],
  "foes": [],
  "power": 0
}
```

### 1.4 重复登录

* **同一条连接** 再次 `hello`/`auth`：返回提示，**不会断开**：

```json
{
  "command": "notice",
  "style": "info",
  "text": "You are already signed in from this location!"
}
```

* **另一条连接** 用同一账号登录：新连接登录正常进行；**旧连接** 会先收到下面这条然后被踢下线（连接关闭）：

```json
{
  "command": "notice",
  "style": "kick",
  "text": "You have been signed out because you signed in elsewhere."
}
```

### 1.5 登录类错误一览

| 情况 | 服务端响应 | 连接是否关闭 |
| --- | --- | --- |
| 账号不存在 / 口令错误 | `authentication_failed` text=`Login not found or password incorrect. They are case sensitive.` | 否（可重试） |
| 找不到令牌中的用户 | `authentication_failed` text=`Cannot find user id` | 否 |
| 令牌签名无效/过期/字段缺失 | `authentication_failed` text=`Token signature was invalid` | 否 |
| 令牌缺少 lobby 作用域 | `authentication_failed` text=`Token does not have permission to login to the lobby server` | 否 |
| 账号被封禁 | `notice` style=`error`，文案含封禁原因与申诉邮箱 | **是** |

`authentication_failed` 表示「这次登录失败但连接还在」，可以直接换凭据重试；`notice`+封禁则会断开。

---

## 2. 在线玩家同步

### 2.1 脏标记广播模型

服务端 **不是** 每次变化都立刻单独推送，而是周期性地把「脏了的」对象批量广播（间隔由 `config.DIRTY_REPORT_INTERVAL` 控制）。
你会周期性收到：

* `player_info` —— 本周期内有变化的玩家（上线、改名、改状态、下线等）。
* `matchmaker_info` —— 各匹配队列概况（见 [§3.6](#36-matchmaker_info-队列概况)）。
* `game_info` —— 有变化的游戏（逐局推送；只发给「该游戏对你可见」的已登录连接；已结束的游戏会被移除）。

`player_info` 形如（`players` 为数组；玩家对象与登录时的 `me` 同构）：

```json
{
  "command": "player_info",
  "players": []
}
```

### 2.2 玩家下线的信号

玩家对象里 **会被丢弃为 `null` 的字段不会出现**。关键点：`state` 字段平时被省略，但当某玩家 **失去大厅连接（下线）** 时，
这一次 `player_info` 中该玩家的 `state` 会等于字符串 `"offline"`。**这是客户端判定「某人下线」的权威信号**。

### 2.3 保活 ping

服务端会按 `config.PING_INTERVAL` 周期给所有连接发：

```json
{ "command": "ping" }
```

客户端应把它当作保活信号（按协议回 `pong`），**不要** 当作业务消息处理或因为「不认识」而报错。

---

## 3. 组队与匹配

### 3.1 发起组队邀请

发：

```json
{
  "command": "invite_to_party",
  "recipient_id": 3
}
```

被邀请方收到（`sender` 是邀请人的玩家 id）：

```json name=party_invite
{
  "command": "party_invite",
  "sender": 1
}
```

### 3.2 接受邀请

被邀请方发：

```json
{
  "command": "accept_party_invite",
  "sender_id": 1
}
```

队伍内所有人都会收到一条 `update_party`（即 `party.to_dict()`）。新成员默认拥有全部阵营。
下例为「玩家 1 邀请玩家 3，3 接受后」队伍双方收到的内容，已逐字段校验：

```json name=update_party_two
{
  "command": "update_party",
  "owner": 1,
  "members": [
    {
      "factions": ["uef", "aeon", "cybran", "seraphim"],
      "player": 1
    },
    {
      "factions": ["uef", "aeon", "cybran", "seraphim"],
      "player": 3
    }
  ]
}
```

### 3.3 设置阵营 / 踢人 / 离队

* **设置自己的阵营**：`set_party_factions`，随后全队收到更新后的 `update_party`。
  重复的阵营会被去重（`["uef","uef","uef"]` → `["uef"]`）；**阵营列表为空会报错**：
  `notice` style=`error` text=`You must select at least one faction.`
* **房主踢人**：`kick_player_from_party`（带 `kicked_player_id`）。被踢者收到 `kicked_from_party`，其余成员收到新的 `update_party`。
* **离队**：`leave_party`。离队者收到只剩自己的 `update_party`；若队伍因此为空则解散。

### 3.4 组队类「静默忽略 vs 报错」

| 情况 | 行为 |
| --- | --- |
| 邀请的对象是你的黑名单（foe） | **静默忽略**（不发任何消息） |
| 邀请的玩家不存在 | 报错 `notice/error`：`The invited player doesn't exist` |
| 接受邀请时邀请人不存在 | 报错：`The inviting player doesn't exist` |
| 踢一个不存在的玩家 | 报错：`The kicked player doesn't exist` |
| 队伍已在排队时接受邀请 | 报错：`That party is already in queue` |
| 自己不在 `IDLE`（如正在天梯排队）时邀请/加入/踢人 | 报错：`Can't invite a player while in state SEARCHING_LADDER`（加入/踢人同理，动词不同） |

「邀请的玩家不存在」这一条已逐字段校验：

```json name=error_invite_nonexistent
{
  "command": "notice",
  "style": "error",
  "text": "The invited player doesn't exist"
}
```

### 3.5 进入匹配队列（matchmaking）

开始排队：

```json
{
  "command": "game_matchmaking",
  "state": "start",
  "faction": "uef"
}
```

服务端确认（`queue_name` 取决于所选队列，默认 `ladder1v1`），已校验：

```json name=search_info_start
{
  "command": "search_info",
  "queue_name": "ladder1v1",
  "state": "start"
}
```

停止排队：

```json
{
  "command": "game_matchmaking",
  "state": "stop"
}
```

确认（已校验）：

```json name=search_info_stop
{
  "command": "search_info",
  "queue_name": "ladder1v1",
  "state": "stop"
}
```

> **取消排队的边界**：如果你 **并不在** 任何队列里又发 `state:"stop"`，服务端 **静默忽略**，不会回 `search_info`。
> 因此客户端不要「等一条 stop 确认」来判断；应以自己维护的状态为准。

匹配成功后的时序：

1. 全队收到 `match_found`（`{"command":"match_found","queue_name":"<queue>"}`），状态进入 `STARTING_AUTOMATCH`。
2. 随后收到 `game_launch`（匹配局的字段，见 [§4.4](#44-匹配局的-game_launch)）。
3. 若拉起失败（有人没按时进游戏、掉线等），收到 `match_cancelled`（`{"command":"match_cancelled","game_id":<id>}`）。
   > 历史遗留：即便匹配被取消，服务端有时仍会先发 `game_launch` 再发 `match_cancelled`，以兼容老客户端。

违规计时（频繁取消等）会通过 `search_timeout` 下发（`{"command":"search_timeout","timeouts":[{"player":<id>,"expires_at":...}]}`）。

状态守卫：已匹配/排队中再发 `game_matchmaking start` 会报错，文案以 `Can't join a queue while <login> is in ...` 开头。

### 3.6 `matchmaker_info` 队列概况

周期性下发，也可主动发 `{"command":"matchmaker_info"}` 拉取。每个队列对象含：
`queue_name, team_size, num_players, queue_pop_time, queue_pop_time_delta, boundary_80s, boundary_75s`。

---

## 4. 游戏创建与加入

### 4.1 建房（host）

1. 客户端发 `game_host`（带 title、mod、地图等参数）。状态需为 `IDLE`，否则报错（见下）。
2. 服务端回 `game_launch`，告诉客户端去启动本地 FA 进程。host 的 `game_launch` 形如
   （`args` 固定以 `/numgames` 开头；其余字段随请求与配置变化）：

```json
{
  "command": "game_launch",
  "args": ["/numgames", 0],
  "uid": 42,
  "mod": "faf",
  "name": "My Game",
  "init_mode": 0,
  "game_type": "custom",
  "rating_type": "global"
}
```

3. 客户端启动 FA 并上报 `GameState` 进入 lobby（`HostGame`）。此后这局游戏开始对外可见，
   其他人会通过 [§2](#2-在线玩家同步) 的 `game_info` 看到它。

`game_info` 形如（字段较多，`state` 取 `open`/`playing`/`closed`；`map_file_path` 已废弃）：

```json
{
  "command": "game_info",
  "uid": 42,
  "title": "My Game",
  "state": "open",
  "game_type": "custom",
  "featured_mod": "faf",
  "sim_mods": {},
  "mapname": "scmp_007",
  "host": "Rhiza",
  "num_players": 1,
  "max_players": 8,
  "rating_type": "global",
  "rating_min": null,
  "rating_max": null,
  "enforce_rating_range": false,
  "teams_ids": [],
  "teams": {},
  "visibility": "public",
  "password_protected": false
}
```

### 4.2 进房（join）

客户端发 `game_join`（带 `uid`，私密房需带 `password`）。成功则收到一条 `game_launch`（作为对端加入），
随后同样启动 FA、上报 `GameState`。

### 4.3 进房失败与状态守卫

| 情况 | 服务端响应 | 备注 |
| --- | --- | --- |
| 房主已离开 | `game_join_failed` reason=`host_left_game` + uid | 之后还会跟一条 **已废弃** 的 `notice/info` |
| 房间未就绪 | `game_join_failed` reason=`game_not_ready` | 同上跟一条废弃 notice |
| 密码错误 | `game_join_failed` reason=`bad_password` | 同上 |
| 这种房不能这样加入（如自动匹配房） | `notice/error`：`The game cannot be joined in this way.` | |
| 已在 `STARTING_GAME` 等状态再建房/进房 | `notice/error`：`Can't host a game while in state STARTING_GAME`（进房动词不同） | 状态守卫 |

客户端应优先处理 `game_join_failed`（带机器可读的 `reason`），把紧随其后的废弃 `notice` 忽略即可。

### 4.4 匹配局的 `game_launch`

匹配成功拉起的 `game_launch` 比自建局多出对局编排信息：
`team, faction, expected_players, map_position, map_pool_map_version_id, mapname`，并且
`game_type:"matchmaker"`、`init_mode:1`、`rating_type` 为该队列的评分类型（如 `ladder_1v1`）。

### 4.5 重连到 *游戏*（restore_game_session）

大厅连接本身没有「重连宽限」（见 [§5](#5-掉线清理)）。但如果你是在 **一局进行中的游戏** 里掉线，
重新登录后可发 `restore_game_session` 把会话接回那局。其错误：

* `Can't reconnect to a game while in state PLAYING`
* `You are not part of this game`
* `The game you were connected to no longer exists`

---

## 5. 掉线清理

当一条客户端连接断开时（`servercontext.handle_client_connected` 的 `finally`），服务端依次：

1. 从连接表删除该连接，`abort` 协议。
2. 对每个服务调用 `on_connection_lost(connection)`：
   * **player_service**：移除该玩家，并把它标脏 → 下一周期的 `player_info` 中该玩家 `state` 为 `"offline"`（其他客户端据此判定其下线）。
   * **party_service**：把该玩家移出其队伍；若队伍因此为空则解散；其余成员收到新的 `update_party`。
   * **ladder_service**：取消其排队（已掉线的人本身收不到 `search_info`，但其队伍/队列状态会相应更新）。
3. 调用连接自身的 `on_connection_lost()`：仅处理与之绑定的 `game_connection`。

**要点：大厅层面没有断线重连的宽限期——连接一断，玩家立即被移除并广播下线。**
要恢复体验只能 **重新走一遍登录**；若此前在某局游戏中，再用 [§4.5](#45-重连到-游戏restore_game_session) 的 `restore_game_session` 接回。

---

## 6. 异常处理总表

下表覆盖「客户端发了一条命令后，服务端怎么处理异常」。**关键区分：是否回消息、是否断开连接。**
（实现见 `server/lobbyconnection.py` 的 `on_message_received`、`ensure_authenticated` 及各 `command_*`。）

| 触发条件 | 服务端响应 | 连接是否关闭 |
| --- | --- | --- |
| 未知命令（没有对应 `command_*`） | `{"command":"invalid"}` | **是** |
| 必填字段缺失 / 字段值非法（如阵营枚举错误） | **不回任何消息**（日志记 `Garbage command: ...`） | **是** |
| 登录前发了不允许的命令（白名单：`Bottleneck, ask_session, auth, create_account, hello, ping, pong`） | **不回任何消息**（静默） | **是** |
| `target == "game"` 但当前没有 `game_connection` | **静默丢弃** | 否 |
| `AuthenticationError`（凭据/令牌问题） | `authentication_failed` + text | 否（可重试） |
| `BanError`（封禁） | `notice/error` + 封禁文案 | **是** |
| `ClientError(recoverable=True)`（绝大多数业务报错） | `notice/error` + text | 否 |
| `ClientError(recoverable=False)` | `notice/error` + text | **是** |
| `DisabledError`（功能被关闭） | `{"command":"disabled","request":"<原命令>"}` | 否 |
| 数据库不可用（`OperationalError`） | `notice/error`：`Unable to connect to database. Please try again later.` | **是** |
| `social_add` 缺 friend/foe 键，或重复添加 | **静默忽略** | 否 |
| `social_remove` 缺键 | **断开**（`No-op social_remove.`） | **是** |
| 邀请黑名单对象入队 | **静默忽略** | 否 |
| 不在队列时取消排队 | **静默忽略** | 否 |

> 经验法则：
> * 业务层「这件事现在做不了」→ `notice/error`，连接保留，可纠正后重试。
> * 协议层「你发来的东西没法解析/不该现在发」→ 直接断开，且**多数情况下不回消息**。
>   因此客户端必须保证消息格式与字段正确，不能依赖服务端的报错来调试格式问题。
> * 一部分「无意义但无害」的请求被 **静默忽略**（社交去重、邀请黑名单、空取消等），不要等它们的回执。

---

## 文档如何保持正确

本文带 `name=` 的 JSON 代码块是 **可校验示例**。对应测试
[`tests/integration_tests/test_protocol_doc.py`](../tests/integration_tests/test_protocol_doc.py) 会：

1. 解析本文件中所有 json 代码块，校验它们都是合法 JSON（`test_doc_examples_are_valid_json`，不需要数据库）。
2. 把带 `name=` 的示例与 **进程内真实服务器** 跑出来的消息逐字段比对：
   `welcome_me`、`social_rhiza`、`party_invite`、`update_party_two`、`search_info_start`、`search_info_stop`、`error_invite_nonexistent`。

示例标记约定：可校验块在 json 代码块的信息串里加上 `name=<标识>`；普通示意块不加 `name=`，仅参与「合法 JSON」校验。

运行（需本地已起测试用 MySQL，与其余集成测试一致）：

```
pipenv run tests tests/integration_tests/test_protocol_doc.py
```

仅跑不依赖数据库的 JSON 合法性检查：

```
pipenv run tests tests/integration_tests/test_protocol_doc.py::test_doc_examples_are_valid_json
```

> 测试套件本身已开启 `--doctest-modules`。当你修改某条消息的结构时，请同步更新对应的 `name=` 代码块；
> 若忘记更新，上面的比对测试会失败，从而避免「服务端改了、文档没跟上」。
