# Pixel Office — Architecture & Specification

Status: **v1.0 (Phase 1, 2026-10-11)**. This is the binding contract between modules. If you must deviate, update this file and note it in `PROJECT.md` → İlerleme Günlüğü.

> v2 cloud mode ("Bulut Ofis": Vercel + rooms + hooks + PWA/PiP) is specified separately in [`CLOUD.md`](CLOUD.md). Everything in this file (local mode) stays valid and unchanged.

Hard constraints: Linux, **Python 3.14 stdlib only**, **no Node.js**, plain HTML + vanilla JS ES modules + `<canvas>`, no build step, no CDN, no external images (all sprites are generated in JS from string pixel maps + palettes). Transcripts are read **read-only**; the server never writes under `~/.claude`.

---

## 1. File / folder layout

```
Pixel/
├── PROJECT.md                 Master doc (Turkish): goals, phases, progress log
├── README.md                  (Phase 5) user-facing usage docs
├── server.py                  Entry point: argparse → store → watcher|simulator → HTTP server
├── pixel_office/
│   ├── __init__.py            __version__ = "1.0.0"
│   ├── config.py              All constants/thresholds (§2.4), default paths, port
│   ├── models.py              State enum, AgentInfo dataclass, to_dict() (camelCase JSON, §3.3)
│   ├── transcript.py          JSONL line parsing, tool-input summarising, SessionTracker (facts → state)
│   ├── watcher.py             FileTail (incremental reader), Watcher thread (discovery, polling, pruning)
│   ├── simulator.py           DemoSimulator thread (--demo): 5 scripted fake agents
│   ├── store.py               AgentStore: thread-safe registry + change events to subscribers
│   └── httpd.py               ThreadingHTTPServer, request handler: static, /api/*, /events (SSE)
├── web/
│   ├── index.html             Canvas + side panel markup; loads js/main.js as type="module"
│   ├── css/style.css          Layout, panel, pixelated canvas scaling, connection banner
│   └── js/
│       ├── sprites.js         Procedural pixel-art: characters, tiles, furniture, bubbles, icons (§5)
│       ├── office.js          Map constants, ASCII map parsing, walkability, spots + reservation (§4, §6.1)
│       ├── pathfinding.js     A* on the tile grid (§6.2)
│       ├── agents.js          Character class + CharacterManager (server state → target spot → walk → animate) (§6.3)
│       ├── renderer.js        Canvas drawing: background cache, y-sorting, bubbles, labels, hit-testing (§6.4)
│       ├── ui.js              Side panel: agent list, details of selected agent, connection status (§6.5)
│       └── main.js            Bootstrap, SSE client with reconnect, fixed-timestep loop, input (§6.6)
├── tests/                     (Phase 5) unittest: test_transcript.py, test_store.py, test_httpd.py, ...
└── docs/
    └── ARCHITECTURE.md        This file
```

Ownership: Phase 2 → `web/js/sprites.js`. Phase 3 → `server.py`, `pixel_office/*`. Phase 4 → `web/index.html`, `web/css/style.css`, all other `web/js/*.js`. Phase 5 → `tests/`, `README.md`, fixes anywhere.

---

## 2. Data source & agent state model

### 2.1 Observed transcript format (Claude Code, inspected 2026-10-11)

Location: `~/.claude/projects/<project-dir>/<sessionId>.jsonl` where `<project-dir>` is the cwd with `/` replaced by `-` (e.g. `-home-user-Desktop-Pixel`). One JSON object per line, appended over time.

Subagents: `~/.claude/projects/<project-dir>/<sessionId>/subagents/agent-<agentId>.jsonl` plus `agent-<agentId>.meta.json` = `{"agentType": "Explore", "description": "Phase 1: architecture spec", "toolUseId": "toolu_…", ...}`. Every line in a subagent file has `isSidechain: true`, `agentId`, and `sessionId` = the parent session. Other siblings in `<sessionId>/` (e.g. `tool-results/`, `memory/` in project dir) are ignored.

Line `type` values seen: `user`, `assistant`, `attachment`, `queue-operation`, `last-prompt`, `ai-title`, `cost-state`, `file-history-snapshot`, `file-history-delta`, `mode`, `atis-latch`, `summary` (older versions). Unknown types must be ignored.

Relevant fields:

| Field | Where | Meaning |
|---|---|---|
| `type` | all | line kind |
| `timestamp` | user/assistant/attachment/queue-operation | ISO-8601 UTC, e.g. `2026-10-11T12:46:50.208Z` (parse with `datetime.fromisoformat`) |
| `sessionId`, `cwd`, `gitBranch`, `isSidechain`, `agentId` | user/assistant/attachment | identity; `cwd` gives project path |
| `isMeta` | user | injected context, **ignore for state** |
| `message.role`, `message.content` | user/assistant | content is a `str` (user prompt) or a list of blocks |
| content block `{"type":"text","text":…}` | both | prose |
| content block `{"type":"thinking",…}` | assistant | model reasoning |
| content block `{"type":"tool_use","id","name","input"}` | assistant | tool call started |
| content block `{"type":"tool_result","tool_use_id","is_error","content"}` | user | tool call finished |
| content block `{"type":"image"}` | user | ignore |
| `message.stop_reason` | assistant | `tool_use`, `end_turn`, `stop_sequence`, `max_tokens`, or `null` |
| `message.model` | assistant | model id |
| `isApiErrorMessage` | assistant | API error turn |
| `aiTitle` | `ai-title` | session title |
| `lastPrompt` | `last-prompt` | last user prompt text |

Important quirk: **each assistant content block is written as its own line** (same `message.id`); intermediate lines usually have `stop_reason: null`. A turn's final line has `end_turn`/`stop_sequence`. User interruption appears as a user text block `"[Request interrupted by user]"` or `"[Request interrupted by user for tool use]"`. A `cost-state` line is written when a session exits.

### 2.2 States

Server-side states (the `state` JSON field) — exactly these five strings:

| state | meaning | client visual |
|---|---|---|
| `working` | a tool call is in progress | seated at desk, typing anim, tool icon bubble |
| `thinking` | model is generating (after prompt / tool result, or streaming) | seated at desk, static sit, `dots` bubble |
| `waiting_for_user` | blocked on user (permission prompt / question) | seated at desk, static sit, blinking `alert` bubble |
| `idle` | turn finished, waiting for next prompt | walks to coffee machine, drink anim, steam |
| `sleeping` | done (subagent finished, session exited, or long idle/silence) | walks to bed, sleep anim, `zzz` bubble |

`spawning` and `leaving/gone` are **client-side phases**, not server states:
- **spawning**: when an `agent_added` SSE event arrives (not part of the initial snapshot), the character appears on the door tile and walks in.
- **gone**: the server emits `agent_removed`; the character walks to the door and fades out, then is deleted.

### 2.3 Heuristics (per transcript file; `SessionTracker`)

Facts accumulated while feeding lines (in order):

- `last_write` = file `st_mtime` (seconds). For a main session, **effective_last_write** = max(own `last_write`, `last_write` of all its subagents).
- `pending` = ordered dict `tool_use_id → (name, detail, ts)`; added on assistant `tool_use` block, removed on user `tool_result` with matching `tool_use_id`. Cleared entirely on interruption text and on a new non-meta user prompt (string content or text block not starting with `[Request interrupted`).
- `last_event` = kind of the last *relevant* line, one of: `prompt` (non-meta user text/str), `tool_result`, `interrupt`, `assistant_stream` (assistant line, `stop_reason` null or `tool_use`), `assistant_end` (assistant with `stop_reason` in `end_turn`,`stop_sequence`,`max_tokens`, or `isApiErrorMessage: true`), plus `last_event_ts` (line timestamp; ingest time if absent).
- `ended` = True when a `cost-state` line is read **after** the last user/assistant line; reset to False by any later user/assistant line.
- Bookkeeping: `tool` / `detail` (last started tool), `tool_count` (# tool_use blocks seen), `last_message` (last assistant text block, first 140 chars, whitespace collapsed), `last_prompt`, `title`, `model`, `cwd`, `first_ts`.

Ignored for state: `attachment`, `queue-operation`, `file-history-*`, `mode`, `atis-latch`, `ai-title`, `last-prompt` (metadata only), user lines with `isMeta: true`.

Derivation `derive_state(now)` — first matching rule wins (`silence = now - effective_last_write`):

1. `ended` → **sleeping**.
2. `silence ≥ SILENCE_TO_SLEEP (600 s)` → **sleeping**.
3. `pending` non-empty → let `(name, _, ts)` = most recently added:
   - `name ∈ INTERACTIVE_TOOLS = {"AskUserQuestion", "ExitPlanMode"}` → **waiting_for_user**.
   - `name ∈ LONG_RUNNING_TOOLS = {"Bash", "Agent", "Task", "WebFetch", "WebSearch", "Monitor", "BashOutput"}` → **working**.
   - `now - ts ≥ PERMISSION_WAIT (8 s)` **and** `silence ≥ 8 s` → **waiting_for_user** (a fast tool like Edit/Write/Read that has not returned is almost always a permission prompt).
   - else → **working**.
4. `last_event == "assistant_end"` or `"interrupt"`:
   - subagent → **sleeping** (job finished).
   - main session: `now - last_event_ts < IDLE_TO_SLEEP (180 s)` → **idle**, else **sleeping**.
5. `last_event ∈ {"prompt", "tool_result", "assistant_stream"}` → **thinking**.
6. No user/assistant line yet → **idle**.

`tool`/`detail` in the JSON are non-null only when state is `working` or `waiting_for_user` (taken from the pending tool).

Tool detail summary (`summarize_tool_input(name, input) -> str`, max 60 chars):
`Bash` → `input.description` or first line of `input.command`; `Read`/`Edit`/`Write`/`NotebookEdit` → basename of `file_path`/`notebook_path`; `Grep`/`Glob` → `pattern`; `Agent`/`Task` → `description`; `WebFetch` → host of `url`; `WebSearch` → `query`; `TodoWrite` → `"todos"`; otherwise first string value in `input`, else `""`.

Discovery & removal:
- Track a main-session file if `mtime ≥ now - DISCOVERY_WINDOW (1800 s)`. Track a subagent file if its own mtime is within the window.
- On first read, if file size > `INITIAL_TAIL_BYTES (262144)`, start at `size - 262144` and discard the first (partial) line.
- Remove main session when `now - effective_last_write ≥ REMOVE_AFTER (1800 s)` or the file disappears.
- Remove subagent when it has been `sleeping` for `SUBAGENT_REMOVE_AFTER (120 s)` or the file disappears or its parent is removed.
- Removed files are not re-added unless their mtime changes afterwards.

Identity:
- `id` = sessionId (file stem) for main sessions; `"<sessionId>:<agentId>"` for subagents (`agentId` = file stem without `agent-`).
- `name` = `NAMES[zlib.crc32(id) % len(NAMES)]` with `NAMES = ["Ada","Linus","Grace","Alan","Barbara","Ken","Margaret","Dennis","Hedy","Guido","Radia","Bjarne","Frances","Edsger","Katherine","Donald","Anita","Niklaus","Joan","Yukihiro","Sophie","Brian","Lynn","Larry"]`.
- `colorSeed` = `zlib.crc32(id.encode())` (uint32).
- `project` = basename of `cwd` (from first line that has it); fallback: project-dir name after its last `-`. `projectPath` = `cwd` or `null`.
- `title` = latest `ai-title.aiTitle` (main); `meta.json.description` (subagent); else `null`.
- `agentType` = `meta.json.agentType` (subagent) else `null`.

### 2.4 Constants (`pixel_office/config.py`)

```python
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
POLL_INTERVAL = 1.0              # s, tail tracked files
DISCOVERY_INTERVAL = 5.0         # s, glob for new files
DISCOVERY_WINDOW = 1800          # s
INITIAL_TAIL_BYTES = 262_144
MAX_LINE_BYTES = 8_388_608       # longer partial buffer → drop it
PERMISSION_WAIT = 8              # s
IDLE_TO_SLEEP = 180              # s
SILENCE_TO_SLEEP = 600           # s
REMOVE_AFTER = 1800              # s
SUBAGENT_REMOVE_AFTER = 120      # s
SSE_KEEPALIVE = 15               # s
SSE_RETRY_MS = 2000
SNIPPET_LEN = 140
DETAIL_LEN = 60
INTERACTIVE_TOOLS = frozenset({"AskUserQuestion", "ExitPlanMode"})
LONG_RUNNING_TOOLS = frozenset({"Bash", "Agent", "Task", "WebFetch", "WebSearch", "Monitor", "BashOutput"})
NAMES = [...]                    # §2.3
```

---

## 3. Protocol

### 3.1 CLI

```
python3 server.py [--demo] [--port 8765] [--host 127.0.0.1] [--projects-dir ~/.claude/projects] [--poll 1.0]
```
- `--demo`: run `DemoSimulator` instead of `Watcher` (mode `"demo"`).
- `--projects-dir`: expanded with `expanduser()`; if missing, log a warning and keep polling (it may appear later).
- Prints `Pixel Office running at http://<host>:<port>  (mode: live|demo)`. Ctrl+C → stop threads, `server.shutdown()`, exit 0.

### 3.2 HTTP endpoints

| Method/path | Response |
|---|---|
| `GET /` | `web/index.html` |
| `GET /<path>` | static file under `web/` (path resolved, must stay inside `WEB_ROOT`, else 404). MIME: `.html` `text/html; charset=utf-8`, `.js` `text/javascript; charset=utf-8`, `.css` `text/css; charset=utf-8`, `.json` `application/json`, `.svg` `image/svg+xml`, `.png` `image/png`, `.ico` `image/x-icon`. `Cache-Control: no-store`. |
| `GET /api/agents` | `200 application/json`: `{"mode": "live"|"demo", "serverTime": <ms>, "agents": [Agent, ...]}` sorted by `startedAt` then `id` |
| `GET /api/health` | `{"ok": true, "mode": "...", "agents": <count>}` |
| `GET /events` | SSE stream (§3.4) |
| anything else | 404 JSON `{"error":"not found"}`; non-GET → 405 |

Server: `ThreadingHTTPServer`, `daemon_threads = True`, default `protocol_version` (HTTP/1.0). Static and API responses send `Content-Length`. Suppress per-request logging except errors.

### 3.3 Agent JSON schema (camelCase; times are integer epoch **milliseconds**)

```json
{
  "id": "d3c4193c-5d1e-4ef1-94ba-fa9fbb3a6d39",
  "name": "Grace",
  "project": "Pixel",
  "projectPath": "/home/user/Desktop/Pixel",
  "title": "Pixel Office architecture",
  "state": "working",
  "tool": "Bash",
  "detail": "Validate ASCII map dimensions",
  "lastMessage": "Map is consistent and fully reachable…",
  "lastPrompt": "You are a senior software architect…",
  "isSubagent": false,
  "parentId": null,
  "agentType": null,
  "model": "claude-opus-5-5",
  "colorSeed": 2840193311,
  "toolCount": 42,
  "startedAt": 1791722114632,
  "stateSince": 1791723950000,
  "updatedAt": 1791723961000
}
```

| field | type | notes |
|---|---|---|
| id | string | §2.3 |
| name | string | display name |
| project | string | short project name |
| projectPath | string \| null | |
| title | string \| null | |
| state | `"working"\|"thinking"\|"waiting_for_user"\|"idle"\|"sleeping"` | |
| tool | string \| null | only for working / waiting_for_user |
| detail | string \| null | ≤ 60 chars |
| lastMessage | string \| null | ≤ 140 chars |
| lastPrompt | string \| null | ≤ 140 chars |
| isSubagent | bool | |
| parentId | string \| null | parent session id for subagents |
| agentType | string \| null | subagent type |
| model | string \| null | |
| colorSeed | int (uint32) | client palette seed |
| toolCount | int | |
| startedAt | int ms | first seen line timestamp |
| stateSince | int ms | when `state` last changed |
| updatedAt | int ms | last transcript activity (`effective_last_write`) |

### 3.4 SSE (`GET /events`)

Headers: `Content-Type: text/event-stream; charset=utf-8`, `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`.

Frames (UTF-8, `\n` separators, data is single-line JSON):
```
retry: 2000

id: 17
event: snapshot
data: {"mode":"live","serverTime":1791723961000,"agents":[...]}

id: 18
event: agent_updated
data: {...Agent...}

: ping
```

| event | data | when |
|---|---|---|
| `snapshot` | same as `/api/agents` body | first event on every connection |
| `agent_added` | Agent | new agent registered |
| `agent_updated` | Agent (full object) | any field changed |
| `agent_removed` | `{"id": "..."}` | agent pruned |

Keep-alive comment `: ping` every 15 s of inactivity. `id` = global monotonically increasing seq from the store; clients ignore `Last-Event-ID` semantics — every (re)connect gets a fresh snapshot. Write errors (`BrokenPipeError`, `ConnectionResetError`) end the handler and unsubscribe.

---

## 4. Office map

- Tile size **16 px**, render scale **3×** → canvas 22×16×3 = **1056 × 672** CSS px (world size 352 × 224 px).
- Grid **22 columns × 14 rows**, origin top-left, `(x, y)` = (column, row).

### 4.1 ASCII map (verified: 14 rows × 22 chars, all walkable tiles reachable from spawn)

```
   0000000000111111111122   ← column index (x), tens
   0123456789012345678901   ← column index (x), units
 0 ##WW##WW##WW######WW##
 1 #KK.P...KK..P...kCk.Q#
 2 #...............ccc..#
 3 #.TT..TT..TT.........#
 4 #.h...h...h....======#
 5 #..............======#
 6 #.TT..TT..TT...======#
 7 #.h...h...h..........#
 8 #.............###.####
 9 #.TT..TT..TT..#......#
10 #.h...h...h...#bb==bb#
11 #.............#BB==BB#
12 #P...........P#.....P#
13 ####D#################
```

Exact rows for `office.js` (`MAP_ROWS`):
```js
export const MAP_ROWS = [
  "##WW##WW##WW######WW##",
  "#KK.P...KK..P...kCk.Q#",
  "#...............ccc..#",
  "#.TT..TT..TT.........#",
  "#.h...h...h....======#",
  "#..............======#",
  "#.TT..TT..TT...======#",
  "#.h...h...h..........#",
  "#.............###.####",
  "#.TT..TT..TT..#......#",
  "#.h...h...h...#bb==bb#",
  "#.............#BB==BB#",
  "#P...........P#.....P#",
  "####D#################",
];
```

### 4.2 Legend

| char | tile | walkable | sprite / notes |
|---|---|---|---|
| `#` | wall | no | `wall` tile |
| `W` | window (in wall) | no | `window` tile |
| `D` | door | yes | `door` tile; floor underneath |
| `.` | floor | yes | `floor` (checker with `floor_alt` when `(x+y)%2==1`) |
| `=` | rug | yes | `rug` tile |
| `T` | desk (pair `TT` = one desk, 2 tiles) | no | `desk` / `desk_on` 32×24 at left `T` |
| `h` | chair = desk seat spot | spot-only | `chair` 16×16; floor underneath |
| `K` | bookshelf | no | `bookshelf` 16×24 per tile |
| `P` | plant | no | `plant` 16×24 |
| `k` | coffee counter | no | part of `coffee_counter` 48×24 drawn at left `k` of `kCk` |
| `C` | coffee machine | no | part of `coffee_counter` |
| `c` | coffee standing spot | spot-only | floor underneath |
| `Q` | water cooler | no | `water_cooler` 16×24 |
| `b` | bed head = sleep spot | spot-only | `bed` 16×32 drawn at `b`, covering `b` and the `B` below |
| `B` | bed foot | no | (covered by `bed` sprite; floor underneath) |

**Walkable rules:** `.`, `=`, `D` are freely walkable. Spot tiles `h`, `c`, `b` are walkable **only as the start or goal** of a path (no through-traffic). Everything else blocks. Movement is 4-directional (no diagonals). Characters do not collide with each other.

### 4.3 Spots

Facing: direction the character faces when settled.

| kind | index | tile (x,y) | facing | notes |
|---|---|---|---|---|
| desk | 0 | (2,4) | up | desk at (2,3)-(3,3) |
| desk | 1 | (6,4) | up | desk (6,3) |
| desk | 2 | (10,4) | up | desk (10,3) |
| desk | 3 | (2,7) | up | desk (2,6) |
| desk | 4 | (6,7) | up | desk (6,6) |
| desk | 5 | (10,7) | up | desk (10,6) |
| desk | 6 | (2,10) | up | desk (2,9) |
| desk | 7 | (6,10) | up | desk (6,9) |
| desk | 8 | (10,10) | up | desk (10,9) |
| coffee | 0 | (16,2) | down | |
| coffee | 1 | (17,2) | down | in front of machine |
| coffee | 2 | (18,2) | down | |
| sleep | 0 | (15,10) | down | bed (15,10)-(15,11), entered from (15,9) |
| sleep | 1 | (16,10) | down | entered from (16,9) |
| sleep | 2 | (19,10) | down | entered from (19,9) |
| sleep | 3 | (20,10) | down | entered from (20,9) |

Overflow spots (used when all spots of a kind are taken; plain walkable tiles):
- desk overflow (stand, `type` anim facing up): (12,3), (12,6), (12,9), (13,4), (13,7), (13,10)
- coffee overflow (stand + drink, facing down): (15,4), (17,4), (19,4), (16,6), (18,6), (20,5)
- sleep overflow (lying on rug, sleep anim, no bed): (17,10), (18,10), (17,11), (18,11), (17,9), (18,9)
- If even overflow is exhausted: any random free `.` tile with the same anim.

Door / spawn: **door tile (4,13)**; **spawn tile (4,12)**. Entering characters are created at (4,13) with alpha fading 0→1 over 0.4 s, then path to their target. Leaving characters path to (4,13), then fade 1→0 over 0.4 s and are deleted.

### 4.4 Pixel anchoring

- A character's position `(px, py)` is its **feet point** in world pixels. Standing on tile `(x,y)`: `px = x*16 + 8`, `py = y*16 + 15`.
- Character sprite (16×24) is drawn at `(round(px) - 8, round(py) - 23)` (at desk seats too; the sit frames place the body appropriately within the frame).
- Sleeping on a bed spot `(x,y)`: sprite drawn at `(x*16, y*16 + 2)` (lies over the bed sprite, which is drawn at `(x*16, y*16)` and is 16×32). Sleep on overflow rug: same rule.
- Furniture of height H drawn at `(x*16, y*16 + 16 - H)` with `(x,y)` = its top-left tile in the map, except `bed` (drawn at `(x*16, y*16)`).

---

## 5. Sprites (`web/js/sprites.js`)

### 5.1 Format

Every sprite is defined as an array of equal-length strings + a palette object mapping single characters to CSS colors (`'.'` = transparent). A generic builder renders it to an `HTMLCanvasElement` (via `document.createElement('canvas')`, `imageSmoothingEnabled = false`, `fillRect` per pixel or `ImageData`). All results are cached (`Map` keyed by sprite name + palette key + flip). Left-facing frames are produced by horizontally mirroring right-facing frames.

### 5.2 Character (16 × 24)

Character map palette keys (used in all character frames):
`K` outline (#1b1b2a fixed), `S` skin, `s` skin shade, `H` hair, `h` hair shade, `E` eyes (#1b1b2a fixed), `W` white (#f4f4f4 fixed, eye whites / mug), `T` shirt, `t` shirt shade, `P` pants, `p` pants shade, `O` shoes (#3a2a22 fixed), `M` mug (#c8553d fixed), `B` blanket (#5b7bd5 fixed), `b` blanket shade (#46609f fixed).

Animations (`dir` ∈ `down|up|right|left`; `left` = mirrored `right`):

| anim | dirs | frames | fps | description |
|---|---|---|---|---|
| `stand` | down, up, right, left | 1 | – | idle standing |
| `walk` | down, up, right, left | 4 | 8 | step L, pass, step R, pass |
| `sit` | up | 1 | – | seated at desk, back to camera (thinking / waiting) |
| `type` | up | 2 | 6 | seated, arms alternating on keyboard |
| `drink` | down | 2 | 1.5 | frame0 mug at chest, frame1 mug at mouth |
| `sleep` | down | 2 | 1 | lying face up eyes closed under blanket; blanket rises 1 px in frame1 |

Calling a single-dir anim with another dir returns its only dir.

Per-agent palette: `makeCharacterPalette(seed)` uses a seeded PRNG (mulberry32) to pick: SKIN 6 tones (`#f5d0b0,#e8b48f,#d39a6a,#b07a4f,#8a5a3b,#5e3d29`, shade = 15 % darker), HAIR 8 (`#2b2b2b,#5a3825,#8b5a2b,#d8b04a,#b8432f,#e8e0d0,#3b4fa8,#7a3fa0`), SHIRT 12 (`#d64545,#e07b39,#e3c14b,#5fae4f,#3f9a8f,#3f7fd0,#5050c8,#9a50c8,#d050a0,#7a7a7a,#f0f0f0,#2f3a4a`), PANTS 6 (`#2f3a5a,#3a3a3a,#5a4630,#2a4a3a,#4a4a6a,#1f2a44`). Shade colors are computed (darken 15 %). Subagents: the client overrides `shirt`/`shirtShade` with the parent's palette if the parent exists.

### 5.3 Tiles & furniture

| name | size (w×h) | notes |
|---|---|---|
| `floor` | 16×16 | wood planks |
| `floor_alt` | 16×16 | slightly different tone |
| `rug` | 16×16 | tileable pattern |
| `wall` | 16×16 | wall face with top trim |
| `window` | 16×16 | wall with glass |
| `door` | 16×16 | open doorway with frame, mat |
| `desk` | 32×24 | desk, monitor (off) on left half, keyboard, papers/mug right half |
| `desk_on` | 32×24 | same, monitor lit (blue-green screen with code lines) |
| `chair` | 16×16 | office chair, seen from front-top |
| `bookshelf` | 16×24 | books |
| `plant` | 16×24 | potted plant |
| `coffee_counter` | 48×24 | counter with coffee machine centered, cups |
| `water_cooler` | 16×24 | |
| `bed` | 16×32 | pillow at top, blanket bottom |

### 5.4 Bubbles & icons

`getBubbleSprite(kind)` kinds: `zzz` (12×10), `dots` (14×10, "..." thought bubble), `alert` (10×12, red "!"), `steam` (8×8, coffee steam wisp), `question` (10×12, "?"). Each kind has 2 frames for animation (frame index argument).

`getToolIcon(toolName)` returns 10×10 icon inside a small white bubble: `Bash`→terminal `>_`, `Read`→eye/page, `Edit`/`Write`/`NotebookEdit`→pencil, `Grep`/`Glob`→magnifier, `WebFetch`/`WebSearch`→globe, `Agent`/`Task`→two people, `TodoWrite`→checklist, others→gear.

### 5.5 Export API (exact)

```js
export const TILE = 16;
export const CHAR_W = 16;
export const CHAR_H = 24;
/** @type {Record<string,{frames:number,fps:number,dirs:string[]}>} */
export const ANIMS;              // per §5.2 table

/** @returns {{skin,skinShade,hair,hairShade,shirt,shirtShade,pants,pantsShade,key:string}} colors as '#rrggbb'; key = unique cache key */
export function makeCharacterPalette(seed /* uint32 */);

/** @returns {HTMLCanvasElement} 16x24. frame is taken modulo ANIMS[anim].frames. */
export function getCharacterFrame(palette, anim, dir, frame);

/** name ∈ floor|floor_alt|rug|wall|window|door. @returns {HTMLCanvasElement} 16x16 */
export function getTileSprite(name);

/** name ∈ desk|desk_on|chair|bookshelf|plant|coffee_counter|water_cooler|bed. @returns {HTMLCanvasElement} */
export function getFurnitureSprite(name);

/** kind ∈ zzz|dots|alert|steam|question. @returns {HTMLCanvasElement} */
export function getBubbleSprite(kind, frame = 0);

/** @returns {HTMLCanvasElement} ~12x12 including bubble border */
export function getToolIcon(toolName);

/** Dev self-check: unequal row lengths, unknown palette chars, wrong dims. @returns {string[]} errors ([] = ok) */
export function validateSprites();
```

`sprites.js` has no imports and no side effects at import time other than defining data (canvases are built lazily).

### 5.6 Phase 2 implementation notes (additive; the §5.5 API is unchanged)

- Extra exports: `TILE_NAMES`, `FURNITURE_NAMES`, `BUBBLE_KINDS`, `TOOL_ICON_KINDS`, `toolIconKind(toolName)` (tool name → icon kind), `listSprites()` (used by `web/sprite-preview.html`).
- `ANIMS[*].fps` is `0` for the single-frame anims (`stand`, `sit`), where the table says "–".
- Extra character palette keys (fixed or derived, not part of `makeCharacterPalette`'s return value): `r` blush (skin mixed with pink), `C`/`c`/`g` chair fabric/shade/metal. The `sit`/`type` frames draw the chair backrest themselves (frame rows 17–21 = `chair` rows 9–13), so the seated character reads as sitting *in* the chair when seen from behind. The overflow `type` anim at standing desk spots therefore also shows a chair back; that's intended.
- The character canvas cache key is built from the palette's actual colours, not `palette.key`. A client that overrides `shirt`/`shirtShade` (subagents) gets correct sprites without recomputing `key`.
- Extra bubble kinds: `coffee` (12×12 cup-in-bubble) and `speech` (14×10). Both have 2 frames like the others.
- Unknown tile, furniture or bubble names return a 16×16 magenta/black placeholder and log one `console.warn`. They don't throw. Unknown anims fall back to `stand`, and unknown dirs to `down`.
- Tool icons are always 12×12. Mapping: Bash/BashOutput/KillShell/Monitor → terminal; Read → eye; Edit/Write/MultiEdit/NotebookEdit → pencil; Grep/Glob → magnifier; WebFetch/WebSearch (any `Web*`) → globe; Agent/Task → people; TodoWrite → checklist; everything else → gear.

---

## 6. JavaScript module interfaces

All modules are ES modules under `web/js/`, imported with relative paths (`./office.js`). No globals except `window.pixelOffice` debug handle set by `main.js`.

### 6.1 `office.js`

```js
export const MAP_W = 22, MAP_H = 14, TILE = 16, SCALE = 3;
export const MAP_ROWS;                    // §4.1
export const DOOR = {x: 4, y: 13};
export const SPAWN = {x: 4, y: 12};

/** @typedef {{kind:'desk'|'coffee'|'sleep', index:number, x:number, y:number, facing:'up'|'down'|'left'|'right', overflow:boolean, occupant:string|null, furnitureX?:number, furnitureY?:number}} Spot */
/** @typedef {{kind:string, x:number, y:number, w:number, h:number, sortY:number, spot?:Spot}} Furniture  (w,h in px) */

export class Office {
  constructor(rows = MAP_ROWS);
  width; height;                          // tiles
  tileAt(x, y);                           // char, '#' if out of bounds
  isWalkable(x, y);                       // true for . = D
  isSpotTile(x, y);                       // h c b
  /** passable for pathfinding: walkable, or spot tile equal to start/goal */
  isPassable(x, y, start, goal);
  spots;                                  // {desk: Spot[], coffee: Spot[], sleep: Spot[]} incl. overflow (overflow:true after regular ones)
  furniture;                              // Furniture[] parsed from map (desk at left T with spot link, counter at left k, bed at b, ...)
  /** Reserve a free spot of kind for agentId; prefer `preferIndex` if free; regular before overflow; returns null only if none incl. overflow (then caller picks random floor). Releases any previous spot held by agentId. */
  reserveSpot(kind, agentId, preferIndex = -1);
  releaseSpot(agentId);
  spotOf(agentId);                        // Spot|null
  randomFloorTile(rng = Math.random);     // {x,y} walkable '.' tile
}
```
Furniture `sortY` (y-sort key): `(bottom tile row + 1) * 16 - 1` for blocking furniture; `chair` uses `y*16 + 1` and `bed` uses `y*16 + 1` (of the `b` tile) so the seated/sleeping character is drawn on top.

### 6.2 `pathfinding.js`

```js
/**
 * A* 4-neighbour, cost 1/step, Manhattan heuristic, binary-heap open set, deterministic tie-break (lower f, then lower h, then insertion order).
 * @param {(x:number,y:number)=>boolean} passable  e.g. (x,y)=>office.isPassable(x,y,start,goal)
 * @returns {{x:number,y:number}[]|null} tiles from the first step to goal inclusive; [] if start equals goal; null if unreachable.
 */
export function findPath(width, height, passable, start, goal);
```

### 6.3 `agents.js`

```js
export const WALK_SPEED = 40;     // px/s (2.5 tiles/s)
export const MIN_DWELL = 2.0;     // s a character stays at a spot before re-targeting (except leaving)
export const FADE_TIME = 0.4;     // s

export const STATE_TARGET = {     // server state → {spot kind, anim when settled, bubble}
  working:          {spot: 'desk',   anim: 'type',  bubble: 'tool'},
  thinking:         {spot: 'desk',   anim: 'sit',   bubble: 'dots'},
  waiting_for_user: {spot: 'desk',   anim: 'sit',   bubble: 'alert'},
  idle:             {spot: 'coffee', anim: 'drink', bubble: 'steam'},
  sleeping:         {spot: 'sleep',  anim: 'sleep', bubble: 'zzz'},
};
// Overflow desk spots use anim 'type'/'stand' standing (dir 'up'); bubble rules unchanged.

export class Character {
  constructor(agent /* server JSON */, office, {entering = true, parentPalette = null} = {});
  id; agent;                    // latest server JSON
  palette;                      // makeCharacterPalette(agent.colorSeed) (+ parent shirt override)
  px; py;                       // feet position, world px
  dir;                          // 'down'|'up'|'left'|'right'
  phase;                        // 'entering'|'walking'|'settled'|'leaving'|'gone'
  anim; animTime;               // current sprite anim + elapsed seconds
  alpha;                        // 0..1 fade
  path;                         // remaining tiles
  spot;                         // reserved Spot|null
  settledAt;                    // seconds (game time) when phase became 'settled'
  setAgent(agent);              // new server data; if target spot kind changes → retarget (respect MIN_DWELL via pending flag)
  leave();                      // release spot, path to DOOR, phase 'leaving'
  update(dt, time);             // movement, phase transitions, anim selection
  currentFrame();               // {anim, dir, frame} for renderer
  bubble();                     // {kind:'tool'|'dots'|'alert'|'steam'|'zzz', tool?:string}|null; none while walking
  isSleepingOnBed();            // true when settled at a sleep spot (renderer uses lying placement §4.4)
}

export class CharacterManager {
  constructor(office);
  characters;                   // Map<id, Character>
  /** Full snapshot: upsert all; agents missing from list → leave(). initial=true → new characters placed directly at their target spot (no door entry). */
  sync(agents, {initial = false} = {});
  upsert(agent);                // add (entering via door) or setAgent
  remove(id);                   // character.leave()
  update(dt, time);             // update all; delete those with phase 'gone'
  list();                       // Character[] (alive)
}
```
Behaviour: on retarget, `office.reserveSpot(kind, id, preferIndex)` (prefer the last index this character used for that kind), compute `findPath` from the character's current tile (the tile its feet are in; when mid-step, finish the current step first), walk tile-center to tile-center at `WALK_SPEED`, `dir` from the step direction, `anim='walk'`. On arrival: snap to spot, `dir = spot.facing`, `anim = STATE_TARGET[state].anim`, `phase='settled'`. If no path is found, teleport (fade out/in) to the spot. Desk spots are released when going to coffee/sleep. A seated character's desk furniture renders as `desk_on` while state is `working` or `thinking`.

### 6.4 `renderer.js`

```js
export class Renderer {
  constructor(canvas, office);    // sets canvas.width=1056,height=672, ctx.imageSmoothingEnabled=false
  /** Draws one frame. opts: {selectedId:string|null, hoverId:string|null, time:number(s)} */
  render(characters, opts);
  /** canvas-space (CSS px relative to canvas, accounting for CSS scaling) → character id or null; topmost (highest sortY) wins; hit box = 16x24 sprite rect */
  pickCharacter(characters, canvasX, canvasY);
}
```
Draw order per frame:
1. Background canvas (cached once, 352×224): floor/rug/door tiles under every cell (`floor` under furniture), then `wall`/`window` tiles.
2. `ctx.setTransform(SCALE,0,0,SCALE,0,0)`; blit background.
3. Y-sorted drawables (ascending `sortY`, tie → lower x first): furniture (`sortY` §6.1) and characters (`sortY = py`; sleeping on a bed: `bedTileY*16 + 2`). Apply `globalAlpha = character.alpha`. Selected character: 1-px yellow outline (draw the frame offset by ±1 px tinted, or a yellow ellipse under the feet — use **ellipse under feet**, `#ffd84a`, alpha 0.6).
4. Bubbles (world space, after all sprites): centered at `(px, py - 27)` (sleeping: `(px + 6, bedY - 4)`), bob ±1 px at 2 Hz; `tool` bubble uses `getToolIcon(agent.tool)`; `alert` blinks at 2 Hz.
5. Labels (screen space, `setTransform(1,0,0,1,0,0)`): name centered under feet at `(px*3, (py+2)*3)`, `bold 11px monospace`, white with 2-px black outline; subagents prefixed `↳ `; shown for all characters, hidden while sleeping unless hovered/selected.

### 6.5 `ui.js`

UI language: **Turkish**. State labels: working "Çalışıyor", thinking "Düşünüyor", waiting_for_user "Kullanıcıyı bekliyor", idle "Kahve molasında", sleeping "Uyuyor" (updated in Phase 5, see §7.9). State colors: working `#4caf50`, thinking `#42a5f5`, waiting_for_user `#ef5350`, idle `#ffb74d`, sleeping `#9575cd`.

```js
export class UI {
  /** els: {list: HTMLElement, details: HTMLElement, status: HTMLElement, mode: HTMLElement}; onSelect(id|null) */
  constructor(els, {onSelect});
  setAgents(agents);            // re-render list (grouped: main sessions, subagents indented under parent), counts per state
  setSelected(id);              // highlight row, render details
  setConnection(status);        // 'connecting'|'live'|'reconnecting' → banner text: "Bağlanıyor…", "Bağlı", "Yeniden bağlanıyor…"
  setMode(mode);                // 'live'|'demo' → badge "CANLI MOD" / "DEMO MOD"
  tick(nowMs);                  // refresh relative times ("3 dk önce") once per second
}
```
Details panel shows: name, project (+path tooltip), title, state + since, tool + detail, last message, last prompt, model, tool count, agent type, parent (clickable).

`index.html` structure: `<main id="app">` with `<div id="stage"><canvas id="office"></canvas><div id="conn-banner"></div></div>` and `<aside id="panel"><header>Pixel Office <span id="mode-badge"></span></header><ul id="agent-list"></ul><section id="agent-details"></section></aside>`. CSS: canvas `image-rendering: pixelated`, scales down to fit width with `max-width:100%; height:auto` (aspect preserved); panel 320 px right side, stacks below under 1100 px viewport width. Dark theme background `#1e1e2a`.

### 6.6 `main.js`

```js
// Boot (on DOMContentLoaded):
//  office = new Office(); manager = new CharacterManager(office); renderer = new Renderer(canvas, office);
//  ui = new UI({...}, {onSelect: id => selectedId = id});
//  connect(); requestAnimationFrame(loop);
export const STEP = 1 / 60;          // fixed timestep (s)
export const MAX_STEPS = 5;          // per animation frame (drop excess)
export function connect();           // EventSource('/events') with handlers below
```
- SSE: `snapshot` → `manager.sync(agents, {initial: firstSnapshot})`, `ui.setMode(mode)`, `ui.setAgents`; first snapshot after page load is `initial: true`, later ones (after reconnect) `false`. `agent_added`/`agent_updated` → `manager.upsert`; `agent_removed` → `manager.remove`. Always refresh `ui.setAgents(...)` after changes.
- Reconnect: `onopen` → `ui.setConnection('live')`, reset backoff. `onerror` → `ui.setConnection('reconnecting')`; if `es.readyState === EventSource.CLOSED`, close and retry `connect()` after backoff 1 s → 2 s → 4 s → 8 s → max 10 s.
- Loop: accumulate `min(elapsed, 0.25 s)`; while `acc ≥ STEP` and steps < MAX_STEPS: `manager.update(STEP, gameTime)`; then `renderer.render(manager.list(), {selectedId, hoverId, time: gameTime})`; `ui.tick(Date.now())`.
- Input: canvas `click` → `renderer.pickCharacter` → select (click empty = deselect); `mousemove` → hover (cursor pointer). List row click selects too. `Escape` deselects.
- `window.pixelOffice = {office, manager, renderer, ui}` for debugging.

### 6.7 Phase 4 implementation notes (deviations / additions)

- **Page layout:** a full-width top bar `<header id="topbar">` holds the title, `#mode-badge` (CANLI MOD/DEMO MOD) and `#conn-status` (coloured dot + Bağlanıyor…/Bağlı/Yeniden bağlanıyor…). It replaces the `<header>` inside `<aside id="panel">` from §6.5. The panel starts with an "Ajanlar" heading and a `#summary` row of per-state counts, followed by `#agent-list` and `#agent-details`. `#conn-banner` overlays the canvas whenever the connection is not live. The canvas sits inside a `.frame` wooden border. The panel is 340 px wide and stacks below the stage at ≤ 1100 px.
- **Canvas scaling (`Renderer.fit(availW, availH)`, addition):** the constructor still sets 1056×672 (scale 3). `main.js` calls `fit()` on resize via ResizeObserver. The backing store is `world × s` device pixels with an integer `s`. When an integer scale wastes ≤ 15 % of the space, the CSS size is exactly `world × s / dpr`, which keeps it perfectly crisp. Otherwise the canvas fills the space and uses the next integer backing scale. Only `style.width` is set and the height follows the aspect ratio. Labels are drawn in device pixels with `font = max(9·min(dpr,s), 11·s/3)` px, so they stay crisp at any size. `pickCharacter` converts using the canvas's CSS rect.
- **office.js extras:** `reserveFloorSpot(kind, agentId, rng)` covers the "random free floor tile" fallback. It creates an ad-hoc spot (`index: -1`, `overflow: true`) on a `.` tile that is not used by any spot or held reservation, so even then two characters never share a tile. `reserveSpot` returns the already-held spot when the agent holds one of the same kind (and `preferIndex` does not ask for a different one). `office.rows` exposes the map.
- **agents.js extras:** the `Character` constructor takes an extra `time` option (game time, so initial-snapshot characters count as having dwelt long enough). Added `setParentPalette(p)`, `comeBack()` (an agent re-added while leaving turns around), a `sortY` getter (`py`, or `bedY*16+2` when lying) and `CharacterManager.upsert(agent, {entering})`. `sync` upserts main sessions before subagents, and a parent that arrives later recolours its existing subagents. A retarget during a walk is applied when the current step ends. A state change while settled is deferred until `MIN_DWELL` has passed. A change between states that use the same spot kind (working ↔ thinking ↔ waiting) only switches the animation. Overflow desk spots show `type` while working and `stand` otherwise. A* receives `isPassable(x,y,start,goal)`, and a `null` path triggers a fade-out/in teleport.
- **Bubbles:** `idle` draws the extra `coffee` bubble (cup) above the head plus a small `steam` wisp next to the mug, instead of only `steam`. Bob is ±1 px at 2 Hz with a per-character phase offset derived from `colorSeed`, so characters do not bob in sync. `alert` blinks at 2 Hz. Hovered characters get a faint white ellipse and selected ones the yellow `#ffd84a` ellipse (lying: a vertical ellipse over the bed). `desk_on` is used exactly as specified (settled at that desk in `working`/`thinking`).
- **SSE reconnect:** on *every* `onerror` the client closes the `EventSource` and reconnects itself with backoff 1→2→4→8→10 s (reset on `onopen`), instead of only when `readyState === CLOSED`. Otherwise the browser's own 2 s auto-retry would bypass the backoff. Every reconnect gets a fresh snapshot → `manager.sync(…, {initial:false})`, so new agents walk in and missing ones walk out.
- **Loop:** when `MAX_STEPS` is hit, the accumulator is clamped to one step, which drops excess time instead of spiralling.
- **ui.js extras:** `els.banner` and `els.summary` (optional), `opts.colorOf(agent)` (swatch = the character's actual shirt colour, including the parent override), `setServerTime(ms)` (clock skew between server and client for elapsed times), and exports `STATE_LABELS`, `STATE_COLORS`, `formatDuration`. Labels follow §6.5 (Çalışıyor / Düşünüyor / Kullanıcıyı bekliyor / Kahve molasında / Uyuyor). The list shows a swatch, name, state badge, project, current tool + detail, and time in the current state. Details add start time, last activity, id and a list of subagents. When there are no agents the list shows an empty-office message.
- **Debug:** `?debug` in the URL shows an overlay (fps, game time, per-character state/phase/anim/spot, captured errors). `window.pixelOffice` additionally exposes `agents` (latest server JSON) and `errors`.
- **Verification note:** `--virtual-time-budget` screenshots never finish with an open SSE stream. Phase 4 drove headless Chrome over the DevTools protocol instead, with a small stdlib-only Python WebSocket client, and took screenshots in real time.

---

## 7. Python module interfaces

All modules: `from __future__ import annotations` not required (3.14). Type hints everywhere. Logging via `logging.getLogger("pixel_office")`.

### 7.1 `models.py`

```python
class State(enum.StrEnum):
    WORKING = "working"; THINKING = "thinking"; WAITING = "waiting_for_user"; IDLE = "idle"; SLEEPING = "sleeping"

@dataclass(slots=True)
class AgentInfo:
    id: str
    name: str
    project: str
    state: State
    project_path: str | None = None
    title: str | None = None
    tool: str | None = None
    detail: str | None = None
    last_message: str | None = None
    last_prompt: str | None = None
    is_subagent: bool = False
    parent_id: str | None = None
    agent_type: str | None = None
    model: str | None = None
    color_seed: int = 0
    tool_count: int = 0
    started_at: int = 0      # ms
    state_since: int = 0     # ms
    updated_at: int = 0      # ms
    def to_dict(self) -> dict: ...   # camelCase keys exactly as §3.3, state as str

def name_for(agent_id: str) -> str
def color_seed_for(agent_id: str) -> int
def now_ms() -> int
```

### 7.2 `store.py`

```python
Event = NamedTuple("Event", [("seq", int), ("name", str), ("data", dict)])

class AgentStore:
    def __init__(self, mode: str) -> None              # mode "live" | "demo"
    mode: str
    def upsert(self, agent: AgentInfo) -> bool         # emits agent_added (new) or agent_updated (to_dict differs); returns True if emitted
    def remove(self, agent_id: str) -> bool            # emits agent_removed {"id"}
    def get(self, agent_id: str) -> AgentInfo | None
    def ids(self) -> set[str]
    def snapshot(self) -> dict                         # {"mode","serverTime","agents":[...]} sorted by startedAt, id
    def subscribe(self, maxsize: int = 1000) -> queue.Queue[Event]   # caller then sends snapshot itself
    def unsubscribe(self, q: queue.Queue[Event]) -> None
```
Thread-safe via one `threading.Lock`; events are pushed to all subscriber queues with `put_nowait`; a subscriber whose queue is full is dropped (its SSE handler notices via a sentinel `Event(-1, "overflow", {})` and closes; client reconnects and re-snapshots). `subscribe` + snapshot must be atomic with respect to emits: provide `subscribe_with_snapshot(self) -> tuple[queue.Queue[Event], dict, int]` returning (queue, snapshot, seq) under the lock — **httpd uses this one**.

### 7.3 `transcript.py`

```python
def parse_line(raw: str) -> dict | None                  # json.loads, None on error / non-dict
def parse_ts(value: str | None) -> float | None          # ISO → epoch seconds
def summarize_tool_input(name: str, inp: dict) -> str    # §2.3, ≤ DETAIL_LEN
def snippet(text: str, n: int = SNIPPET_LEN) -> str      # collapse whitespace, truncate with "…"

class SessionTracker:
    def __init__(self, agent_id: str, path: Path, *, is_subagent: bool, parent_id: str | None,
                 project_dir_name: str, meta: dict | None = None) -> None
    agent_id: str; path: Path; is_subagent: bool; parent_id: str | None
    last_write: float                                    # set by watcher from st_mtime
    def feed(self, obj: dict, ingest_ts: float) -> None  # update facts (§2.3)
    def derive_state(self, now: float, effective_last_write: float) -> State
    def to_agent(self, now: float, effective_last_write: float) -> AgentInfo   # calls derive_state, maintains state_since
```

### 7.4 `watcher.py`

```python
class FileTail:
    def __init__(self, path: Path, initial_tail_bytes: int = INITIAL_TAIL_BYTES) -> None
    offset: int
    def read_new_lines(self) -> list[str]
    # opens in 'rb', seeks to offset, reads to EOF, prepends buffered partial bytes, splits on b"\n";
    # last piece without "\n" stays buffered (not parsed); decodes each complete line utf-8 errors="replace".
    # First call with size > initial_tail_bytes: seek to size - initial_tail_bytes and drop the first line.
    # If size < offset (truncated/replaced): reset offset=0, clear buffer. Buffer > MAX_LINE_BYTES → discard.

class Watcher(threading.Thread):
    def __init__(self, projects_dir: Path, store: AgentStore, poll_interval: float = POLL_INTERVAL) -> None  # daemon=True
    def run(self) -> None                      # loop: scan(time.time()); wait(poll_interval) on stop event
    def stop(self) -> None
    def scan(self, now: float) -> None         # one iteration (unit-testable):
        # 1) every DISCOVERY_INTERVAL: glob "*/*.jsonl" and "*/*/subagents/agent-*.jsonl"; start tracking per §2.3
        #    (subagent: read sibling .meta.json once; parent_id = parent dir name of "subagents")
        # 2) for each tracked file: stat (missing → remove); if size/mtime changed → FileTail lines → parse_line → tracker.feed
        # 3) compute effective_last_write for main sessions (max with children)
        # 4) store.upsert(tracker.to_agent(now, elw)) for all; apply removal rules (§2.3) → store.remove
```
All exceptions inside `scan` per file are caught and logged (one bad file never kills the thread).

### 7.5 `simulator.py`

```python
class DemoSimulator(threading.Thread):
    def __init__(self, store: AgentStore, n_agents: int = 5, seed: int | None = None, tick: float = 0.5) -> None  # daemon=True
    def run(self) -> None
    def stop(self) -> None
```
Script: agents `demo-1`…`demo-5`, projects `pixel-office`, `web-shop`, `ml-pipeline`, `docs-site`, `api-gateway`; spawned staggered at t = 0, 2, 4, 6, 8 s. Each agent loops through a cycle: `thinking` 2–5 s → `working` 4–12 s (tool changes every 2–4 s among Bash/Read/Edit/Write/Grep/Glob/WebFetch/TodoWrite with plausible details such as `"pytest -q"`, `"office.js"`, `"TODO"`) → repeat 2–5 times → with 25 % chance `waiting_for_user` 5–8 s (tool `Edit`) → `idle` 8–15 s → repeat; after 3 such cycles `sleeping` 20–30 s → removed, and a new agent `demo-N+1` spawns 3 s later (so door entry/exit is visible). `demo-2` additionally spawns a subagent (`demo-2:sub-K`, `agentType` "Explore", parent tool `Agent`) during one of its working phases; the subagent works 10–20 s, then `sleeping`, and is removed after 15 s. Every state change updates `state_since`, `updated_at`, `tool`, `detail`, `last_message` (canned Turkish/English snippets) and calls `store.upsert`.

### 7.6 `httpd.py`

```python
class PixelOfficeServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, addr: tuple[str, int], store: AgentStore, web_root: Path) -> None
    store: AgentStore; web_root: Path; shutting_down: threading.Event

class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None         # routing §3.2
    def handle_sse(self) -> None     # subscribe_with_snapshot → write retry + snapshot → loop q.get(timeout=SSE_KEEPALIVE)
                                     # → write frame / ": ping"; exit on write error, overflow sentinel, or server.shutting_down
    def log_message(self, fmt, *args) -> None   # silent

def make_server(host: str, port: int, store: AgentStore, web_root: Path = WEB_ROOT) -> PixelOfficeServer
```

### 7.7 `server.py`

```python
def parse_args(argv: list[str] | None = None) -> argparse.Namespace
def main(argv: list[str] | None = None) -> int
# store = AgentStore("demo" if args.demo else "live")
# producer = DemoSimulator(store) if args.demo else Watcher(args.projects_dir, store, args.poll)
# producer.start(); srv = make_server(...); srv.serve_forever() in main thread; KeyboardInterrupt → shutting_down.set(), producer.stop(), srv.shutdown()/server_close()
if __name__ == "__main__": raise SystemExit(main())
```

### 7.8 Phase 3 implementation notes (deviations / additions; all §3 wire formats unchanged)

- **Subagent completion (addition to §2.3).** Real subagent transcripts do *not* contain the final report / `end_turn` line — the last line is usually a `tool_result`, so rule 4 never fires and a finished subagent would sit in `thinking` until the 600 s silence rule. The watcher therefore also uses the parent's transcript: every tracker records `tool_results: {tool_use_id → ts}`; if the parent has a `tool_result` for the subagent's `meta.json.toolUseId`, the subagent gets `finished_ts` and derives **sleeping** (new rule 1b, right after `ended`). If the subagent writes new user/assistant lines later (resumed via SendMessage), `last_event_ts > finished_ts` and the normal rules apply again.
- **`stateSince` is the natural start of the state**, not the moment the server noticed it: the timestamp of the line that caused it (tool_use, prompt, end_turn, cost-state, parent tool_result), `effective_last_write + 600` for silence-sleep, `ts + 8` for permission-wait, `end_turn + 180` for idle→sleep; clipped to `now`, and only changed when the state changes.
- **Removal rules are evaluated before publishing.** A main session with `now - effective_last_write ≥ 1800` and a subagent that has been sleeping (by `stateSince`) for ≥ 120 s are dropped without ever being upserted — so long-finished subagents found at startup do not briefly appear.
- **Discovery:** a subagent file inside the 1800 s window forces tracking of its parent session even if the parent's own mtime is older (or it was removed before) — the parent is active through its child (`effective_last_write`). Subagent `.meta.json` is re-read on each poll until it exists; invalid meta → `{}` + warning.
- **`startedAt` with a tailed file:** when the first read skipped the head (file > 256 KB), the first timestamp is taken from the first 64 KB of the file, so `startedAt` is the real session start.
- **FileTail:** also restarts from 0 when the inode/device changes (file replaced); the initial tail only drops the first line if the tail offset is not already at a line start; an oversized line (> `MAX_LINE_BYTES`) is discarded *up to its terminating newline* (the rest is not parsed as a new line); `\r\n` and blank lines are tolerated. Appended data is read in 1 MiB chunks.
- **Tracker robustness:** `tool_use` blocks without an `id` get a synthetic one; assistant `model` values like `<synthetic>` (API-error turns) are ignored; interrupt texts do not overwrite `lastPrompt`.
- **store.py extras:** `count()`, `subscriber_count()`, module constant `OVERFLOW` (the `Event(-1, "overflow", {})` sentinel). `upsert` stores a copy, so callers may keep mutating their `AgentInfo`.
- **httpd.py:** extra MIME types `.mjs`, `.htm`, `.txt`, `.md`; JSON responses are `application/json; charset=utf-8`; `HEAD`/`POST`/… → 405 JSON with `Allow: GET`. The SSE handler polls its queue every 0.5 s (to notice `shutting_down` quickly) and sends `: ping` after 15 s without any write. Client disconnects (`OSError` family) end the handler silently and unsubscribe; `handle_error` swallows connection errors (no traceback spam).
- **simulator.py:** public `step(now)` drives everything (unit-testable with a fake clock; `run()` calls it every `tick`). Extras beyond §7.5: `demo-1` sleeps after **one** cycle so a leave + replacement is visible within ~1 min; every 5th agent (`demo-2`, `demo-7`, …) spawns one subagent; the parent shows tool `Agent` and keeps working until its subagent is asleep; `waiting_for_user` uses `AskUserQuestion` 30 % of the time (else `Edit`); `WebSearch` is in the tool mix; models alternate between `claude-opus-5-5` and `claude-sonnet-5`.
- **server.py:** extra flags `-v/--verbose` (INFO logging) and `--version`; SIGTERM is handled like Ctrl+C, and SIGINT is re-armed explicitly (works even when started in the background with `&`); port already in use → message + exit 1. The startup line prints `localhost` instead of `127.0.0.1`/`0.0.0.0`.
- **Tests** were started in Phase 3 (`tests/test_transcript.py`, `test_watcher.py`, `test_store.py`, `test_simulator.py`, `test_httpd.py`, helpers in `tests/_helpers.py`). Run with `python3 -m unittest discover -s tests -v`.

### 7.9 Phase 5 (QA) changes

- **UI wording:** state labels are now working "Çalışıyor", thinking "Düşünüyor", waiting_for_user "Kullanıcıyı bekliyor", idle "Kahve molasında", sleeping "Uyuyor". The mode badge says "CANLI MOD" or "DEMO MOD", and the connection status says "Bağlanıyor…", "Bağlı" or "Yeniden bağlanıyor…". Before this change the header showed both "CANLI" and "Canlı", which was confusing.
- **Demo names:** `name_for(id)` (a crc32 hash) collides. For example, `demo-3` and `demo-8` both map to "Ada". The simulator now starts at the hashed name and takes the first name no live demo agent is using. Live mode still uses `name_for` (§2.3).
- **Overflow promotion (`agents.js`):** `Character.tryUpgradeSpot()` runs from `CharacterManager.update` every 0.5 s of game time. A character that is settled on an overflow spot and has dwelt for `MIN_DWELL` walks to a free regular spot of the same kind. A character on an ad-hoc floor spot (`index -1`) also moves to a free overflow spot. `_retarget(preferIndex)` / `_reserveTarget(preferIndex)` take an optional index.
- **Fade fix:** a character that is re-added while it is fading out at the door (`comeBack()`) now fades back in while walking. Before, it stayed partly transparent for good.
- **ui.js hardening:** every untrusted string still goes through `esc()` before it reaches `innerHTML`. The new `num()` helper forces numeric fields (`stateSince`, `updatedAt`, `startedAt`, `toolCount`) to finite numbers. State colours and labels are looked up with `Object.hasOwn`, so a state such as `constructor` cannot pull values from `Object.prototype`.
- **watcher.py:** the `_removed` memo (path → mtime) drops entries for files that no longer exist on each discovery pass, so it stays bounded.
- **Minimum Python version** is 3.11, because of `enum.StrEnum` and `datetime.fromisoformat` with a `Z` suffix. It was developed and tested on 3.14.

---

## 8. Integration checklist

- Server state strings, SSE event names and JSON keys must match §3 exactly (frontend switches on them).
- `colorSeed` is the only input to character colors → same agent looks the same across reloads.
- `office.js` must assert `MAP_ROWS.length === 14 && every row length === 22` at construction.
- Coordinates: tiles `(x,y)`, world px = tiles × 16, canvas px = world × 3.
- No module may fetch anything except `/events` (and optionally `/api/agents`).
