# Pixel Office v2 — "Bulut Ofis" (Cloud Office) Specification

Status: **v2 design, Phase 6 (2026-10-11)**. This is the binding contract for Phases 7–10, just as
`docs/ARCHITECTURE.md` was for v1. If you must deviate, update this file and log it in `PROJECT.md` →
İlerleme Günlüğü.

**Local mode stays backwards compatible.** `python3 server.py [--demo]` and every v1 endpoint in ARCHITECTURE.md
keep working exactly as before. The local server only gains additive endpoints (rename §15.4, generic status API §16). v2 adds new files and makes only *additive* changes to `web/`
(the page detects which backend it is talking to, §9.1).

Hard constraints: no Node.js, no build step, no CDN/npm dependencies. Backend = Vercel Python
functions (`api/*.py`), **stdlib only** (HTTP to storage via `http.client`/`urllib`). Frontend = vanilla
JS ES modules reusing `web/js/*`. Hook client = one stdlib Python file (3.8+) plus a PowerShell twin.

---

## 0. Decisions at a glance

| Topic | Decision |
|---|---|
| Hosting | Vercel Hobby, file-based Python functions in `api/`, static files from `web/` (`outputDirectory: "web"`), region `fra1` |
| Storage | **Upstash Redis** (Vercel Marketplace, free plan: 500K commands/month), REST API over HTTPS; in-memory fallback when env vars are missing (dev) |
| Realtime | **Adaptive polling** (10 s / 30 s / 60 s, paused when nothing is visible) + **Vercel CDN cache** on the read endpoint (5 s / 15 s / 5 s). A 5 s "boost" right after opening an empty room and around the "Ajan bağla" dialog (§17.10). No websockets, no SSE in the cloud |
| Rooms | Room id = 128-bit random (26 chars base32). **Read capability = room id** (in URL query). **Write capability = write key** = HMAC-SHA256(`PIXEL_SECRET`, room id), stateless, carried only in the URL **fragment** (`#key=`) and in `Authorization: Bearer` |
| Privacy | Hook events are mapped and filtered **on the friend's machine**. Only an allow-list leaves the machine: state, tool name, fixed label, project folder basename (aliasable/hideable), owner name, opaque hashed ids |
| Windows without Python | `pixel_hook.ps1` (Windows PowerShell 5.1, built in): same mapping and filtering, **same privacy**. The zero-install HTTP-hook fallback is **opt-in only** because it sends raw hook JSON to the server (§7.6) |
| Mini window | Document Picture-in-Picture (Chrome/Edge 116+, Firefox 151+), with a positioned popup as fallback; installable PWA (manifest + service worker) |
| Renaming | Inline in the details panel, both modes. Renames are stored separately from state, so state updates can't overwrite them. Cloud: `POST /api/rename` (write key), scope agent or owner-default. Local: `POST /api/agents/rename`, user data dir file, SSE `agent_updated` (§15) |
| Local AI agents | The local server gets the same `POST /api/status` as the cloud (§16). `pixel_agent.py` provides a library, the `pixel-run` wrapper (pty) and `pixel-proxy`, an OpenAI/Ollama passthrough proxy (§17) |
| Binding budget constraint | Vercel Hobby **1M CDN requests/month** (viewer polling). The stress case uses ~79%, a realistic month ~58% (§12, updated in Phase 10) |

---

## 1. Architecture

```
 Friend A's machine                        Vercel (Hobby, region fra1)                Upstash Redis (eu-central-1)
 ┌──────────────────────────┐             ┌──────────────────────────────────┐       ┌──────────────────────────┐
 │ Claude Code              │  hook event │  CDN ───────────── static web/   │       │ HASH po:room:<rid>       │
 │  └ hooks (async) ───────►│ pixel_hook  │   │  (index.html, js, sw.js,     │       │  _meta → {"name",..}     │
 │    stdin JSON            │ .py / .ps1  │   │   manifest, icons, hook/*)   │       │  a3f… → agent record     │
 │                          │  map+filter │   │                              │       │  9bc… → agent record     │
 │ ~/.pixel-office/         │  debounce   │   ├─ POST /api/status ──────────────────►│  HSET (+EXPIRE ≤1/h)     │
 │  config.json, state/     │────HTTPS───►│   │   (Bearer write key)         │       │                          │
 └──────────────────────────┘             │   │                              │       │                          │
 Any script / bot ──── POST /api/status ─►│   ├─ GET /api/state?room= ◄─────────────►│  HGETALL (1 cmd)         │
                                          │   │   s-maxage 5–30 s            │       └──────────────────────────┘
 Browser / PWA / PiP mini window          │   │   (CDN-cached per room)      │
 ┌──────────────────────────┐  poll 10 s  │   ├─ POST /api/rooms (create)    │
 │ web/js: transport.js ────┼────────────►│   ├─ GET  /api/config, /health   │
 │ office, agents, renderer │             │   └─ POST /api/cc_hook (opt-in   │
 │ ui, cloud.js, pip.js     │             │        raw-hook fallback)        │
 └──────────────────────────┘             └──────────────────────────────────┘

 Local mode (unchanged): python3 server.py → ~/.claude/projects transcripts → SSE /events → same web/js
 Cloud dev mode:          python3 dev_server.py → same api handlers + MemoryStorage → same web/
```

Read-time derivation: the server stores only the *last reported* state per agent plus timestamps.
Time-based transitions (idle → sleeping, stale → sleeping, removal) are computed on every
`GET /api/state` (§6). No cron jobs, no background workers.

---

## 2. File layout (new files marked ✚, changed files marked ✎)

```
Pixel/
├── server.py, pixel_office/, tests/test_*.py      v1 local mode — behaviour unchanged; additive edits only:
│   ✎ server.py               + --names-file, --write-key
│   ✎ pixel_office/httpd.py   + POST /api/agents/rename, POST /api/status; /api/health gains "features"
│   ✎ pixel_office/models.py  + nameSource/defaultName in to_dict(); name_for() consults names_store
│   ✚ pixel_office/names_store.py   UI renames, user data dir JSON (§15.4)
│   ✚ pixel_office/reported.py      status-API agents + 1 s derive ticker (§16)
├── ✚ dev_server.py            stdlib dev server: serves web/ + mounts pixel_cloud.app.dispatch at /api/*
├── ✚ vercel.json              Vercel config (§13.2)
├── ✚ .vercelignore            keep v1/local-only files out of the deployment (§13.2)
├── ✚ .python-version          "3.12" (Vercel default; code must also run on 3.11–3.14)
├── ✚ .gitignore, LICENSE      (Phase 10)
├── ✚ api/                     one thin file per endpoint; every file is identical except the docstring:
│   ├── config.py              GET  /api/config
│   ├── health.py              GET  /api/health
│   ├── rooms.py               POST /api/rooms
│   ├── state.py               GET  /api/state?room=
│   ├── status.py              POST /api/status?room=      (OPTIONS for CORS)
│   ├── cc_hook.py             POST /api/cc_hook?room=     (opt-in raw Claude Code HTTP-hook fallback)
│   └── rename.py              POST /api/rename?room=      (§15.3)
├── ✚ pixel_cloud/             shared backend package (stdlib only, Python ≥ 3.11)
│   ├── __init__.py            __version__ = "2.0.0"
│   ├── settings.py            constants (§6.2, §8, §12) + env reading
│   ├── ids.py                 room id generation/validation, write-key HMAC, agent id hashing
│   ├── validate.py            request payload schema + sanitisation (§5.5)
│   ├── derive.py              read-time state machine, record → Agent JSON, owner colours (§6)
│   ├── storage.py             Storage interface, MemoryStorage, UpstashStorage, get_storage() (§4)
│   ├── ratelimit.py           in-process token buckets (§8)
│   ├── app.py                 dispatch(Request) -> Response: routing, auth, caching headers, errors
│   ├── vercel.py              ApiHandler: BaseHTTPRequestHandler adapter around app.dispatch
│   └── demo.py                --demo writer for dev_server (fake friends posting through dispatch)
├── web/
│   ├── ✎ index.html           + landing overlay, room toolbar, setup dialog, manifest/theme meta
│   ├── ✎ css/style.css        + landing/dialog/toolbar styles, `.mini` layout, owner chips
│   ├── ✚ manifest.webmanifest
│   ├── ✚ sw.js                service worker (cloud mode only)
│   ├── ✚ icons/               icon-192.png, icon-512.png, icon-maskable-512.png, apple-touch-icon.png
│   ├── ✚ hook/                served as static text (download source for the one-line installers)
│   │   ├── pixel_hook.py      Claude Code hook client + installer + generic CLI (Python 3.8+, stdlib)
│   │   ├── pixel_hook.ps1     PowerShell 5.1+ twin of pixel_hook.py (hook mode + install/uninstall/test)
│   │   ├── install.sh         macOS/Linux bootstrap (curl | sh)
│   │   ├── install.ps1        Windows bootstrap (irm | iex)
│   │   └── pixel_agent.py     local-agent library + CLI: report / run (pixel-run) / proxy (pixel-proxy) / install-shims (§17)
│   └── js/
│       ├── ✚ transport.js     SseTransport (moved from main.js, behaviour identical) + PollingTransport
│       ├── ✚ cloud.js         backend detection, landing (create/join), recent rooms, share + setup dialogs
│       ├── ✚ pip.js           MiniWindow: Document PiP + popup fallback; scheduler host
│       ├── ✎ main.js          uses a transport instead of inline EventSource; rAF/timer host switch for PiP
│       ├── ✎ agents.js        shirt override from agent.ownerColor (takes priority over parent palette)
│       ├── ✎ renderer.js      label "owner·project" when agent.owner is present
│       ├── ✎ ui.js            mode 'cloud', owner chip + project in rows, owner/source/label in details
│       └── sprites.js, office.js, pathfinding.js   UNCHANGED
├── ✚ tools/make_icons.py      stdlib PNG writer that renders the pixel-art app icon at all sizes
├── ✚ tests/
│   ├── test_cloud_ids.py, test_cloud_validate.py, test_cloud_derive.py, test_cloud_storage.py,
│   ├── test_cloud_app.py      dispatch end-to-end (MemoryStorage + fake-Upstash HTTP server)
│   ├── test_hook_client.py    mapping, debounce, throttle, privacy fuzz, installer merge/uninstall
│   ├── test_rename.py         cloud + local rename, precedence, persistence, SSE agent_updated
│   ├── test_local_status.py   local /api/status parity with cloud, CSRF guard, derive ticker
│   ├── test_pixel_agent.py    library sender policy, pixel-run (pty + pipe), proxy streaming/tool_calls
│   └── hook_vectors.json      shared input → expected-payload vectors (Python and PowerShell clients)
└── docs/CLOUD.md              this file
```

Why `pixel_cloud/` lives outside `api/`: every `.py` in `api/` becomes a function, and Vercel's
underscore-exclusion rule for `api/_lib` was not confirmed in the current docs. Each `api/*.py` file does:

```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pixel_cloud.vercel import ApiHandler

class handler(ApiHandler):        # must be a literal top-level class: `handler = make_handler()` is NOT detected (§17.10)
    pass
```

and `vercel.json` adds `includeFiles` for `pixel_cloud/**` and `web/hook/pixel_hook.py` (§13.2), so it does not rely on implicit bundling.

---

## 3. Identity, auth model, links

### 3.1 Identifiers

| Thing | Format | Generated by | Secret? |
|---|---|---|---|
| `roomId` | 26 chars `[a-z2-7]` = base32(16 random bytes), lowercase, no padding | server (`secrets.token_bytes(16)`) | **Read capability.** Unguessable (128 bit). Appears in URL query (`?room=`), CDN cache key and logs. |
| `writeKey` | 32 chars `[A-Za-z0-9_-]` = base64url(HMAC-SHA256(PIXEL_SECRET, `"pixel-write:v1:" + roomId`))[:32] | server, stateless | **Write capability.** Never in a path or query. Only in the URL fragment `#key=…`, in `Authorization: Bearer`, and in `~/.pixel-office/config.json` (mode 600). |
| `ownerId` | `[A-Za-z0-9_-]{4,32}` | client (installer: `"o_" + token_urlsafe(9)`) | no (opaque, stable per machine) |
| `session` / `sub` | client-opaque `[A-Za-z0-9_-]{1,64}` | hook client sends `sha256(salt + session_id)[:20]` / `sha256(salt + agent_id)[:20]` | Raw Claude Code ids never leave the machine |
| agent `id` (public) | `"a" + sha256(roomId + "|" + ownerId + "|" + session + "|" + (sub or ""))[:16]` | server | no |

`PIXEL_SECRET` (env, ≥ 32 random bytes) makes write keys verifiable **without a storage read**. Rotating
it invalidates every write key (all friends must re-run setup). There is no per-friend revocation:
if a join link leaks, create a new room. This is a deliberate trade-off for a friends-only app and
is stated in the README.

### 3.2 Links

| Link | Form | Grants |
|---|---|---|
| Join (invite) link | `https://<host>/?room=<roomId>#key=<writeKey>` | view + "Ajan bağla" setup commands (contain the key) |
| View-only link | `https://<host>/?room=<roomId>` | view only (setup dialog explains a key is needed) |

The fragment is never sent to the server, CDN or Referer header. `cloud.js` reads `#key=`, stores it
in `localStorage["po.rooms"]` and then **removes the fragment** (`history.replaceState`) so screenshots
and copy-paste of the address bar don't leak it. The "Davet et" button rebuilds the join link from storage.

### 3.3 Room creation guard

- If env `PIXEL_CREATE_TOKEN` is set (recommended for a public URL), `POST /api/rooms` requires
  `{"createToken": "<value>"}` (constant-time compare). The landing page shows a password field when
  `GET /api/config` reports `"createRequiresToken": true`.
- Creating a room costs 2 Redis commands (`_meta` HSET + EXPIRE). Rooms are otherwise stateless.

---

## 4. Storage

### 4.1 Choice: Upstash Redis (justification)

Researched 2026-10-11 (sources in §18):

| Option | Free tier (current) | Fit |
|---|---|---|
| **Upstash Redis** (Vercel Marketplace) | 500K commands/month, 256 MB (one page says 250 MB), 10 GB bandwidth, 10K cmd/s, 1 free DB. Pipelines/transactions are billed per inner command. REST API = HTTPS + JSON, so stdlib `http.client` is enough. | ✅ One hash per room = 1 command per read and ~1.1 per write. Native TTLs. Provisioned from the Vercel dashboard, env vars injected automatically. No inactivity pause found in docs. |
| Supabase | 200 concurrent Realtime connections, 2M Realtime messages, 500 MB DB, 2 active projects, **pauses after 7 days of inactivity** (third-party mid-2026 sources and supabase.com/pricing) | ⚠️ Push would remove polling, but it needs a Phoenix-websocket client in vanilla JS (the official client is an npm/CDN dependency) plus Postgres schema/RLS. A friends' room that sits unused over a holiday would pause. More moving parts than the problem needs. |
| Vercel Edge Config / Blob | Edge Config is built for rarely-written config; Blob is object storage with per-minute operation caps | ❌ Wrong write pattern |
| SSE from Vercel functions | Each open stream is one function instance; 10 viewers × 8 h × provisioned memory would blow the 360 GB-h Hobby allotment | ❌ |

**Decision: Upstash Redis + adaptive polling + CDN caching.** The math is in §12.

### 4.2 Schema

One Redis hash per room. Everything about a room is read with a single `HGETALL`.

```
KEY   po:room:<roomId>                 TYPE hash        TTL ROOM_TTL = 30 days, refreshed by writes
FIELD _meta        → {"name":"Kod Kulübü","createdAt":1791720000000,"v":1}
FIELD <agentId>    → agent record (compact JSON, ≤ 600 bytes):
```

```json
{"v":1,"owner":"Timur","oid":"o_Xk3…","proj":"Pixel","st":"working","tool":"Bash","lbl":null,
 "sub":false,"par":null,"atype":null,"src":"claude-code","col":3,
 "ts":1791723961000,"ss":1791723950000,"sa":1791722114000,"tc":42}
```

| key | meaning |
|---|---|
| `ts` | server time (ms) of the last write; **client clocks are never trusted** |
| `ss` | stateSince = `ts - stateAgeMs` (client sends ages, not timestamps, so clock skew doesn't matter) |
| `sa` | startedAt = `ts - startedAgeMs` |
| `col` | preferred owner colour index 0–15 (client may send `color`, else `crc32(ownerId) % 16`) |
| `par` | parent agent id (subagents) |
| `tc` | tool count (client-maintained) |

Additional record keys: `nm` (reported name, §16, optional) and `fin` (finished flag, §16).
Rename fields in the same hash: `n:<agentId>` and `no:<ownerId>` → `{"name":"Dilek","ts":…}` (§15.3). Agent writes never touch them.

Agents with `st == "offline"` are deleted (`HDEL`) instead of written.

### 4.3 Commands per operation

| Operation | Redis commands |
|---|---|
| Create room | `HSET po:room:R _meta …` + `EXPIRE po:room:R 2592000` (pipeline) = **2** |
| Write one agent (`/api/status`) | `HSET po:room:R <aid> <json>` = **1**. Plus: `EXPIRE` when this warm instance hasn't refreshed this room's TTL in the last hour (≈ +0.02/write). Plus `HLEN` only when HSET returned 1 (new field) to enforce `MAX_AGENTS_PER_ROOM` (≈ +0.03/write). Batch of n agents = one pipeline of n HSETs. |
| Offline (`st: offline`) | `HDEL` = 1 |
| Read (`/api/state`) | `HGETALL` = **1**, only on CDN miss. Plus at most one `HDEL f1 f2 …` when the read finds records older than `PURGE_AFTER` (rare). |

### 4.4 `storage.py` interface

```python
class Storage(Protocol):
    name: str                                                    # "upstash" | "memory"
    def put_agents(self, room: str, items: dict[str, str], refresh_ttl: bool) -> dict[str, bool]  # field → created?
    def count(self, room: str) -> int                            # HLEN
    def delete(self, room: str, fields: list[str]) -> None       # HDEL
    def read_room(self, room: str) -> dict[str, str]             # HGETALL ({} when missing)
    def create_room(self, room: str, meta_json: str) -> None     # HSET _meta + EXPIRE
    def ping(self) -> bool

def get_storage() -> Storage:   # cached per process
    # URL: KV_REST_API_URL or UPSTASH_REDIS_REST_URL; TOKEN: KV_REST_API_TOKEN or UPSTASH_REDIS_REST_TOKEN
    # both present → UpstashStorage; else if os.environ.get("VERCEL") → UnconfiguredStorage (raises → 503)
    # else → MemoryStorage (dev; process-local, thread-safe, honours TTLs with an injectable clock)
```

`UpstashStorage`: one module-level `http.client.HTTPSConnection` per warm instance (keep-alive, reconnect
once on failure), `POST /` with a JSON array for single commands, `POST /pipeline` with a 2-D array for
batches, header `Authorization: Bearer <token>`, timeout 3 s. Response `{"result": …}` / `{"error": "…"}`
(per Upstash REST docs). Any error → `StorageError` → HTTP 503 `storage_unavailable`.

---

## 5. HTTP API

Common rules:
- All bodies are UTF-8 JSON. Responses: `Content-Type: application/json; charset=utf-8`.
- Errors: `{"error": "<code>", "message": "<short English text>"}`. Codes: `bad_request` 400,
  `unauthorized` 401, `forbidden` 403, `not_found` 404, `method_not_allowed` 405, `payload_too_large` 413,
  `rate_limited` 429 (+ `Retry-After` header and `"retryAfter"` seconds), `storage_unavailable` 503,
  `storage_not_configured` 503.
- Writes and errors: `Cache-Control: no-store`.
- CORS: every `/api/*` response has `Access-Control-Allow-Origin: *`. `OPTIONS` on `/api/status` returns
  204 with `Access-Control-Allow-Methods: POST, OPTIONS`, `Access-Control-Allow-Headers: Authorization, Content-Type`,
  `Access-Control-Max-Age: 86400`. Bearer tokens, not cookies, so CSRF does not apply.
- The server never logs request bodies, and never logs keys or the `Authorization` header.

### 5.1 `GET /api/config`

Used by the page to tell cloud from local (the v1 local server answers 404 here, §9.1).
```json
{"mode":"cloud","version":"2.0.0","createRequiresToken":true,"storage":"upstash",
 "poll":{"activeMs":10000,"calmMs":30000,"emptyMs":60000},
 "limits":{"maxAgentsPerRoom":48,"maxBatch":10}}
```
`Cache-Control: public, max-age=60, s-maxage=300`.

### 5.2 `GET /api/health`

`200 {"ok":true,"version":"2.0.0","storage":"upstash"|"memory"|"unconfigured","storageOk":true}`
(`storageOk` comes from one `PING`. Uncached. Used for manual checks only, never polled by the UI.)

### 5.3 `POST /api/rooms` — create room

Request (≤ 1 KB):
```json
{"name": "Kod Kulübü", "createToken": "optional"}
```
`name`: 1–32 chars after sanitising (§5.5). Default `"Pixel Ofis"`.

Response `201`:
```json
{"roomId":"k7q2m4x5z6a7b2c3d4e5f6g7h2","name":"Kod Kulübü","writeKey":"Qm9…32chars",
 "viewUrl":"https://<host>/?room=k7q2…","joinUrl":"https://<host>/?room=k7q2…#key=Qm9…",
 "createdAt":1791720000000}
```
`<host>` comes from the `Host` / `X-Forwarded-Host` header (`X-Forwarded-Proto` for the scheme), else `PIXEL_PUBLIC_URL` if set.
Errors: 403 `forbidden` (wrong/missing create token), 429 (5 creations / hour / IP, in-process).

### 5.4 `GET /api/state?room=<roomId>` — room snapshot (polled)

No auth. Invalid room id format → 404. Unknown-but-valid id → 200 with an empty room (`name` default).

Response `200` (v1 `/api/agents` shape plus room fields; Agent objects are **v1-schema compatible**,
ARCHITECTURE.md §3.3, so `agents.js`/`ui.js` work as-is):
```json
{
  "mode": "cloud",
  "serverTime": 1791723961000,
  "room": {"id": "k7q2…", "name": "Kod Kulübü"},
  "pollMs": 10000,
  "agents": [
    {
      "id": "a1b2c3d4e5f60718",
      "name": "Timur",
      "project": "Pixel",
      "projectPath": null, "title": null, "lastMessage": null, "lastPrompt": null, "model": null,
      "state": "working",
      "tool": "Bash",
      "detail": "Komut çalıştırıyor",
      "isSubagent": false, "parentId": null, "agentType": null,
      "colorSeed": 2840193311,
      "toolCount": 42,
      "startedAt": 1791722114000, "stateSince": 1791723950000, "updatedAt": 1791723961000,
      "owner": "Timur", "ownerId": "o_Xk3…", "ownerColor": "#e8564a", "source": "claude-code"
    }
  ]
}
```
- `name` = owner name (subagents: `agentType` or `"Alt-ajan"`; the renderer adds `↳ `).
- `detail` = the stored label, or a Turkish phrase derived from `tool` (§7.4 table) when the label is null.
- `colorSeed` = `crc32(agent id)` (face/hair variety). `ownerColor` = shirt colour (§6.4).
- `pollMs` = what the client should wait before the next poll (§10.2): `POLL_ACTIVE_MS` if any agent is
  `working|thinking|waiting_for_user`, `POLL_CALM_MS` if agents exist but all are idle/sleeping, `POLL_EMPTY_MS` if none.
- Sorted by `startedAt`, then `id`.

Caching headers (the reason polling is affordable, §12):

| room activity | `Cache-Control` |
|---|---|
| active | `public, max-age=0, s-maxage=5` |
| calm | `public, max-age=0, s-maxage=15` |
| empty | `public, max-age=0, s-maxage=5` (was 30 until Phase 10, §17.10) |

The Vercel CDN caches GET responses with `s-maxage` (no `Authorization` header, no `Set-Cookie`, no
`Vary: Cookie`), strips `s-maxage` before the browser sees it, and keys by URL including the query.
The client fetches with `cache: "no-store"`, so the browser never adds its own staleness.
An in-process micro-cache (per warm instance, 2 s) also absorbs concurrent CDN misses.

### 5.5 `POST /api/status?room=<roomId>` — generic status API (also used by the hook clients)

Headers: `Authorization: Bearer <writeKey>`, `Content-Type: application/json`. Body ≤ **4 KB** (checked
from `Content-Length` before reading; a missing length is read up to 4 KB + 1).

Body: either a single update object or `{"agents": [update, …]}` with 1–10 updates.

```json
{
  "session": "build-bot",           // required, [A-Za-z0-9_-]{1,64}; your stable id for this agent/run
  "sub": null,                      // optional subagent id, same charset; makes a child of `session`
  "owner": "Timur",                 // required, 1–24 chars
  "ownerId": "o_Xk3…",              // optional [A-Za-z0-9_-]{4,32}; default "n_" + sha256(lower(owner))[:10]
  "project": "Pixel",               // optional, ≤ 32 chars (basename only; '/' and '\' rejected)
  "state": "working",               // required: working|thinking|waiting_for_user|idle|sleeping|offline
  "tool": "Bash",                   // optional, [A-Za-z0-9_.:-]{1,32}
  "label": "Derleme yapıyor",       // optional free text, ≤ 40 chars
  "agentType": null,                // optional, [A-Za-z0-9 _-]{1,24} (subagents)
  "source": "cursor",               // optional [a-z0-9-]{1,16}; default "api"
  "color": 3,                       // optional 0–15 preferred owner colour
  "stateAgeMs": 0,                  // optional, 0..86_400_000; how long it has been in this state
  "startedAgeMs": 600000,           // optional, 0..604_800_000
  "toolCount": 12,                  // optional, 0..1_000_000
  "name": "Kâtip",                  // optional reported display name, 1–24 code points (priority 4 in §15.2; never overrides a rename)
  "final": false                    // optional; true only with state "sleeping" → removed 300 s later (§16)
}
```

Sanitising (`validate.py`, applies to every string): NFC normalise; strip C0/C1 control chars, bidi
overrides (U+202A–202E, U+2066–2069) and zero-width chars; collapse whitespace; trim; truncate to the
limit **by code points**. Unknown fields are ignored (never stored). Wrong types → 400.

Response `200`:
```json
{"ok": true, "ids": ["a1b2c3d4e5f60718"], "serverTime": 1791723961000}
```
Errors: 401 (missing/wrong key, constant-time compare), 404 (bad room id format), 413, 400, 429, 503.
A new agent beyond `MAX_AGENTS_PER_ROOM` (48) → the field is removed again and the response is 429 `room_full`.

`state: "offline"` deletes the agent. The character walks out through the door on the next poll.
This is the "remove agent" operation. There is no separate DELETE endpoint.

### 5.6 `POST /api/cc_hook?room=<roomId>` — raw Claude Code HTTP-hook fallback (opt-in)

For machines that can run neither Python nor PowerShell scripts. Claude Code's `type: "http"` hook POSTs
the **raw** hook JSON (§7.6 explains the privacy cost). Headers: `Authorization: Bearer <writeKey>`,
`X-Pixel-Owner: <url-encoded name>`, `X-Pixel-Owner-Id: <ownerId>`.
- Body cap 256 KB. Bigger bodies are drained and ignored with 204.
- The server runs the **same mapping function** as the clients (`map_event` imported from
  `web/hook/pixel_hook.py`, stateless variant), extracts only allow-listed fields, and discards the
  body immediately. Nothing from it is logged or stored.
- Responds `200 {}`, quickly. Claude Code treats non-2xx and timeouts as non-blocking errors anyway.

---

### 5.7 `POST /api/rename?room=<roomId>`

Renames an agent or sets an owner default name. Specified in §15.3.

---

## 6. State model in the cloud

### 6.1 States

Same five states as v1 (`working`, `thinking`, `waiting_for_user`, `idle`, `sleeping`) plus the write-only
pseudo-state `offline` (delete). Removal = omitted from `/api/state` → `CharacterManager.sync` makes the
character leave through the door (v1 behaviour).

### 6.2 Read-time transitions (`derive.py`, evaluated per record on every CDN-miss read)

`age = now - ts`, `sub = record.sub`. First matching rule wins:

| # | condition | result |
|---|---|---|
| 1 | `age > PURGE_AFTER` (6 h) | omit + schedule `HDEL` |
| 2 | `sub` and `st == "sleeping"` and `age > SUB_REMOVE_AFTER` (120 s) | omit |
| 2b | `fin` and `age > FINISHED_REMOVE_AFTER` (300 s) | omit (§16) |
| 3 | `sub` and `age > SUB_STALE_REMOVE` (1800 s) | omit |
| 4 | not `sub` and `age > REMOVE_AFTER` (2700 s = 45 min) | omit |
| 5 | `st == "idle"` and `age > IDLE_TO_SLEEP` (180 s) | `sleeping`, `stateSince = ts + 180 s` |
| 6 | `st in (working, thinking, waiting_for_user)` and `age > ACTIVE_STALE` (1800 s) | `sleeping`, `stateSince = ts + 1800 s`, tool cleared |
| 7 | otherwise | stored state |

Also: a subagent whose parent record is gone is shown as a top-level agent with `parentId: null` (`agents.js`
already tolerates missing parents).

Known consequence (documented in README): a single tool call running longer than 30 min with no other hook
events (e.g. a huge build) shows as sleeping until the next event. The client's 5-minute keep-alive (§7.3)
only helps when events keep firing.

### 6.3 Constants (`pixel_cloud/settings.py`)

```python
ROOM_TTL = 30 * 86400          # s, refreshed on writes (EXPIRE at most once per hour per warm instance)
IDLE_TO_SLEEP = 180            # s
ACTIVE_STALE = 1800            # s
REMOVE_AFTER = 2700            # s
SUB_REMOVE_AFTER = 120         # s
SUB_STALE_REMOVE = 1800        # s
PURGE_AFTER = 6 * 3600         # s
MAX_AGENTS_PER_ROOM = 48
MAX_NAME_FIELDS = 96           # n:/no: rename fields per room (§15.3)
FINISHED_REMOVE_AFTER = 300    # s (§16)
NAME_MAX = 24                  # code points
MAX_BATCH = 10
MAX_STATUS_BODY = 4096         # bytes
MAX_CREATE_BODY = 1024
MAX_CC_HOOK_BODY = 262_144
POLL_ACTIVE_MS, POLL_CALM_MS, POLL_EMPTY_MS = 10_000, 30_000, 60_000    # env-overridable: PIXEL_POLL_*_MS
CDN_TTL_ACTIVE, CDN_TTL_CALM, CDN_TTL_EMPTY = 5, 15, 5                  # s (empty: 30 → 5 in Phase 10, §17.10)
MICRO_CACHE_S = 2
```

### 6.4 Owner colours

16-colour shirt palette (`derive.OWNER_COLORS`, hex): `#e8564a #f29b38 #f2d03b #8bc34a #3fb67d #2bb3b1 #3a9be0 #5b6ee1
#9b5de5 #d65db1 #ff8fa3 #a1887f #78909c #ffffff #c0ca33 #26a69a`. For each room snapshot, the distinct
owners present are sorted by `ownerId`. Each gets its preferred index `col`, or the next free index if an
earlier owner took it. Deterministic for a given set of owners, so with ≤ 16 owners no two share a colour.
Subagents inherit their parent's shirt (v1 rule).

---

## 7. Agent integration

### 7.1 Claude Code hooks: facts used (code.claude.com/docs/en/hooks, read 2026-10-11)

- Configured in `~/.claude/settings.json` (user scope = all projects) under `"hooks"`:
  `{"<Event>": [{"matcher": "<tool regex or exact>", "hooks": [{"type": "command", "command": "...", "args": [...], "async": true, "timeout": N}]}]}`.
  Omitted, `""` or `"*"` matcher = all. `args` present = exec form (no shell). The command form runs PowerShell on
  Windows when Git Bash is absent.
- Common stdin fields: `session_id`, `prompt_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`,
  `agent_id` / `agent_type` (when the hook fires inside a subagent/teammate), `effort`.
- Event-specific fields used here: `SessionStart.source` (`startup|resume|clear|compact|fork`);
  `UserPromptSubmit.prompt` (**never read**); `PreToolUse.tool_name`, `tool_input` (**never read**), `tool_use_id`;
  `PermissionRequest.tool_name`; `PostToolUse.tool_name`; `PostToolUseFailure.is_interrupt`;
  `Notification.notification_type` (`permission_prompt`, `idle_prompt`, `elicitation_dialog`, `agent_needs_input`, …) and
  `message` (**never read**); `Stop` (`last_assistant_message` **never read**); `StopFailure.error` (enum, e.g. `rate_limit`);
  `SubagentStart/SubagentStop.agent_id, agent_type`; `SessionEnd.reason` (`clear`, `logout`, `prompt_input_exit`, `other`, …).
- Timeouts: default 600 s for command hooks (30 s for `UserPromptSubmit`). **`SessionEnd` defaults to 1.5 s**
  unless the hook sets its own `timeout`.
- `"async": true` (command hooks only) runs the hook in the background without blocking Claude, and the timeout is not
  enforced. In `-p` (headless) mode, still-running async hooks are killed at teardown.
- Non-zero exit codes other than 2 are non-blocking errors. Our client **always exits 0** and prints nothing.
- Windows: paths arrive with backslashes (basename must split on `/` and `\`).
- `type: "http"` hooks POST the event JSON to a URL. Non-2xx/timeouts are non-blocking. They are **not** async.

### 7.2 Hook event → office state (client-side, `map_event`)

`S` = the session's main record, `A` = subagent record (when `agent_id` is present, the event is applied to
`A` = (session, sub=hash(agent_id)) instead of `S`). "last" = last state sent for that record (local state file).

| Hook event (matcher) | Condition | Sends | tool / label |
|---|---|---|---|
| `SessionStart` | `source` ≠ `compact` | `idle` (character enters, goes for coffee) | —, — |
| `SessionStart` | `source == compact` | nothing | |
| `UserPromptSubmit` | | `thinking` | tool cleared |
| `PreToolUse` (`*`) | tool ∈ {`AskUserQuestion`} | `waiting_for_user` | tool, "Soru soruyor" |
| `PreToolUse` | tool ∈ {`ExitPlanMode`} | `waiting_for_user` | tool, "Plan onayı bekliyor" |
| `PreToolUse` | otherwise | `working` | tool (MCP → `"MCP"`), label null |
| `PermissionRequest` (`*`) | | `waiting_for_user` | tool, "İzin bekliyor" |
| `PostToolUse` (`*`) | last == `waiting_for_user` | `thinking` (permission granted / question answered) | tool cleared |
| `PostToolUse` | otherwise | **nothing** (saves ~50% of writes; next PreToolUse/Stop corrects state) | |
| `PostToolUseFailure` | `is_interrupt == true` | `idle` | "Durduruldu" |
| `PostToolUseFailure` | otherwise | same rule as PostToolUse | |
| `Notification` | `permission_prompt`, `elicitation_dialog`, `agent_needs_input` | `waiting_for_user` | "İzin bekliyor" / "Yanıt bekliyor" |
| `Notification` | `idle_prompt` | `idle` (usually a no-op via dedupe) | |
| `Notification` | other types | nothing | |
| `PreCompact` | | `thinking` | "Bağlam sıkıştırılıyor" |
| `Stop` | | `idle` | tool cleared |
| `StopFailure` | | `idle` | "API hatası: <error enum>" (enum allow-listed) |
| `SubagentStart` | | `A` → `working`, `agentType` | "Alt-ajan" |
| `SubagentStop` | | `A` → `sleeping` (server removes it after 120 s) | |
| `SessionEnd` | | `S` → `offline` (walks out). Its live subagents → `offline` | |

Main-session state while a subagent runs: last main event was `PreToolUse(Agent)` → `working`, tool `Agent`.
`permission_mode` is not used. In `auto`/`bypassPermissions` modes `PermissionRequest` simply never fires.

### 7.3 Write coalescing in the client (budget-critical)

Per record, `~/.pixel-office/state/<sha256(salt+session[+sub])[:16]>.json` holds
`{"seq", "last": {"st","tool","lbl","proj","t"}, "pending": update|null, "since", "started", "tc"}`.
Written atomically (temp file + `os.replace`). On Windows (no `fcntl`) the Python client locks the same file with `msvcrt.locking` (waits at most 2 s, then goes on without the lock; Phase 20 / Package 7, H31); the seq check keeps any remaining race harmless.

1. Map the event (§7.2). No update → maybe bump `tc` (PreToolUse) → exit 0.
2. Increment `seq`, store `pending`.
3. **Dedupe:** if `(st, tool, lbl, proj)` equals `last` and `now - last.t < KEEPALIVE (300 s)` → clear pending, exit.
4. **Classify:** *state change* (st differs, or anything involving `waiting_for_user`/`offline`) vs *tool-only change*.
5. Tool-only: if `now - last.t < TOOL_MIN_INTERVAL` (30 s main, 60 s subagent) → leave as pending, exit. The next event
   that does send carries the newest data anyway. Accepted loss: if no further event comes, the office shows the previous tool name.
6. State change: sleep `DEBOUNCE` (1.0 s). Reload. If `seq` changed (a newer event arrived) → exit, the newer process sends.
7. POST `/api/status` (timeout 4 s, one retry after 1 s on network error / 5xx, honour 429 `retryAfter` by dropping).
   On success write `last`. Errors go to `~/.pixel-office/hook.log` (≤ 64 KB, rotated, event names and HTTP status only).
8. `SessionEnd` is configured synchronous with `"timeout": 5` and skips the debounce (async hooks may be killed at exit).

Effect: a typical turn costs 2–4 writes (`thinking` → `working` → [tool refreshes ≤ 1/30 s] → `idle`) instead of
2 × tool calls.

### 7.4 Privacy allow-list (identical in `pixel_hook.py`, `pixel_hook.ps1` and the cc_hook mapper)

Leaves the machine: `state`, `tool` (built-in tool names verbatim; any `mcp__*` → `"MCP"` unless
`send_mcp_names: true`), `label` (only from the fixed Turkish strings in §7.2), `project` (basename of
`cwd`, or alias, or `"Gizli proje"`), `owner`, `ownerId`, hashed `session`/`sub`, `agentType` (only if in the built-in set
{`general-purpose`, `Explore`, `Plan`, `statusline-setup`, `claude-code-guide`, `claude`}, else `"Özel ajan"`), `color`, ages, `toolCount`, `source:"claude-code"`.

**Never read or sent**: `prompt`, `tool_input`, `tool_response`, `last_assistant_message`, notification `message`/`title`,
`transcript_path`, `agent_transcript_path`, full `cwd`, `session_title`, `scratchpad_dir`, raw `session_id`/`agent_id`,
`error_details`, `model`, `background_tasks`, `session_crons`. The clients parse stdin with `json` and **only index the allow-listed
keys**. There is no "copy the dict and delete fields" code path.

Local controls (`config.json`): `"project_aliases": {"/abs/path": "Takma ad"}`, `"hide_projects": false`,
`"send_mcp_names": false`, `"enabled": true`. Per project: a `.pixel-office-ignore` file in `cwd` or any parent
→ that session is never reported. Per run: env `PIXEL_OFFICE_DISABLE=1`.

Tool → Turkish detail phrase (server `derive.py` and UI, used when `label` is null):
Bash/PowerShell/BashOutput/Monitor "Komut çalıştırıyor" · Read "Dosya okuyor" · Edit/Write/MultiEdit/NotebookEdit "Dosya düzenliyor" ·
Grep/Glob "Kodda arıyor" · WebFetch/WebSearch "Web'de arıyor" · Agent/Task "Alt-ajan yönetiyor" · TodoWrite "Plan güncelliyor" ·
MCP "Araç kullanıyor (MCP)" · other "Çalışıyor".

### 7.5 Clients and one-line setup

**`web/hook/pixel_hook.py`** (Python ≥ 3.8, stdlib, single file). Subcommands:

| command | purpose |
|---|---|
| `hook` | read stdin hook JSON → §7.2/§7.3 → maybe POST. Always exit 0, no stdout. |
| `install --server URL --room R --key K --name N [--color i] [--force-powershell]` | write `~/.pixel-office/config.json` (0600), copy itself to `~/.pixel-office/pixel_hook.py`, merge hooks into `~/.claude/settings.json` (§7.5.1), then run `test` |
| `uninstall` | remove our hook entries (identified by the `pixel_hook` marker in command/args), keep the backup, leave other hooks untouched |
| `test` | POST `idle` as session `setup-test` with label "Kurulum testi", then `offline` 20 s later (detached) |
| `status --session S --state X [--tool T] [--label L] [--project P]` | generic CLI for scripts/bots (uses config.json) |
| `doctor` | print config (key masked), settings.json hook presence, last 20 log lines, a reachability check of `/api/health` |

Exposes a pure function `map_event(event: dict, cfg: dict, last_state: str | None) -> list[Update]` that the
server's cc_hook endpoint imports.

**`web/hook/pixel_hook.ps1`** (Windows PowerShell 5.1+, also runs on `pwsh` 7 on macOS/Linux). Same subcommands,
same mapping table, same state-file format, same allow-list. Uses `ConvertFrom-Json`,
`[System.Security.Cryptography.SHA256]`, `Invoke-WebRequest -UseBasicParsing`, and paths under
`$env:USERPROFILE\.pixel-office`. Splits paths on `/` and `\`.

**Setup commands** (rendered by the "Ajan bağla" dialog with host/room/key/name filled in; the name comes from
`localStorage["po.name"]`, editable in the dialog):

macOS / Linux (needs `python3` ≥ 3.8):
```sh
curl -fsSL https://<host>/hook/install.sh | sh -s -- --server https://<host> --room <roomId> --key <writeKey> --name "Timur"
```
`install.sh` (POSIX sh): find `python3` (≥ 3.8), else `pwsh` (→ ps1 client), else print a clear error with the manual
JSON. Download `pixel_hook.py` to `~/.pixel-office/`, run `install`.

Windows (no Python needed):
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((irm https://<host>/hook/install.ps1))) -Server https://<host> -Room <roomId> -Key <writeKey> -Name 'Timur'"
```
`install.ps1`: if `py -3` or `python` ≥ 3.8 exists (and not `-ForcePowerShell`) → Python client, else → PowerShell client.
Writes config, merges settings.json (backup `settings.json.pixel-backup`), runs `test`.

The dialog also shows: "Komutu çalıştırmadan önce betiği okuyabilirsiniz: https://<host>/hook/install.sh" and
"Açık Claude Code oturumlarını yeniden başlatın."

#### 7.5.1 settings.json entries written by the installer

Python client (absolute interpreter path = `sys.executable`; exec form avoids shell quoting on all OSes):
```json
{
  "hooks": {
    "SessionStart":       [{"hooks": [{"type": "command", "command": "/usr/bin/python3", "args": ["/home/u/.pixel-office/pixel_hook.py", "hook"], "async": true}]}],
    "UserPromptSubmit":   [{"hooks": [{"type": "command", "command": "/usr/bin/python3", "args": ["/home/u/.pixel-office/pixel_hook.py", "hook"], "async": true}]}],
    "PreToolUse":         [{"matcher": "*", "hooks": [{"type": "command", "command": "/usr/bin/python3", "args": ["/home/u/.pixel-office/pixel_hook.py", "hook"], "async": true}]}],
    "PermissionRequest":  [{"matcher": "*", "hooks": [ …same, "async": true… ]}],
    "PostToolUse":        [{"matcher": "*", "hooks": [ …same, "async": true… ]}],
    "PostToolUseFailure": [{"matcher": "*", "hooks": [ …same, "async": true… ]}],
    "Notification":       [{"hooks": [ …same, "async": true… ]}],
    "PreCompact":         [{"hooks": [ …same, "async": true… ]}],
    "Stop":               [{"hooks": [ …same, "async": true… ]}],
    "StopFailure":        [{"hooks": [ …same, "async": true… ]}],
    "SubagentStart":      [{"hooks": [ …same, "async": true… ]}],
    "SubagentStop":       [{"hooks": [ …same, "async": true… ]}],
    "SessionEnd":         [{"hooks": [{"type": "command", "command": "/usr/bin/python3", "args": ["/home/u/.pixel-office/pixel_hook.py", "hook"], "timeout": 5}]}]
  }
}
```
PowerShell client: `"command": "powershell.exe", "args": ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", "C:\\Users\\u\\.pixel-office\\pixel_hook.ps1", "hook"]`.

Merge rules: parse the existing file (if it is invalid JSON → abort without writing, tell the user). Back it up once.
Remove our previous entries (marker `pixel_hook` in `command`/`args`), then **append** our matcher groups. Never
reorder or modify foreign entries. Write atomically, keep 2-space indentation, keep `ensure_ascii=False`.
Re-running install is idempotent.

### 7.6 Python-free paths and their honest trade-offs

| Path | Who | Privacy | Cost |
|---|---|---|---|
| `pixel_hook.py` | macOS/Linux (python3 almost always present), Windows with Python | Full guarantee (filtered locally) | ~40 ms CPU per hook event, async |
| **`pixel_hook.ps1`** (default on Windows without Python) | every Windows 10/11 (PowerShell 5.1 is built in) | **Full guarantee** (filtered locally) | ~0.3–0.6 s PowerShell start-up per event, async so Claude is not slowed. Needs `-ExecutionPolicy Bypass` on the command line (process scope only, no system change). **May be blocked** on managed/corporate PCs (AppLocker, Constrained Language Mode) |
| `type: "http"` hook → `/api/cc_hook` (opt-in, dialog section "Gelişmiş: kurulum yapılamıyorsa") | anyone who can only edit settings.json | ⚠️ **Breaks the promise in transit.** Claude Code sends the full hook JSON (prompts, code in `tool_input`, Claude's last message) over TLS to the server. The server keeps only the allow-listed fields and stores/logs nothing else, but the data does leave the machine. The dialog says this in red, and the user must tick a checkbox before the snippet is shown. | HTTP hooks are synchronous: each tool call waits for the round trip (≈ 50–300 ms; `"timeout": 3`). Registered only for `SessionStart, UserPromptSubmit, PreToolUse, PermissionRequest, Notification, Stop, SubagentStart, SubagentStop, SessionEnd` |

Recommended wording for README (TR): "Windows'ta Python gerekmez. Kurulum, Windows'la birlikte gelen PowerShell'i kullanır ve
aynı gizlilik garantisini verir. Şirket bilgisayarlarında PowerShell betikleri engellenmiş olabilir. Bu durumda tek seçenek
'HTTP kancası'dır. Bu yöntem istem ve kod içeriklerini sunucuya *gönderir*. Sunucu bunları saklamaz, ama bilgisayardan çıkarlar."

### 7.7 Generic API examples (shown in the "Ajan bağla → Genel API" tab and README)

```sh
# curl
curl -X POST "https://<host>/api/status?room=<roomId>" \
  -H "Authorization: Bearer <writeKey>" -H "Content-Type: application/json" \
  -d '{"session":"nightly-build","owner":"Timur","project":"api","state":"working","tool":"Build","label":"Derleniyor","source":"ci"}'
# done:
curl -X POST "https://<host>/api/status?room=<roomId>" -H "Authorization: Bearer <writeKey>" \
  -H "Content-Type: application/json" -d '{"session":"nightly-build","owner":"Timur","state":"offline"}'
```
```python
# Python (stdlib)
import json, urllib.request
req = urllib.request.Request("https://<host>/api/status?room=<roomId>", method="POST",
    data=json.dumps({"session": "my-bot", "owner": "Ayşe", "project": "bot", "state": "thinking", "source": "custom"}).encode(),
    headers={"Authorization": "Bearer <writeKey>", "Content-Type": "application/json"})
urllib.request.urlopen(req, timeout=5)
```
```powershell
Invoke-RestMethod -Method Post -Uri "https://<host>/api/status?room=<roomId>" -Headers @{Authorization="Bearer <writeKey>"} `
  -ContentType "application/json" -Body '{"session":"cursor-1","owner":"Mert","project":"web","state":"working","source":"cursor"}'
```
```js
// Browser / Node 18+ / Deno
await fetch(`https://<host>/api/status?room=${roomId}`, {method: 'POST',
  headers: {Authorization: `Bearer ${writeKey}`, 'Content-Type': 'application/json'},
  body: JSON.stringify({session: 'agent-7', owner: 'Ece', state: 'idle', source: 'custom'})});
```
Also: `python3 ~/.pixel-office/pixel_hook.py status --session cursor --state working --tool Edit --project web`
(reuses the installed config, the easy path for Cursor tasks/scripts). Guidance for API users: send on state
change only, at most once per 10 s per session. Send `offline` when done, otherwise the record ages out (§6.2).

---

## 8. Abuse limits

| Limit | Value | Where |
|---|---|---|
| Body size | status 4 KB, rooms 1 KB, cc_hook 256 KB | `app.py`, from `Content-Length`, before reading |
| Batch | ≤ 10 updates | `validate.py` |
| Agents per room | 48 | `HLEN` on new fields |
| Field lengths | owner 24, project 32, label 40, tool 32, agentType 24, room name 32 (code points) | `validate.py` |
| Write rate | per room: token bucket 2/s, burst 30; per IP: 4/s, burst 60 | `ratelimit.py`, **in-process per warm instance** (best effort, no Redis cost) |
| Room creation | per IP 5/hour (in-process) + optional `PIXEL_CREATE_TOKEN` | `ratelimit.py` |
| Read | not rate-limited by us (CDN absorbs it). Invalid room ids → 404 without touching storage | |

In-process limits are not global (several warm instances could each allow the burst). That is acceptable
for a friends app. The real protection is that the write key is required and room creation can be locked.
Client IP = first entry of `X-Forwarded-For` (Vercel sets it), else the socket address.

Security headers (`vercel.json` + `dev_server.py`): `X-Content-Type-Options: nosniff`,
`Referrer-Policy: no-referrer`, `Content-Security-Policy: default-src 'self'; img-src 'self' data:;
style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'`.
All untrusted strings reach the DOM only through `ui.js` `esc()` and reach the canvas via `fillText`.

---

## 9. Frontend

### 9.1 Backend detection (`cloud.js`, called first by `main.js`)

```js
export async function detectBackend() // → {kind: 'local'} | {kind: 'cloud', config}
// fetch('/api/config', {cache: 'no-store'}): 200 + {mode:'cloud'} → cloud; anything else (v1 server: 404) → local.
```
- `local` → `SseTransport('/events')`: v1 behaviour, byte-for-byte the same UX. No service worker, no room UI, no PiP button
  changes required (PiP may be offered in local mode too, it only moves the canvas).
- `cloud` and no `?room=` → landing overlay. With `?room=<id>` → `PollingTransport`.

### 9.2 Transport interface (`transport.js`)

```js
/** handlers: {onSnapshot(data), onUpsert(agent), onRemove(id), onStatus(status)} where
 *  data = {mode, serverTime, agents, room?}; status ∈ 'connecting'|'live'|'reconnecting'. */
export class SseTransport {
  constructor(url = '/events');
  start(handlers); stop();
}
export class PollingTransport {
  constructor({roomId, base = '', scheduler = window});   // scheduler: object with setTimeout/clearTimeout (PiP window when open)
  start(handlers); stop();
  setScheduler(win);          // switch timer host when the PiP window opens/closes
  setActive(bool);            // false = pause polling (page hidden and no PiP); true = poll immediately
  pollNow();
}
```
PollingTransport behaviour:
- `GET /api/state?room=<id>` with `cache: 'no-store'`. Every success → `onSnapshot(data)` (main.js already calls
  `manager.sync(agents, {initial: first})`, which diffs and makes characters enter/leave), then `onStatus('live')`.
- Next poll after `data.pollMs` (clamped 3 s – 120 s) ± 10% jitter.
- Errors: `onStatus('reconnecting')`, back off 2 → 4 → 8 → 16 → 30 s. 404 → show "Oda bulunamadı" and stop.
- `setActive(false)` when `document.visibilityState === 'hidden'` **and** no PiP window is open, after a 30 s grace period.
  Back to visible → immediate poll.
- Uses the server's `serverTime` for `ui.setServerTime` (relative times).

`main.js` changes: `connect()` becomes `transport.start({...})` with the same handler bodies as today. The loop
uses `rafHost.requestAnimationFrame` where `rafHost = miniWindow.window || window`. When the opener is
minimised but the PiP window is visible, the opener's rAF and timers are throttled, so the PiP window's must
be used.

### 9.3 Room UI (Turkish)

- **Landing** (cloud, no room): "Yeni oda oluştur" (Oda adı, Adınız, [Oluşturma parolası]) → `POST /api/rooms` →
  navigate to `?room=…` with the key stored. "Bağlantıyla katıl" (paste a link). "Son odalar" list from `localStorage["po.rooms"]`
  (`[{id, name, key?, lastOpened}]`, max 10). If `lastRoom` exists and the app was launched as a PWA
  (`?source=pwa`), open it directly.
- **Top bar in a room**: room name, badge "BULUT ODA", connection status, buttons **Davet et** (copy join link;
  second button "Sadece izleme bağlantısı"), **Ajan bağla** (setup dialog, tabs: macOS/Linux · Windows · Genel API ·
  Gelişmiş), **Mini pencere** (§11), **Uygulamayı yükle** (shown only after `beforeinstallprompt`).
- **List/details**: rows show an owner colour chip, `owner · project`, state badge, tool. Grouped by owner, with
  subagents indented under their parent (existing grouping). Details: owner, project, state + since, tool + detail,
  source, agent type, tool count. Null fields hidden.
- **Labels on canvas**: `owner·project` (max 18 chars, ellipsis). Subagents `↳ agentType`.
- Without a key (view-only), "Ajan bağla" explains that the invite link is needed.

### 9.4 Changes to shared modules (additive, local mode unaffected)

- `agents.js`: if `agent.ownerColor` is set, override `shirt` (`shirtShade = darken`). Applied in the constructor and on
  `setAgent` when it changes. Takes priority over `setParentPalette`.
- `renderer.js`: label text = `agent.owner ? owner·project : name` (unchanged for local agents, which have no `owner`).
- `ui.js`: `setMode('cloud')` → "BULUT ODA". Empty-office hint in cloud mode: "Henüz ajan yok. 'Ajan bağla' ile ekleyin."
- The office has 9 desks / 3 coffee spots / 4 beds. Overflow spots (v1) absorb the rest. Rooms are capped at 48 agents.

---

## 10. PWA

### 10.1 `manifest.webmanifest`
```json
{
  "name": "Pixel Office — Bulut Ofis", "short_name": "Pixel Ofis", "lang": "tr",
  "start_url": "/?source=pwa", "scope": "/", "display": "standalone",
  "background_color": "#1e1e2a", "theme_color": "#1e1e2a",
  "launch_handler": {"client_mode": "focus-existing"},
  "icons": [
    {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
    {"src": "/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}
  ]
}
```
`index.html` adds `<link rel="manifest">`, `<meta name="theme-color">` and `<link rel="apple-touch-icon">`. Icons come from
`tools/make_icons.py` (stdlib zlib + struct PNG writer, the same 8×8 pixel art as the current favicon, nearest-neighbour
upscaled; maskable variant with a 20% safe-zone border). The generated PNGs are committed.

### 10.2 `sw.js` (registered only in cloud mode)

- `const VERSION = 'po-v2.0.0'`. Bump it on every release (Phase 10 checklist), together with `APP_VERSION` in
  `js/cloud.js` (Phase 20: `tests/test_release.py` checks that they are equal and that SHELL covers every module
  reachable from the cached pages).
- install: precache the shell (`/`, `index.html`, `css/style.css`, all `js/*.js`, manifest, icons). `skipWaiting()`.
- activate: delete other caches, `clients.claim()`, then post `{type: 'po-sw-version', version}` to open pages; a page
  whose `APP_VERSION` differs (an older tab) shows "Yeni sürüm hazır" with a **Yenile** button.
- fetch: `/api/*`, `/hook/*`, `/connector/*` → not intercepted (network). Navigations → network-first, fall back to the
  cached `/index.html` (ignoring the query). Code and pages (`*.js`, `*.css`, `*.html`, manifest) → network-first with
  the cache as offline fallback (Phase 20, v2.md H16: stale-while-revalidate could mix modules of two releases on the
  first load after a deploy). Icons → stale-while-revalidate.
- Vercel header for `/sw.js`: `Cache-Control: no-cache`, so updates are picked up.

---

## 11. Mini window (Document Picture-in-Picture) and fallbacks (`pip.js`)

```js
export class MiniWindow {
  constructor({frameEl, onOpen(win), onClose()});
  static supported();          // 'documentPictureInPicture' in window
  async open();                // must be called from a click handler (user activation required)
  close();
  get window();                // PiP Window or null
}
```
Document PiP path (Chrome/Edge 116+, Firefox 151+, desktop only; not Safari, not mobile):
1. `const w = await documentPictureInPicture.requestWindow({width: 400, height: 270})`. The site **cannot** position
   it. The user drags it to the bottom-right once. Chrome generally reopens it where it was last placed, but that is not guaranteed.
2. Copy styles: append `<link rel="stylesheet" href="/css/style.css">` (absolute URL) and set `w.document.body.className = 'mini'`.
3. Move the existing `.frame` element (canvas + banner) into `w.document.body`, plus a one-line status strip
   ("3 çalışıyor · 1 bekliyor · 2 kahvede"). Event listeners on the canvas keep working, since it is the same element.
4. `main.js`: `rafHost = w`, `transport.setScheduler(w)`, renderer fit via `new w.ResizeObserver(...)` on `w.document.body`.
5. `w.addEventListener('pagehide', …)` → move the frame back, restore rafHost/scheduler, refit.
   The PiP window never outlives its opener. Closing the main tab or PWA window closes it.
6. The PiP window is always on top. The main PWA window can be minimised. Polling continues because the PiP window is visible (§9.2).

Fallbacks:
- No Document PiP (Safari, older browsers): "Mini pencere" opens `window.open('/?room=<id>&view=mini', 'po-mini',
  'popup,width=400,height=270,left=<availLeft+availWidth-416>,top=<availTop+availHeight-300>')`. This **is** positioned
  bottom-right, but it is **not** always-on-top. The tooltip says so. `view=mini` hides the top bar and panel, and the canvas fills the window.
- Mobile / no popups: the button is hidden. `?view=mini` still works as a compact layout.

---

## 12. Budget (free tiers, researched 2026-10-11)

Limits used: **Vercel Hobby**: 1M function invocations, **1M CDN requests**, 4 h Active CPU, 360 GB-h provisioned
memory, 100 GB Fast Data Transfer, 10 GB Fast Origin Transfer per month. Exceeding them can **pause the account** (there is no
paid overage on Hobby). **Upstash free**: 500K commands/month, 10 GB bandwidth, 256 MB.

### 12.1 Assumptions (the user's target load)

- 10 friends, one shared room, widget (tab or PiP) visible 8 h/day each. Their 8 h windows overlap within a 10 h daily span.
- Room activity while watched: 50% *active* (someone working/thinking/waiting), 40% *calm*, 10% *empty*.
- Agent work: each friend actively drives Claude Code 4 h/day with 1.5 concurrent sessions on average →
  **60 session-hours/day**. With §7.3 coalescing, ≈ **100 writes per session-hour** (≈ 10 turns × 4 state sends +
  ≤ 1 tool refresh per 30 s while working ≈ 50 + subagent events ≈ 10).
- Stress month = 30 days. Realistic month = 22 working days.

### 12.2 Per-day numbers

| Item | Formula | /day |
|---|---|---|
| Viewer polls (CDN requests) | 10 viewers × 28 800 s × (0.5/10 + 0.4/30 + 0.1/60) | **18 720** |
| CDN-miss reads (function invocations = Redis HGETALL) | 36 000 s × (0.5/6 + 0.4/16 + 0.1/6) (≈ one per TTL+1 s while polls arrive; empty rooms: every 60 s poll of 10 viewers ≈ 1/6 s, so the 5 s TTL caps nothing) | **≈ 4 500** |
| Hook/API writes (CDN requests + invocations) | 60 session-h × 100 | **6 000** |
| Redis commands for writes | 6 000 × 1.1 (EXPIRE ≤ 1/h, HKEYS on new agents, rare purge) | **6 600** |
| Static/PWA requests | SW caches the shell; ~2 cold loads × 20 files × 10 people + SW update checks | **≈ 500** |
| Boost polls (§17.10) | pessimistic 30 boost windows/day (room opens while empty + "Ajan bağla" uses) × 36 polls (3 min at 5 s), each counted as a CDN request **and** a miss | **≈ 1 000** |

### 12.3 Monthly totals vs free tier

| Resource (limit) | Stress (30 d) | % | Realistic (22 d) | % |
|---|---|---|---|---|
| Vercel CDN requests (1M) | 18 720+6 000+500+1 000 = 26 220/d → **787K** | **79%** | 577K | 58% |
| Vercel function invocations (1M) | 4 500+6 000+1 000 → **345K** | 35% | 253K | 25% |
| Vercel Active CPU (4 h) | 345K × ~15 ms (warm ≈ 5–10 ms, cold starts amortised; I/O wait is not CPU) → **1.45 h** | 36% | 1.05 h | 26% |
| Vercel provisioned memory (360 GB-h) | 345K × ~0.12 s × 2 GB → **23 GB-h** | 6% | 17 GB-h | 5% |
| Vercel Fast Origin Transfer (10 GB) | 345K × ~4 KB → **1.4 GB** | 14% | 1.0 GB | 10% |
| Vercel Fast Data Transfer (100 GB) | 562K × ~4 KB + static → **≈ 2.5 GB** | 3% | 1.8 GB | 2% |
| Upstash commands (500K) | 4 500+6 600+1 000 = 12 100/d → **363K** | **73%** | 266K | 53% |
| Upstash bandwidth (10 GB) | 120K × 6 KB + 180K × 0.5 KB → **0.8 GB** | 8% | 0.6 GB | 6% |
| Upstash storage (256 MB) | ≤ 48 × 600 B per room | ≈ 0% | | |

**The binding constraint is Vercel CDN requests from viewer polling.** For comparison, a naive design (2 s polling,
no CDN cache, one write per hook event including PostToolUse) would need ≈ 4.3M polls and ≈ 1.2M Redis reads per month.

### 12.4 Levers if usage grows (no redeploy of clients needed)

1. `PIXEL_POLL_ACTIVE_MS` / `PIXEL_POLL_CALM_MS` env vars: clients obey `pollMs` from the server. Raising active from 10 s to
   15 s cuts viewer polls by ~28%.
2. CDN TTLs (settings) raise the cache hit rate. This affects Upstash/invocations, not CDN requests.
3. Client `TOOL_MIN_INTERVAL` 30 → 60 s (needs friends to re-run setup, so prefer levers 1–2 first).
4. Hidden-tab pause is already on. Encourage the PiP window over a full tab: same cost, more useful.
5. Watch the Vercel Usage tab and the Upstash console weekly in the first month (Phase 10 checklist).

Upstash's docs don't say what happens on the free plan when the 500K is exceeded. If Redis errors out, writes and reads return
503 and the clients back off. The local mode is unaffected.

---

## 13. Local development, tests, deployment

### 13.1 Local dev

```sh
python3 dev_server.py                 # http://127.0.0.1:8787 — web/ + /api/* (MemoryStorage, random PIXEL_SECRET per run)
python3 dev_server.py --demo          # also starts pixel_cloud.demo: creates room "Demo Oda", 3 fake owners post via dispatch
python3 dev_server.py --port 8787 --host 127.0.0.1
UPSTASH_REDIS_REST_URL=… UPSTASH_REDIS_REST_TOKEN=… PIXEL_SECRET=… python3 dev_server.py   # against real Upstash
python3 server.py                     # v1 local mode, unchanged (port 8765)
```
`dev_server.py`: `ThreadingHTTPServer`. `/api/*` → `pixel_cloud.app.dispatch(Request(method, path, headers, body, client_ip))`.
Everything else → static files from `web/` (same path-traversal guard as v1 `httpd.py`; MIME adds `.webmanifest`
`application/manifest+json`, `.ps1`/`.sh`/`.py` under `/hook/` as `text/plain; charset=utf-8`). It prints the demo room's join link.
It applies the same security headers as `vercel.json`. It does **not** emulate the CDN cache. The `X-Pixel-Cache-Hint` debug header
shows the TTL that Vercel would apply.

Hook client against the dev server: `python3 web/hook/pixel_hook.py install --server http://127.0.0.1:8787 --room … --key … --name Test`
(with `PIXEL_OFFICE_HOME=/tmp/…` and `PIXEL_OFFICE_CLAUDE_SETTINGS=/tmp/…/settings.json` env overrides, so tests never touch the real
`~/.claude`).

### 13.2 `vercel.json` and `.vercelignore`

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "framework": null,
  "outputDirectory": "web",
  "regions": ["fra1"],
  "functions": {
    "api/*.py": {"maxDuration": 10, "includeFiles": "{pixel_cloud/**,web/hook/pixel_hook.py}"}
  },
  "headers": [
    {"source": "/(.*)", "headers": [
      {"key": "X-Content-Type-Options", "value": "nosniff"},
      {"key": "Referrer-Policy", "value": "no-referrer"},
      {"key": "Content-Security-Policy", "value": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"}
    ]},
    {"source": "/sw.js", "headers": [{"key": "Cache-Control", "value": "no-cache"}]},
    {"source": "/manifest.webmanifest", "headers": [{"key": "Content-Type", "value": "application/manifest+json"}]},
    {"source": "/hook/(.*)", "headers": [
      {"key": "Content-Type", "value": "text/plain; charset=utf-8"},
      {"key": "Cache-Control", "value": "no-cache"}
    ]}
  ]
}
```
`.vercelignore`:
```
/server.py
/pixel_office/
/tests/
/docs/
/tools/
/names.json
/dev_server.py
/PROJECT.md
**/__pycache__/
```
Why: the root `server.py` is also one of the entry-point file names Vercel's Python framework detection looks for
(`app.py, index.py, server.py, main.py, …`), and `names.json` holds local session names. Neither belongs in the deployment.
`outputDirectory: "web"` makes only `web/` publicly static. **Verified in Phase 10 against Vercel's source (§17.10):** with `framework: null` and no build command, zero-config
detection adds `@vercel/static` for `web/**/*` (served at `/`) and, independently, one `@vercel/python` function per
`api/*.py` *that defines a top-level `class handler`* (or `app`/`application`). An assignment like `handler = make_handler()`
is **not** detected, so every `api/*.py` spells out `class handler(ApiHandler)`. Verification after the first deploy:
`/api/health` = 200, `/README.md` and `/server.py` = 404, `/hook/pixel_hook.py` = 200 text/plain. **Fallback if
`outputDirectory` turns out to disable `api/` detection:** drop it and add
`"rewrites": [{"source": "/((?!api/).*)", "destination": "/web/$1"}]`, keeping `.vercelignore` as the guard.

### 13.3 Test plan

Unit (`python3 -m unittest discover -s tests`, all stdlib, no network):
- `test_cloud_ids`: room id format, HMAC key verify/compare, agent id determinism, a key from another secret is rejected.
- `test_cloud_validate`: every field limit, control/bidi stripping, path separators in `project` rejected, batch > 10, unknown fields dropped.
- `test_cloud_derive`: §6.2 table with a fake clock (each row, plus boundaries), owner colour assignment and collisions, pollMs/TTL selection, v1 Agent schema keys present.
- `test_cloud_storage`: MemoryStorage TTL/HLEN/HDEL. UpstashStorage against a **fake Upstash REST server** (stdlib `http.server`
  implementing `POST /` and `/pipeline` for HSET/HGETALL/HDEL/HLEN/EXPIRE/PING), plus error → `StorageError`, plus reconnect.
- `test_cloud_app`: full dispatch matrix (all endpoints, all error codes, CORS preflight, body caps from Content-Length,
  rate limits with an injected clock, `Cache-Control` per activity, create token on/off, unconfigured storage on `VERCEL=1` → 503).
- `test_hook_client`: `hook_vectors.json` (≥ 40 vectors covering every row of §7.2, subagents, MCP, Windows paths, aliases,
  ignore file); debounce/seq supersede with a fake sleep; tool throttle and keep-alive; **privacy fuzz**: inject a canary
  string (`"CANARY-7f3a"`) into every non-allow-listed field of every event type, including nested `tool_input`, `cwd`
  parents and `transcript_path`, capture outbound bodies, assert the canary never appears; installer merge (foreign hooks preserved,
  idempotent, invalid JSON aborts, uninstall restores), all with temp `PIXEL_OFFICE_HOME`.
- The v1 suite must still pass unchanged (58 tests).

Integration / E2E:
- `dev_server.py --demo` + headless Chrome via the existing stdlib CDP script approach: landing → create room → join link
  → characters enter → state changes within one poll → `offline` makes the character walk out → view-only link has no key
  → poll pauses when hidden (count requests) → no console errors.
- Real Claude Code on the dev machine: install the hook against the dev server, run a session, check working/waiting/idle/sleep/exit.
  Assert with a request-logging dev flag (`--log-bodies`, dev only) that no prompt text is ever received.
- PowerShell client: run `hook_vectors.json` through `pwsh` if available on the dev machine, otherwise on a friend's Windows PC
  (manual checklist in Phase 8). Document whichever was done.
- PiP: manual on Chrome/Edge (open, drag, minimise the main window, polling continues, close restores). Fallback popup on Firefox ESR/Safari.
- Deployed: §13.2 verification, a Lighthouse PWA installability check in Chrome DevTools, `x-vercel-cache: HIT` seen on repeated `/api/state`.

### 13.4 Deployment (what the user does by hand)

1. **Git + GitHub**: install git (`sudo apt install git`) or use GitHub Desktop / the github.com "Upload files" page.
   Create a repo under your **personal** account (Hobby cannot import repos owned by GitHub organisations) and push.
2. **Vercel**: vercel.com → Add New… → Project → Import the GitHub repo. Framework preset: **Other**. Root directory `./`.
   Build/Install command: leave empty. Output directory: `web` (also set in vercel.json). Deploy.
3. **Storage**: Project → Storage (Marketplace) → **Upstash → Redis** → Free plan, primary region **Frankfurt (eu-central-1)**
   (next to `fra1`) → Connect to the project (all environments). Vercel injects `KV_REST_API_URL` / `KV_REST_API_TOKEN` and/or
   `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN`. The code accepts either pair.
4. **Env vars** (Project → Settings → Environment Variables, Production):
   `PIXEL_SECRET` = output of `python3 -c "import secrets;print(secrets.token_urlsafe(32))"` (43 characters, ≥ 32 required) (**never change it later**, or all
   setups break); `PIXEL_CREATE_TOKEN` = a password for creating rooms (recommended).
5. Redeploy (Deployments → … → Redeploy). Open `https://<project>.vercel.app/api/health` → `"storage":"upstash","storageOk":true`.
6. Open the site → create a room → "Davet et" → send the link to friends. Each friend opens it → "Ajan bağla" → runs one command
   → restarts Claude Code.
7. Use the **production** domain in all links. Preview deployments on Hobby are protected by Vercel Authentication by default.

---

## 14. Phase ownership (see PROJECT.md for checklists)

Phase 7 (backend): `api/` (incl. `rename.py`), `pixel_cloud/`, `dev_server.py`, `vercel.json`, `.vercelignore`, `.python-version`, `tests/test_cloud_*`;
local server additions: `pixel_office/names_store.py`, `reported.py`, edits to `httpd.py`, `models.py`, `server.py` (§15.4, §16).
Phase 8 (agent integration): `web/hook/*` incl. `pixel_agent.py` (library, `pixel-run`, `pixel-proxy`, §17), `tests/test_hook_client.py`, `tests/hook_vectors.json`.
Phase 9 (cloud UI & PWA): `web/js/transport.js|cloud.js|pip.js`, edits to `main.js|agents.js|renderer.js|ui.js|index.html|style.css`,
`manifest.webmanifest`, `sw.js`, `icons/`, `tools/make_icons.py`.
Phase 9 also owns the rename UI (§15.1).
Phase 10 (QA & release): `tests/test_rename.py`, `test_local_status.py`, `test_pixel_agent.py`, E2E, README deployment guide (TR + EN summary), `.gitignore`, `LICENSE`, budget check after the first week.

---

## 15. Renaming agents from the UI (local and cloud)

### 15.1 UX (Phase 9, both modes)

- Select a character (canvas click or list row). The details panel title shows the name with a ✎ button. Clicking the button turns the
  title into an `<input maxlength=24>`. Enter or blur saves, Esc cancels.
- Under the input there is a checkbox whose meaning depends on the mode:
  - local: "Bu projedeki yeni oturumlara da bu adı ver" (scope `project`)
  - cloud: "<owner> adlı kişinin yeni oturumlarına da bu adı ver" (scope `owner`)
- When the effective name comes from a rename (`nameSource` ∈ `custom|project|owner`), a "Varsayılana dön" link appears, and the
  default it will return to is shown in grey (`defaultName`).
- The UI updates optimistically and keeps a local override until a snapshot or SSE event reflects the change (max 60 s).
  On error it reverts and shows a short toast ("Ad kaydedilemedi").
- Cloud, view-only link (no key): the ✎ button is hidden.
- Names are rendered with `esc()` in the DOM and with `fillText` on the canvas. Label on canvas: `name·project` (cloud) / `name` (local).

### 15.2 Name resolution (single rule, both modes)

Agent JSON gets two additive fields. v1 clients ignore them.
- `nameSource`: `"custom" | "project" | "owner" | "file" | "reported" | "auto"`
- `defaultName`: the name a reset would produce

| Priority | Local mode (`pixel_office`) | Cloud mode (`pixel_cloud.derive`) |
|---|---|---|
| 1 | UI rename, scope `agent` (`custom`) | rename field `n:<agentId>` (`custom`) |
| 2 | UI rename, scope `project`, keyed on `projectPath` (main agents only) (`project`) | rename field `no:<ownerId>` (main agents only) (`owner`) |
| 3 | legacy `names.json` at the project root (v1, read-only) (`file`) | — |
| 4 | name reported by a status-API client (`name` field, §5.5) (`reported`) | same (`reported`) |
| 5 | v1 hash name (Ada, Linus…), or `agentType` for subagents (`auto`) | owner name, or `agentType`/"Alt-ajan" for subagents (`auto`) |

**Agent state updates never touch renames.** In the cloud, renames live in separate hash fields, and an agent write is an `HSET` of
its own field only. The hook client never sends `name`. A status client's `name` is priority 4, below every rename.
Locally, renames live in a separate file, and `name_for()` consults it on every tracker update.

Why both scopes: a Claude Code `session_id` (and so the agent id) changes with every new session. Scope `agent` alone would be lost
the next day. The owner scope (cloud) or project scope (local) is the "my next sessions are called Dilek too" default.

### 15.3 Cloud: `POST /api/rename?room=<roomId>`

Headers: `Authorization: Bearer <writeKey>` (the page has it from the `#key` fragment, §3.2), `Content-Type: application/json`. Body ≤ 1 KB.
```json
{"scope": "agent", "agentId": "a1b2c3d4e5f60718", "name": "Dilek"}
{"scope": "owner", "ownerId": "o_Xk3…", "name": "Robo-Timur"}
{"scope": "agent", "agentId": "a1b2c3d4e5f60718", "name": null}          // reset (null or "")
```
- Validation:
  - `agentId` must match `^a[0-9a-f]{16}$`, and `ownerId` must match the §3.1 charset.
  - `name` goes through the §5.5 sanitising pipeline (NFC, control/bidi/zero-width strip, whitespace collapse, trim), then is limited to 1–24 code points.
  - A non-empty input that becomes empty after sanitising → 400 `bad_request`.
  - The agent or owner does not need to exist currently: renaming a just-departed agent is harmless, and the field ages out.
- Storage: one `HSET po:room:R n:<agentId> {"name":"Dilek","ts":…}` or `no:<ownerId> …`. Reset = `HDEL`. Plus the amortised `EXPIRE` (§4.3).
  **Cost: 1 Redis command and 1 invocation per rename. Reads cost nothing extra**, because the same `HGETALL` already returns these fields.
- Limits: at most 96 `n:`/`no:` fields per room. When a new field would exceed that, the server first deletes stale `n:` fields
  (agent record absent and `ts` > 6 h old) in the same pipeline, else returns 429 `room_full`. Rate limit: per room 10 renames/min, in-process.
- Lazy purge (§6.2 rule 1) also removes `n:<id>` fields whose agent has been gone for > `PURGE_AFTER`. `no:` fields live as long as the room.
- Response `200 {"ok": true, "name": "Dilek", "scope": "agent", "serverTime": …}`. Errors as in §5.
- Freshness: after a successful rename, the renaming client polls once with a cache-busting `&_=<ms>` (one CDN miss, one extra
  `HGETALL`). Other viewers see the change within one CDN TTL plus their poll interval (≤ ~15 s when active).
- Who may rename: anyone holding the write key, which is everyone the invite was shared with. Same trust model as writes.

### 15.4 Local: `POST /api/agents/rename` (server.py, additive)

Body `{"id": "<agent id>", "name": "Dilek" | null, "scope": "agent" | "project"}`. With `scope: "project"`, the server takes
`projectPath` from the agent's current record. If the agent is unknown → 404. Validation is the same as §15.3 (shared function
`pixel_cloud.validate.clean_name`).
- Persistence: `names_store.py` keeps `{"version":1,"agents":{id:{"name","ts"}},"projects":{path:{"name","ts"}}}` in the user data dir:
  - Linux: `$XDG_DATA_HOME/pixel-office/names.json` (default `~/.local/share/pixel-office/names.json`)
  - macOS: `~/Library/Application Support/pixel-office/names.json`
  - Windows: `%APPDATA%\pixel-office\names.json`

  The file is outside the repo, so it is out of git by construction (`names.json` is also added to `.gitignore` for the legacy root file).
  It is written atomically (temp + `os.replace`), mode 0600, and capped at 1000 agent entries (oldest `ts` pruned). It can be overridden
  with `--names-file PATH`. The file is created only on the first rename.
- On success: update the stored `AgentInfo.name` and re-upsert, so `AgentStore` emits **`agent_updated`** over SSE (the existing event,
  no protocol change). It also applies to agents from the local status API (§16).
- Response `200 {"ok": true, "agent": {…Agent…}}`.
- Backwards compatibility:
  - The legacy root `names.json` keeps working (priority 3).
  - `GET /api/agents` and SSE payloads only gain `nameSource`/`defaultName`.
  - Any POST to any other path is still 405, and the existing tests stay valid.
  - `GET /api/health` gains `"features": ["rename", "status"]`, which is what the page uses to show the ✎ button in local mode.

## 16. Local server additions (server.py): generic status API

Goal: **one client works against both backends by changing only the base URL** (plus room/key for the cloud).

| | Local (`http://127.0.0.1:8765`) | Cloud (`https://<host>`) |
|---|---|---|
| Endpoint | `POST /api/status` (a `?room=` param is accepted and ignored) | `POST /api/status?room=<roomId>` |
| Body / response / errors | identical to §5.5 (shared `pixel_cloud.validate`) | §5.5 |
| Auth | none when bound to loopback. When `--host` is not loopback, `Authorization: Bearer <local key>` is required; the key comes from `--write-key` or is generated and printed at startup | write key |
| CSRF guard | requests carrying an `Origin` header must match the server's own origin, and `Content-Type: application/json` is required (forces a CORS preflight, which the local server never approves) | CORS `*` + Bearer |
| Time transitions | `pixel_office/reported.py`: a ticker thread (1 s) applies the **same `pixel_cloud.derive` rules** (§6.2) and calls `store.upsert`/`store.remove` | read-time derive |

- Local agent id: `"r" + sha256("local|" + ownerId + "|" + session + "|" + (sub or ""))[:16]`. `ownerId` defaults to `"n_" + sha256(lower(owner))[:10]`. If `owner` is omitted, it defaults to the OS user name, local only.
- Reported agents appear alongside transcript agents in both `live` and `demo` mode, with `source` shown in the details panel.
- The Claude Code hook client is **not** meant for the local server, since transcripts already cover Claude Code there. The local
  status API is for non-Claude agents. If someone does it anyway, the result is a duplicate character, not breakage.
- `pixel_office` imports `pixel_cloud.validate`/`derive`. Both are stdlib-only, so the local server keeps its zero-dependency property.
  `.vercelignore` still excludes `pixel_office/`. The import direction is local → shared only.
- Payload additions (both backends, §5.5):
  - `"name"` (optional, 1–24 code points): the reported display name, priority 4 in §15.2.
  - `"final": true` (optional, only with `state: "sleeping"`): marks the record finished. A new §6.2 rule 2b removes it once
    `fin and age > FINISHED_REMOVE_AFTER (300 s)`. This lets wrappers end with "goes to bed, then leaves" without staying alive.

## 17. Local AI agents (Ollama, LM Studio, llama.cpp, aider, Open Interpreter, custom scripts)

All three tools live in **one stdlib file, `web/hook/pixel_agent.py`** (Python ≥ 3.8). It can be downloaded from the site or copied
into a project. `pixel_agent.py install-shims` writes small launchers to `~/.pixel-office/bin/` (`pixel-run`, `pixel-proxy`;
`.cmd` variants on Windows) and prints the PATH line to add.

**Privacy (same rule as the hook client, §7.4):** these clients send only state, tool name, label, project basename and name/owner.
Never prompts, model output, command lines, arguments, stdout/stderr content, request/response bodies or API keys.

### 17.1 Configuration (shared by library, wrapper and proxy)

Resolution order: explicit arguments → env vars `PIXEL_OFFICE_URL`, `PIXEL_OFFICE_ROOM`, `PIXEL_OFFICE_KEY`, `PIXEL_OFFICE_OWNER` →
`~/.pixel-office/config.json` (written by the hook installer, §7.5) → default `http://127.0.0.1:8765` (local, no room/key).
The POST URL is `{url}/api/status` plus `?room={room}` if a room is set. Key → `Authorization: Bearer`.

### 17.2 Library API (`pixel_agent.Agent`)

```python
from pixel_agent import Agent

bot = Agent("research-bot", project="notes")        # session = slug(name) unless session= given; owner from config
bot.report("thinking")                               # never raises, never blocks: queued to one daemon sender thread
bot.report("working", tool="web_search", label="Web'de arıyor")

with bot.working(tool="llm", label="Özetliyor"):     # context manager: working on enter → idle on normal exit
    summarize()                                      #   exception → idle with label "Hata" (exception re-raised, text never sent)

@bot.task(tool="embed")                              # decorator, same semantics as working()
def embed_all(): ...

bot.waiting("Onay bekliyor")                         # waiting_for_user
bot.done()                                           # sleeping + final:true (bed, then leaves after 300 s)
bot.offline()                                        # leaves now; also registered with atexit (1 s timeout, best effort)
```

Sender policy (in the library, so every tool built on it inherits it):
- Dedupe identical updates. Keep-alive every 300 s while the process is alive.
- **Local target:** 0.3 s debounce, no further throttling.
- **Cloud target (room set):**
  - Group-level changes are debounced 1 s and sent immediately. The groups are `{thinking, working}`, `{waiting_for_user}` and `{idle, sleeping, offline}`.
  - Changes inside the active group (thinking↔working, tool/label changes) are sent at most once per 30 s.
  - This keeps a tight agent loop (one LLM call every 2–3 s) at ≈ 2 + 120 writes/hour instead of ~1 500.
- Network errors are retried once, then dropped. A 429 is dropped. Nothing is printed unless `PIXEL_OFFICE_DEBUG=1`.

CLI: `python3 pixel_agent.py report --name aider --state working [--tool T] [--label L] [--project P]`. One-shot and synchronous, for shell scripts.

### 17.3 Command wrapper: `pixel-run`

```sh
pixel-run --name aider --project myapp -- aider --model ollama/qwen2.5-coder
pixel-run --name interpreter -- interpreter --local
pixel-run --name nightly --idle-after 30 --on-exit offline -- ./train.sh
```
Options: `--name` (default: basename of the command), `--project` (default: basename of the cwd), `--tool` (default: the command
basename, sanitised to the §5.5 tool charset), `--idle-after N` (default 15 s), `--quiet-state idle|waiting_for_user` (default `idle`),
`--on-exit sleeping|offline` (default `sleeping` + `final`).

Behaviour:
- **POSIX:** the child runs on a **pseudo-terminal** (stdlib `pty`/`os.openpty`, relaying both directions, propagating window size
  on SIGWINCH and setting raw mode on our TTY). Interactive TUIs such as aider and Open Interpreter keep working. Output is
  byte-counted only, never inspected or stored.
- If stdout is not a TTY (CI, pipes), it uses plain pipes instead of a pty.
- States:
  - Start → `working` (tool = command basename).
  - Child output seen → `working`.
  - No output for `--idle-after` s → `--quiet-state`.
  - Output again → `working`.
  - Exit code 0 → `sleeping` + `final` (label "Bitti").
  - Non-zero exit code → `sleeping` + `final` (label "Hata: kod N").
  - With `--on-exit offline` → `offline`.
- SIGINT/SIGTERM/SIGHUP are forwarded to the child. The wrapper exits with the child's exit code. A reporting failure never affects the child.
- **Windows:** the stdlib has no pty, so the child inherits the console and output can't be observed. The state is `working` from
  start to exit, then the exit states. `--pipe` opts into pipe mode, which gives idle detection but breaks interactive TUIs.
  This limitation is stated in the README.

### 17.4 OpenAI-compatible / Ollama passthrough proxy: `pixel-proxy`

```sh
pixel-proxy --listen 127.0.0.1:11435 --upstream http://127.0.0.1:11434 --name ollama       # Ollama
pixel-proxy --listen 127.0.0.1:1235  --upstream http://127.0.0.1:1234  --name lmstudio     # LM Studio
pixel-proxy --listen 127.0.0.1:8081  --upstream http://127.0.0.1:8080  --name llamacpp     # llama.cpp server
```
Point any local agent's base URL at the proxy (e.g. aider `OLLAMA_API_BASE=http://127.0.0.1:11435`, or OpenAI-SDK
`base_url="http://127.0.0.1:11435/v1"`).

- **Server:** `ThreadingHTTPServer`, HTTP/1.1, binds `127.0.0.1` by default. A non-loopback `--listen` requires `--allow-remote`.
- **Forwarding is byte-transparent.** Method, path, query, headers and body go to `--upstream` via `http.client`.
  - Hop-by-hop headers are dropped (`Connection`, `Keep-Alive`, `Proxy-*`, `TE`, `Trailer`, `Transfer-Encoding`, `Upgrade`), and so is `X-Pixel-Agent`.
  - `Authorization` is passed through and never logged.
  - The response status and headers are relayed unchanged.
  - The response body is relayed **as it arrives**: `read1(65536)` → write → flush, re-framed with `Transfer-Encoding: chunked`
    when the upstream used chunked or sent no length. SSE (`text/event-stream`) and Ollama NDJSON streams therefore stream token by token, unchanged.
  - No request/response timeouts on streams. Connect timeout 10 s → 502 `{"error":"upstream_unreachable"}`.
  - Client disconnect → close upstream → finish state.
- **Agent identity**, first match wins:
  1. Header `X-Pixel-Agent: <name>`.
  2. Path prefix `/agent/<name>/…`, which is stripped before forwarding (`/agent/aider/v1/chat/completions` → `/v1/chat/completions`).
  3. `--name`, default `"local-llm"`.

  Session = slug(name), so each name is one character. Invalid names fall back to `--name`.
- **Generation endpoints** (POST only): `/v1/chat/completions`, `/v1/completions`, `/v1/responses`, `/api/chat`, `/api/generate`
  (Ollama), `/completion` and `/completions` (llama.cpp), `/api/v0/chat/completions` and `/api/v0/completions` (LM Studio). Everything else
  (model lists, embeddings, health) is forwarded without touching state.
- **State machine** per agent:
  - Request start → `thinking`, tool `"LLM"`. The in-flight count is tracked, and the state stays `thinking` while it is > 0.
  - While relaying, a **copy** of the bytes feeds a bounded scanner (non-stream JSON ≤ 8 MB; streams line by line, each line ≤ 1 MB).
    The scanner looks for tool calls: OpenAI `choices[].message.tool_calls` / `choices[].delta.tool_calls[].function.name`,
    Responses API `output[].type == "function_call"`, Ollama `message.tool_calls[].function.name`.
  - Response finished with tool calls → `working`, tool = first function name (sanitised to the tool charset, else `"tool"`).
    The agent is now executing tools, and its next request flips it back to `thinking`.
  - Finished without tool calls → `idle` (turn over; `sleeping` follows after 180 s via derive).
  - Upstream error status → `idle`, label "LLM hatası".
- **Privacy:** the model name is **not** sent by default (`--send-model` puts it in `label`, truncated). Prompts, messages,
  completions and tool arguments are never sent. The scanner keeps only booleans and function names, and nothing is logged.
- **Cost:** reporting goes through the library sender (§17.2), so a cloud room sees ≈ 2 writes per turn plus ≤ 1 per 30 s, as with the hook client.

### 17.5 Copy-paste examples

```sh
# curl — local (no key) and cloud (room + key): only the base URL / auth differ
curl -X POST http://127.0.0.1:8765/api/status -H "Content-Type: application/json" \
  -d '{"session":"my-agent","name":"Kâtip","owner":"Timur","project":"notes","state":"working","tool":"llm","source":"custom"}'
curl -X POST "https://<host>/api/status?room=<roomId>" -H "Authorization: Bearer <writeKey>" -H "Content-Type: application/json" \
  -d '{"session":"my-agent","owner":"Timur","state":"sleeping","final":true}'
```
```js
// JS (browser, Node 18+, Deno, Bun) — tiny helper, no dependencies
const PIXEL = {url: 'http://127.0.0.1:8765', room: null, key: null};   // cloud: {url:'https://<host>', room:'<roomId>', key:'<writeKey>'}
export async function pixel(state, extra = {}) {
  const q = PIXEL.room ? `?room=${PIXEL.room}` : '';
  try {
    await fetch(`${PIXEL.url}/api/status${q}`, {method: 'POST',
      headers: {'Content-Type': 'application/json', ...(PIXEL.key ? {Authorization: `Bearer ${PIXEL.key}`} : {})},
      body: JSON.stringify({session: 'js-agent', owner: 'Timur', source: 'custom', state, ...extra})});
  } catch { /* never let reporting break the agent */ }
}
// await pixel('thinking'); await pixel('working', {tool: 'search', label: "Web'de arıyor"}); await pixel('idle');
```
Note for the browser: a page on another origin can't call the **local** server (§16 CSRF guard). Browser-based agents should
use the cloud API or run under the proxy.

### 17.6 Budget impact (cloud)

Assume 10 friends × 1 h/day of local-agent use at ≈ 130 writes/h (library cloud policy) → +1 300 writes/day.
That adds ≈ +39K CDN requests/invocations and ≈ +43K Redis commands per 30-day month. Stress totals become
CDN ≈ 826K (83%), Upstash ≈ 406K (81%) with the Phase 10 numbers (§12.3, §17.10), still within the free tiers. Renames are negligible (1 command each). Local-server use costs nothing.

---

### 17.7 Phase 7 implementation notes — deviations (2026-10-11)

Decisions taken where this spec was silent or contradicted itself. Everything else is implemented as written.

1. **v1 tests vs. additive fields.** The v1 suite asserts the exact `/api/health` body and the exact `AgentInfo.to_dict()` key set.
   - `make_server(...)` without `reported=`/`names=` is byte-for-byte v1: no write endpoints, v1 health body. `server.py` always
     passes both, so the real server has `POST /api/status`, `POST /api/agents/rename` and `"features": ["rename", "status"]`.
   - `nameSource`/`defaultName` come from `AgentInfo.to_dict(names=True)`. `AgentStore` always serialises that way, so
     `/api/agents` and SSE carry them, while a plain `to_dict()` keeps the v1 key set.
2. **Local name resolution lives in the store, not in `name_for()`.** `AgentStore(mode, namer=names_store.Namer(...))` applies
   §15.2 on every upsert and keeps the raw agent. `store.refresh_names()` re-resolves after a rename and emits `agent_updated`
   only for agents whose JSON changed. `name_for()` is unchanged (legacy file → hash name).
3. **`agentType` charset.** §5.5 says `[A-Za-z0-9 _-]{1,24}`, but §7.4 makes the hook client send `"Özel ajan"`. `agentType`
   is therefore sanitised free text, at most 24 code points.
4. **Agent / name limits use `HKEYS`, not `HLEN`.** `HLEN` also counts `_meta` and up to 96 `n:`/`no:` fields. `HKEYS` costs
   the same (1 command, only after an `HSET` that created a field) and counts only agent fields. The 96-name limit runs as
   `HSET` → `HKEYS` → (only if over 96) `HGETALL` + `HDEL` of stale `n:` fields, or `HDEL` of the new field + 429 `room_full`.
   This happens after the write, not in the same pipeline.
5. **Missing `stateAgeMs` / `startedAgeMs`.** The cloud keeps `ss`/`sa` from a per-warm-instance LRU (4096 agents) of the
   last write it saw. On a miss they become "now". The local server keeps them exactly. Out-of-range ages and `toolCount`
   are clamped. `color` outside 0–15 → 400.
6. **Room links.** `PIXEL_PUBLIC_URL`, when set, takes precedence over `Host`/`X-Forwarded-Host`. It is the canonical
   production domain (§13.4 step 7) and it ignores spoofed or internal hosts. An invalid `Host` gives relative links (`/?room=…`).
7. **`POST /api/rooms` order.** The per-IP creation limit is checked before the create token, so the token can't be brute-forced.
8. **Extra error codes.**
   - `internal_error` 500: unexpected exception. No details are returned or logged, only the exception type.
   - `hook_mapper_unavailable` 503: `cc_hook` when `web/hook/pixel_hook.py` (Phase 8) is missing or has no `map_event`.
   - `room_full` 429 carries `Retry-After: 60`.
   - `storage_unavailable` 503 carries `Retry-After: 5`.
9. **Secret.**
   - On Vercel, a missing `PIXEL_SECRET` or one shorter than 32 chars → room creation and writes return 503
     `storage_not_configured`. There is never a random secret in production.
   - Off Vercel, a missing secret gets a random per-process value (dev).
10. **`cc_hook` contract for Phase 8.** The server calls `map_event(event, cfg, None)` with
    `cfg = {"room", "name", "owner", "ownerId", "salt", "send_mcp_names": False, "hide_projects": False, "project_aliases": {}, "enabled": True}`,
    where `salt = HMAC-SHA256(PIXEL_SECRET, "pixel-cc-salt:v1:" + roomId)` hex[:32].
    - It accepts a list of §5.5-shaped dicts, or objects with `as_payload()`/`to_dict()`/`_asdict()`, or dataclasses.
    - The server overrides `owner`/`ownerId`/`source="claude-code"`, drops `name`, validates with `parse_update` and keeps at most 10.
    - The raw body is dropped right after parsing. Mapper exceptions are swallowed (200 `{}`).
11. **Local write guard.**
    - The CSRF guard (Origin + `Content-Type: application/json`) and the optional key apply to **both** `/api/status` and `/api/agents/rename`.
    - Allowed origins are `http://127.0.0.1|localhost|[::1]:<port>` plus the bound address. They are not derived from `Host`, which
      defends against DNS rebinding.
    - A wrong content type → 400 `bad_request`, because §5 has no 415.
    - `--write-key` is enforced on loopback too when it is given. Off loopback the rename also needs the key, so the Phase 9 UI can
      rename only on loopback unless it is given the key.
12. **Local reported agents.**
    - The auto name is the v1 hash name (main agents) or `agentType`/"Alt-ajan" (subagents).
    - Their JSON adds `source`; transcript agents don't get it.
    - They are capped at 48 → 429 `room_full`.
    - Rename scope `project` needs a `projectPath`, so status-API agents and subagents get 400 and must use scope `agent`.
13. **Derive details.**
    - Derived `sleeping` (rules 5/6) clears both tool and label.
    - A subagent whose parent is gone keeps `isSubagent: true` with `parentId: null`.
    - Unparseable agent records are purged with the lazy `HDEL`.
    - An unknown `project` is shown as `"?"` (v1 convention).
14. **HTTP details.**
    - `OPTIONS` preflight is answered on every POST endpoint (rooms, status, rename, cc_hook), not only `/api/status`. `HEAD` works on GET endpoints.
    - Chunked request bodies are read with the same caps.
    - An unread oversized body closes the connection (`Connection: close`).
    - Status and rename writes invalidate the 2 s micro-cache of that warm instance.
15. **Tests.**
    - `tests/test_rename.py` and `tests/test_local_status.py` (listed for Phase 10 in §14) were created now, because the Phase 7
      acceptance criteria need them. Phase 10 extends them.
    - `tests/test_cloud_devserver.py` was added for `dev_server.py`/`demo.py`.
    - The v1 suite has 59 tests, not 58, and all pass unchanged.

### 17.8 Phase 8 implementation notes — deviations (2026-10-11)

User-facing guide (Turkish): [`docs/INTEGRATIONS.md`](INTEGRATIONS.md). Everything else in §7 and §17 is implemented as written.

1. **`map_event` output.** It returns a list of plain §5.5 dicts. Keys whose value would be null are **omitted**, and the
   server treats a missing `tool`/`label` as cleared. The hook path yields at most one update per event. `owner`/`ownerId`
   come from `cfg["name"]` (or `cfg["owner"]`) and `cfg["ownerId"]`. With `cfg` from §17.7 #10, `/api/cc_hook` now works:
   it is covered by `tests/test_hook_client.py` and was checked live, and it no longer returns 503.
2. **"last" = last *mapped* state.** The `last_state` passed to `map_event` is the record's pending state if there is one,
   else the last state sent. This applies to PostToolUse after waiting and the Notification rule. Without it, a
   PostToolUse arriving inside the 1 s debounce of a PermissionRequest would leave the character stuck on "!".
3. **Notification waiting types** send nothing when the record is already `waiting_for_user`. PermissionRequest usually
   comes first, and re-sending would only drop its tool name.
4. **State file additions.** Besides `seq/last/pending/since/started/tc` it stores the hashed `s`/`sub` ids, which
   SessionEnd uses to find the session's subagents, and `atype`, the last built-in agent type. Later subagent events
   without `agent_type` keep the type.
   - File name = `sha256(salt + hashedSession + (hashedSub or ""))[:16]`.
   - Writes are atomic, mode 0600, under an `fcntl` lock on POSIX and a named mutex in PowerShell.
   - SessionEnd deletes all of that session's files. Files older than 7 days are pruned.
5. **SessionEnd** sends `offline` for **every** subagent of the session that is not already offline, including sleeping
   ones. This is one batched request (≤ 10), with timeout 2.5 s, no retry and no debounce. Otherwise a sleeping subagent
   would linger for 120 s after its parent left.
6. **Watchdog.** On POSIX, `SIGALRM` ends a hook process after 30 s, or after 4 s for SessionEnd, with exit 0. Stdin is
   read fully (cap 64 MB, beyond that it is drained and ignored). The log records only *known* event names plus the HTTP
   status or `neterr`.
7. **StopFailure enum allow-list:** `rate_limit, authentication_failed, billing_error, invalid_request, server_error,
   max_output_tokens, overloaded, unknown`. Anything else → plain "API hatası". Event names, tool names and agent types
   are compared case-sensitively.
8. **Aliases** match the exact path or any subdirectory (longest prefix wins). Windows drive paths are compared
   case-insensitively. `/` or `\` inside an alias become `-`. An invalid tool name is dropped, so the detail falls back to
   "Çalışıyor". With `send_mcp_names`, an MCP tool name is cut to 32 characters.
9. **Installer extras:**
   - `--settings-path` (in addition to `PIXEL_OFFICE_CLAUDE_SETTINGS`), `--no-test`.
   - `--command-style shell` (one quoted command string instead of `command` + `args`), in case a Claude Code version
     ignores `args`.
   - `uninstall` also drops empty matcher groups, event arrays and an empty `hooks` object. It keeps `~/.pixel-office`.
   - A hidden `map` subcommand (`{"event","cfg","last"}` on stdin) exists in both clients so that
     `tests/hook_vectors.json` can run against `pwsh`.
   - `test` sends `offline` after 20 s from a detached `_offline-later` process.
10. **PowerShell client (untested, no `pwsh` on the dev machine; reviewed statically):**
    - The file is pure ASCII (Turkish labels built from `[char]` codes) because PowerShell 5.1 reads BOM-less files in
      the ANSI code page. `install.ps1` saves downloads as UTF-8 *with* BOM.
    - JSON is parsed with `JavaScriptSerializer` (MaxJsonLength raised) on 5.1, avoiding `ConvertFrom-Json`'s 2 MB limit,
      and with `ConvertFrom-Json -AsHashtable` on 7.
    - `ConvertTo-Json` on 5.1 does not keep 2-space indentation in settings.json. The JSON is still valid and foreign
      entries are kept.
    - `$HOME` is read-only in PowerShell, so the office dir variable is `$oh`.
11. **`pixel_agent.py`:**
    - Agents send `source` `custom` (library), `pixel-run` or `pixel-proxy`. The proxy and wrapper send `name` = agent
      name (priority 4, §15.2).
    - Target resolution: url/room/key come as one unit from the first source that has a URL (args → env → config.json →
      default). `ownerId` is taken from config.json only when the owner name is the configured one.
    - Sender: a group change is due 1 s after the *first* pending change (no starvation in tight loops), and an
      intra-group change is due ≥ 30 s after the last send.
    - Dropped sends (4xx/429/network error after one retry) count as sent, so there is no hot retry loop.
    - Due updates are batched (≤ 10 per request) on both backends.
    - `atexit` sends `offline` only if the agent is not finished (`done()` keeps "bed, then leave").
12. **`pixel-run`:**
    - The pty uses `os.openpty` + `subprocess.Popen(start_new_session=True)`, with `TIOCSCTTY` in the child, instead of
      `pty.fork`. This avoids fork-with-threads warnings, since the sender thread is already running.
    - Raw mode is set with `TCSANOW`, so type-ahead input is not discarded.
    - `--pty`/`--pipe` force a mode.
    - A command that is not found → exit 127 and label "Hata: kod 127".
    - In the cloud, a short `working` burst right before exit is merged into the final `sleeping` by the 1 s debounce. This
      is expected.
13. **`pixel-proxy`:**
    - `/chat/completions` (llama.cpp alias) was added to the generation endpoints.
    - `Expect` and `Host` are not forwarded (`Host` = upstream).
    - The scanner also understands OpenAI `function_call` and Responses-API stream events (`item`,
      `response.output`), and gzip/deflate bodies. Other encodings disable tool detection only.
    - On client disconnect the state is finished as idle.
    - Proxy logs are disabled entirely.
14. **Tests:** `tests/test_hook_client.py` (61 vectors, privacy canary incl. a real HTTP end-to-end run, coalescing with a
    fake clock, installer, cc_hook) and `tests/test_pixel_agent.py` (listed for Phase 10 in §14, created now because the
    Phase 8 acceptance criteria need it). The PowerShell vector test is skipped when `pwsh` is missing.

### 17.9 Phase 9 implementation notes — deviations (2026-10-11)

Files: `web/js/transport.js`, `cloud.js`, `pip.js` (new); `main.js`, `agents.js`, `renderer.js`, `ui.js`, `index.html`,
`css/style.css` (edited); `web/manifest.webmanifest`, `web/sw.js`, `web/icons/*.png`, `tools/make_icons.py` (new).
`sprites.js`, `office.js` and `pathfinding.js` are unchanged. Everything else in §9–§11 and §15.1 is implemented as written.

1. **Local `GET /api/config` (backend tweak).** The v1 404 from §9.1 shows up in Chrome's console as a failed resource
   load, which breaks the "no console errors" criterion. `pixel_office/httpd.py` therefore answers
   `GET /api/config` with `200 {"mode":"local","version":"1.0.0","features":[…]}`, but only when the server has the v2
   extensions (`server.py` always does). A bare `make_server(...)` still answers 404, so v1 stays byte-for-byte the
   same (§17.7 #1). The page treats anything other than `mode: "cloud"` as local and reads the ✎ feature from
   `config.features`, falling back to `/api/health` for older servers. The local server also serves `.webmanifest` as
   `application/manifest+json`. Tests: `tests/test_local_status.py` (`test_config_endpoint_v2_only`, `test_webmanifest_mime`).
2. **Backend detection retries on network errors.** Any HTTP answer decides the mode, so a v1 404 still means local.
   A *network error* (for example an offline PWA start served from the service-worker cache) is retried with back-off from
   2 s to 30 s, while the page shows "Yeniden bağlanıyor…". Without this, an offline start would fall back to local SSE
   mode and stay there.
3. **Transport API additions.**
   - `handlers.onError(code)`: `PollingTransport` calls it with `'not_found'` on a 404 and stops. The UI then shows
     "Oda bulunamadı" (connection status `notfound`).
   - `pollNow({bust: true})` appends `&_=<ms>`, used once after a rename (§15.3).
   - `requests` counts polls, so tests can check that nothing is sent while the page is hidden.
   - `SseTransport` has no-op `setScheduler`/`setActive`/`pollNow`, so `main.js` can treat both transports alike.
   - The 30 s hidden grace period lives in `main.js` (`updateActive`), not in the transport.
4. **Rename semantics** (§15.1 leaves these open):
   - The "future sessions" checkbox is **unchecked** by default.
   - Without the checkbox, the name is saved with scope `agent`.
   - With the checkbox, the name is saved with scope `owner` (cloud) or `project` (local). If the agent currently has
     an agent-scope name, that name is reset in the same action, so the new default is visible on this agent too.
   - "Varsayılana dön" removes the rename layer that is currently winning: `custom` → agent scope `null`, `owner`/`project`
     → that scope `null`.
   - The checkbox is hidden for subagents (both modes) and, locally, for agents without a `projectPath` (status-API agents, §17.7 #12).
   - An empty or unchanged name with the checkbox unchecked counts as cancel. The `<input>` has `maxlength=24`.
   - Optimistic overrides apply to every affected main agent (same owner or same project) and are dropped once the
     server shows the name, or after 60 s. If the request fails, the name reverts (cloud: cache-busting poll; local:
     `GET /api/agents`) and the toast "Ad kaydedilemedi" appears.
   - While the input is open, snapshots do not re-render the details panel, so the input survives polls and SSE events.
5. **Mini window.**
   - It is also offered in local mode, but only via Document PiP. The popup fallback needs `?room=`, so it exists only in cloud mode.
   - The requested PiP size is 400×290 (270 + the status strip).
   - The PiP body gets `class="mini pip"`. The strip uses short Turkish counts ("3 çalışıyor · 1 bekliyor · 2 kahvede").
   - The loop guards against stale rAF callbacks with a token, and the frame clock is reset on every host switch,
     because each window has its own `performance` time origin.
   - On open, a toast tells the user to drag the window to the bottom-right. The popup fallback's tooltip says it is
     not always-on-top.
   - The button is hidden in `?view=mini` and when neither PiP nor a desktop popup is available (coarse pointer).
6. **Setup dialog ("Ajan bağla").**
   - The display name is stored in `po.name`. In commands it is reduced to letters, digits, space and `._-` (max 24,
     fallback `Arkadas`), so it is safe inside single quotes in sh and PowerShell. Room ids and keys have fixed safe charsets.
   - sh commands use single quotes. The pixel-run/pixel-proxy examples pass `--url --room --key --owner` explicitly
     (no config file needed), after `curl …/hook/pixel_agent.py -o pixel_agent.py`.
   - The HTTP-hook snippet (opt-in, behind the red warning and a checkbox) uses `type: "http"`, `timeout: 3`, and the
     9 events of §7.6. `PreToolUse`/`PermissionRequest` get `matcher: "*"`. Its headers are `Authorization`, the
     URL-encoded `X-Pixel-Owner` and `X-Pixel-Owner-Id`, a random per-browser id kept in `localStorage["po.ownerId"]`.
   - Windows visitors see the Windows tab first.
7. **Rooms.**
   - After "Oda oluştur", the page opens `?room=…&new=1`. That shows the invite dialog once, and the marker is then
     removed from the address bar.
   - "Son odalar" marks each room as `katılımcı` (key stored) or `izleyici`, and has an "Unut" button that removes the room and its key.
   - A malformed `?room=` shows the landing with an error.
   - The room name is shown in the top bar and the document title.
8. **Labels and list.**
   - The canvas label is `name·project` (cloud). The default `name` is the owner, so this equals `owner·project` until
     a rename. An unknown project (`"?"`) is omitted. Subagents get `↳ name`. The 18-character limit counts code points.
   - List rows show an owner colour dot and `owner · project`. The list is grouped by owner, with a group header that shows the agent count.
   - The details panel gains `Sahip`, `Kaynak` (a readable source name), and `Not` (the label when there is no tool).
9. **PWA.**
   - The manifest also has `"id": "/"` and `"description"`.
   - `sw.js` passes `/sw.js` itself through, caches only `ok` same-origin (`basic`) responses, and matches static files
     with `ignoreSearch`.
   - The `<link rel="manifest">` is static in `index.html` for both modes. The page's own "Uygulamayı yükle" button
     appears only in cloud mode, but Chrome's own install UI may also be offered locally.
   - Icons: the maskable art is 288/512 px (56 %, inside the 80 % safe-zone circle). `apple-touch-icon.png` is 180 px,
     with 160 px of art.
10. **Known behaviour (fixed in Phase 10, §17.10).** A brand-new (empty) room was polled every 60 s, so the first agent
    could take up to about 60 s to appear. Phase 10 adds a 5 s boost.

### 17.10 Phase 10 implementation notes — QA fixes and deviations (2026-10-11)

1. **Vercel function detection (deploy blocker, fixed).** Vercel decides whether `api/x.py` is a function by parsing it
   (`find_app_or_handler_impl`, §18): only a literal top-level `class handler` (or `app`/`application`) counts. The
   seven files had `handler = make_handler()`, so a real deploy would have had **no** API at all. Each file now reads
   `class handler(ApiHandler): …`. `tests/test_release.py` ports the detector to `ast` and checks every file, and runs
   one handler behind a real socket. The rest of the §13.2 setup (`framework: null`, `outputDirectory: "web"`, `functions`
   glob, `.python-version` 3.12) was confirmed against Vercel's source. The documented rewrites fallback is not needed.
2. **Fast first appearance.** `PollingTransport.boost({ms, emptyMs, hold})` polls every `POLL_FAST_MS` = 5 s:
   - for 3 min after opening a room, but only while it has no agents (`emptyMs`);
   - while the "Ajan bağla" dialog is open (`hold`) and for 3 min after it closes (`ms`).
   The empty-room CDN TTL drops from 30 s to 5 s (`CDN_TTL_EMPTY`), otherwise the CDN would hide the first agent anyway.
   `pollMs` for empty rooms stays 60 s, so viewers who aren't boosting cost the same as before. Hidden pages stay paused.
   Measured in headless Chrome: the first agent of a new room appeared 3.4 s after its first hook event (was up to ~60 s).
   Budget: §12.2/§12.3 updated (CDN 79%, Upstash 73% in the stress month).
3. **Installer and symlinks.** `install`/`uninstall` now follow a symlinked `~/.claude/settings.json` (dotfiles setups)
   and write the target. Before, the atomic replace swapped the link for a plain file. The PowerShell twin is unchanged
   (symlinked settings are rare on Windows, and it can't be tested here).
4. **Setup commands.** `cmdName` also strips leading `-`/`.`, so a name like `-x` can't be read as an option by argparse
   or PowerShell. Quoting was already safe.
5. **Release files.** `.gitignore`, `LICENSE` (MIT), `.vercelignore` also drops `*.pyc`, `.env*` and `.pixel-office/`.
   `sw.js` `VERSION` → `po-v2.0.1`. Personal absolute paths were removed from docs and test vectors.
6. **Tests added:** `tests/test_release.py` (17), a names-file failed-write test in `test_rename.py`, a pixel-proxy
   client-disconnect test in `test_pixel_agent.py`. 251 tests in total (1 skipped without `pwsh`).

## 18. Sources (all accessed 2026-10-11)

- Vercel, *Using the Python Runtime with Vercel Functions* (last updated 2026-08-12): Python 3.12 default, 3.13 and 3.14 available;
  entry-point detection list includes `server.py`. https://vercel.com/docs/functions/runtimes/python
- Vercel, *Python Functions in the /api Directory* (2026-08-12): each `api/*.py` = one function; top-level `handler`
  (BaseHTTPRequestHandler subclass), `app` or `application`; framework presets take precedence. https://vercel.com/docs/functions/runtimes/python/api-directory
- Vercel, *Limits* (2026-09-16): Hobby max duration 300 s; Hobby can't import Git repos owned by organisations. https://vercel.com/docs/limits
- Vercel, *Fair Use Guidelines* (2026-09-14): Hobby includes 1M invocations, 4 h Active CPU, 360 GB-h memory, 100 GB FDT, 10 GB FOT;
  Hobby is non-commercial only. https://vercel.com/docs/limits/fair-use-guidelines
- Vercel, *Pricing* page: Hobby "CDN Requests 1M / month included". https://vercel.com/pricing
- Vercel, *CDN Cache* (2026-09-14): functions cached with `s-maxage`; GET/HEAD only; no `Authorization`/`Set-Cookie`;
  `CDN-Cache-Control`/`Vercel-CDN-Cache-Control`; cache is per region. https://vercel.com/docs/caching/cdn-cache
- Upstash, *Redis pricing* and *Billing* docs: free = 500K commands/month, 10 GB bandwidth, 250/256 MB, 1 DB;
  pipeline/transaction commands billed individually. https://upstash.com/pricing/redis, https://upstash.com/docs/redis/overall/billing
- Upstash, *REST API* docs: JSON-array commands, `/pipeline`, `/multi-exec`, Bearer token. https://upstash.com/docs/redis/features/restapi
- Vercel Marketplace, *Upstash KV* page, and Upstash redis-js docs: `KV_REST_API_URL/TOKEN` and `UPSTASH_REDIS_REST_URL/TOKEN` both in use.
  https://vercel.com/marketplace/upstash/upstash-kv
- Supabase free plan (mid-2026 third-party summaries plus supabase.com/pricing): 200 concurrent Realtime, 2M messages, pause after 7 days inactive.
  https://supabase.com/pricing
- Anthropic, *Claude Code hooks reference*: event list, stdin fields, `async`, `http` hooks, `args` exec form, SessionEnd 1.5 s default,
  Windows notes. https://code.claude.com/docs/en/hooks
- Chrome for Developers, *Document Picture-in-Picture API* (last updated 2024-09-10): Chrome/Edge 116, Firefox 151, no Safari;
  user gesture required; position cannot be set; `pagehide` on close; never outlives opener.
  https://developer.chrome.com/docs/web-platform/document-picture-in-picture

Phase 10 additions (accessed 2026-10-11):
- Vercel source, `packages/fs-detectors/src/detect-builders.ts`: api detection (`api/**/*.py` → `@vercel/python`) is independent of
  `outputDirectory`; with no framework/build command the static builder is `@vercel/static` for `<outputDirectory>/**/*`.
  https://github.com/vercel/vercel/blob/main/packages/fs-detectors/src/detect-builders.ts
- Vercel source, `packages/python-analysis/crates/vercel_python_analysis/src/entrypoint.rs` (`find_app_or_handler_impl`):
  a file is a Python entrypoint only with a top-level `app`/`application` (assignment, def, import) or a top-level **class**
  named `handler`. https://github.com/vercel/vercel/blob/main/packages/python-analysis/crates/vercel_python_analysis/src/entrypoint.rs
- Vercel source, `packages/python/src/index.ts`: a Python function bundles every project file (`glob('**')`) minus
  `.vercelignore` and predefined excludes (`**/__pycache__/**`, `.git/**`, …); `includeFiles` is therefore belt and braces.
- Vercel docs *Python runtime* and *Python functions in /api* (both last updated 2026-08-12), *vercel.json* (2026-08-14):
  `framework: null` = "Other"; Python 3.12 default, 3.13/3.14 available via `.python-version`.

Still unverified (needs a real deploy): Upstash free-plan behaviour on exceeding 500K; whether Chrome remembers the PiP window
position per origin; exact Marketplace env var names (code accepts both pairs); `x-vercel-cache: HIT` on repeated `/api/state`.
