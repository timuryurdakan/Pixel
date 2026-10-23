# Pixel Office: Security Audit (Phase 16, "Siber Güvenlik Denetimi")

Date: 2026-10-19 · Baseline: commit `14c4a46` (public at github.com/timuryurdakan/Pixel) plus the uncommitted Phase 16
changes described here · Live targets: https://www.pixeloffice.com.tr (Vercel Python `api/` + static `web/` + Upstash) and
https://pixel-relay.yurdakantimur.workers.dev (Cloudflare Worker + Durable Object).

**Result:** 1 High, 4 Medium, 6 Low, 9 Info. The High and all four Mediums are **fixed, each with a regression test**
(`tests/test_phase16_security.py`), as are the cheap Lows. One Low supply-chain item needs Node on the user's side (S16-08). Nothing
in the public git history needs rotating. The live site still runs the pre-Phase-16 code until these changes are pushed.

## 1. Scope and method

| Area | What was done |
|---|---|
| Repo hygiene | `git log -p --all` (11 commits, 2.7 MB of patches) grepped for token formats (GitHub `ghp_`/`github_pat_`/`gho_`, AWS `AKIA`, Slack, Anthropic `sk-ant-`, OpenAI-style `sk-`, Vercel, Upstash `AX…`), private-key headers, `.env`/`*.pem`/`*.key`/`config.json` paths, `PIXEL_SECRET`/`RELAY_SECRET`/`UPSTASH_*`/`KV_REST_*`/`VERCEL_TOKEN`/`CLOUDFLARE_API_TOKEN` assignments, 26-char room ids, 32-char write keys, e-mail addresses, Turkish phone numbers, absolute home paths, and Claude session UUIDs. Every real secret on this machine (`~/.pixel-office/config.json`, `connector/connector.json`, `keys.json`, `control.json`, `agents.json`) was compared byte-for-byte against the history without printing it. Every historical `pixel-connector.pyz` blob was unzipped and checked for unexpected files and local paths. Test fixtures were checked for real data. `.gitignore` and `.vercelignore` were reviewed. |
| Live config | About 60 read-only requests: public-path probes, response headers on HTML and `/api/*`, TLS, every `/api/*` endpoint with missing or invalid auth, an oversize body and wrong methods, relay `/health`, and WS upgrades without a ticket, with a foreign Origin and with the ticket in the URL. No rooms were created, nothing was brute-forced and no load was generated. |
| Code | Risk-ordered review of the connector command surface and pairing, `/onay`, runtime and workspace allow-lists, Claude argv/env/permission rules, the server (HMAC write keys, tickets, rate limits, Redis key handling, CRLF, path traversal, SSRF, error messages), the client (every `innerHTML` sink, `rehber.html`, `sprite-preview.html`, `postMessage`, SW caching, key storage, `#key`, Referer, CSP), supply chain, crypto use (building on the Phase 15 line-by-line review in V3.md §20, which this audit re-checked rather than repeated), and the privacy promise. |
| Tests | One regression test per fix. The full suite was run after each step: `python3 -m unittest discover -s tests` → **481 tests, OK (1 skipped: no `pwsh`)**; `tools/build_connector.py --check` passes. |

Out of scope: Cloudflare/Vercel/Upstash account settings (the auditor has no access), denial of service by volume, and the
user's own machine apart from reading the connector config with secrets masked.

## 2. Findings

Severity: **High** = a realistic path to code execution on the user's machine or to reading prompts.
**Medium** = a security boundary is weakened, or a free account can be exhausted by an outsider.
**Low** = needs an unusual precondition, or the impact is small. **Info** = hygiene or a documented limit.

| ID | Severity (why) | Component | Description | Exploit scenario | Status | Fix / test |
|---|---|---|---|---|---|---|
| S16-01 | **High**: escapes the "edit = no shell" promise and leads to arbitrary code execution as the user | `pixel_connector/engines/claude.py` | The built-in deny rules only covered reading `~/.ssh` etc. Every profile that can edit (`edit`, `dev`, `auto`) could write files that become code later and are mostly invisible in `git diff`: `.git/hooks/*`, `.git/config` (`core.fsmonitor`, `core.hooksPath`), `.claude/settings*.json` (hooks), `.mcp.json` (MCP servers), `.vscode/` tasks, `.envrc`, shell start-up files, `~/.config` (autostart, systemd user units), `~/.local/bin` shims, and the Claude binary itself (`~/.local/share/claude`, `~/.vscode/extensions`). | A prompt injection in repo content or a web page (T10), or a compromised origin acting as a paired browser (T4), asks an `edit`-profile agent to write `.claude/settings.json` with a `PreToolUse` hook, or `.git/config` with `core.fsmonitor=<cmd>`. The next task, or the dev profile's allowed `git status`, runs the command. | **fixed** | `EXEC_SENSITIVE_DENY` (Edit rules in both `//**/` and `**/` form, plus `~/…`) and `SECRET_READ_DENY` (`~/.claude.json`, `~/.netrc`, `~/.git-credentials`, `~/.config/gh`, `~/.docker/config.json`, `~/.kube`) are always passed in `--disallowedTools`. Tests: `ClaudeExecSensitiveDeny`, `test_connector_claude`. Residual (updated in Phase 20, see S20-01): the 2026-10-21 review (v2.md H1) found three gaps — a `.git` *file* (`gitdir:` redirection to a directory with an agent-written config), the scripts that `.claude/settings*.json` runs (`.claude/hooks/*`, commands, agents) and JetBrains `.idea/` run configurations. All three are closed in S20-01, together with git pins in the child env. What remains: edits to tracked build files inside the workspace (Makefile, package.json scripts, `.husky/*`) are the feature, so review them with git before running them; the rules were not replayed against a live Claude Code run, only checked in the argv. |
| S16-02 | **Medium**: lets any website read prompts and outputs | `pixel_office/httpd.py` (v1 local server) | No Host check. With DNS rebinding, a page on `attacker.example` that re-resolves to 127.0.0.1 is same-origin with the local server. | The user visits a malicious page while `server.py` runs. The page rebinds and reads `/api/agents` (`lastPrompt`, `lastMessage`, project paths) and `/events`. | **fixed** | `host_allowed()` accepts only IP literals, `localhost`, this machine's own name (`name`, `name.local`), the `--host` value and `PIXEL_OFFICE_ALLOWED_HOSTS`. Any other Host gets `421`. Tests: `LocalServerHostCheck` (3 tests). Existing raw-socket tests now send `Host: 127.0.0.1`. README documents the variable. |
| S16-03 | **Medium**: an outsider can exhaust a free-tier account | `pixel_cloud/app.py` `/api/state`, `/api/health` | Both endpoints are unauthenticated, and each call reaches Upstash: one `HGETALL` per CDN miss, which any new room id causes, and one `PING` per health call. Free Upstash and Vercel quotas are monthly, so a single client in a loop could get the account throttled or paused for every room. | `while true; curl /api/state?room=<random valid id>` | **fixed** (per instance) | Per-IP token bucket `RATE_IP_STATE` = 2/s with a burst of 120, checked **before** any storage command. `/api/health` reuses its PING for 15 s. Tests: `test_state_is_rate_limited_per_ip_before_storage`, `test_health_reuses_the_ping`. Residual: the limiters live in memory per Vercel instance, so a distributed attacker is not stopped (see S16-15). |
| S16-04 | **Low**: no authentication bypass, but unvalidated key material | `pixel_cloud/ids.py`, `validate.py`, `app.py` | Format regexes ended in `$`, which in Python also matches before a final `\n`. **Confirmed live**: `/api/state?room=<26 chars>%0A` returns 200 with `"id":"…\n"`, and Upstash gets a key containing a newline. Write keys are HMACs, so they cannot be forged this way. | Unbounded distinct cache and storage keys, and log or UI confusion. | **fixed** (live after deploy) | `\Z` in `ROOM_RE`, `WRITE_KEY_RE`, `AGENT_ID_RE`, `OWNER_ID_RE`, `SESSION_RE`, `TOOL_RE`, `SOURCE_RE` and `_HOST_RE`. Test: `test_trailing_newline_is_rejected`. |
| S16-05 | **Medium**: tampering with installers on the network | `web/hook/install.sh`, `web/hook/install.ps1` (v2 hook installers) | Phase 15 F2 hardened only the *connector* installer. The hook installer ran line by line under `curl … \| sh`, so a truncated download executed partially. It also accepted `--server http://public-host`, which lets a network attacker swap `pixel_hook.py`. That file is then registered as a Claude Code hook, so it runs on every tool call. | Hostile Wi-Fi with `http://` in the command means attacker code runs as a Claude Code hook. | **fixed** | `install.sh` puts everything in `main()`, called on the last line. Both installers accept only `https://`, or `http://` for loopback, RFC 1918, `*.local`, `*.lan` or single-label hosts (local mode on the LAN). The `pwsh` path is quoted. Tests: `HookInstallerHardening` (4 tests). |
| S16-06 | **Medium**: one config mistake removes the deny list | `pixel_connector/workspaces.py` | `check_denied` rejected the home directory and paths *under* denied folders, but not *ancestors* of home. A workspace `/home` or `C:\Users` contained `~/.ssh`, `~/.config`, `~/.pixel-office` and everything else. | The user adds `/home` "to reach all projects". A paired browser then picks the `ali/.config/autostart` sub-folder and the agent edits files there. | **fixed** | An ancestor of home is denied ("an ancestor of the home directory"). Test: `WorkspaceAncestors`. The allow-list UX (defaults, CLI) is unchanged and left for Aşama 17. |
| S16-07 | **Low**: misdirection only, inside the owner's own paired devices | `web/js/main.js` / `prompt.js` | Anyone with the room write key can post a v2 status record whose `ctl` field points at one of *your* connector agents, under a misleading name and character. | A room member posts "Docs bot (read-only)" with `ctl` set to your `dev` agent. You type a task there. | **accepted** | The task can only reach an agent on a device that *your* browser paired. The panel's profile badge and folder hint come from the device (`agent.list`), not from the record. Documented, with no code change. |
| S16-08 | **Low**: builds are not reproducible, but E2E (T1/T2) bounds the impact | `relay/package.json` | `wrangler` is pinned exactly (4.145.0), but there is no lockfile, so the Cloudflare build's `npm install` re-resolves wrangler's transitive tree and runs its install scripts on every build. | A compromised transitive package gets into the deployed Worker. Under the threat model a malicious relay can only drop or delay traffic and see metadata, not read or inject commands (T1, T2). | **needs-user** | No Node on the audit machine. See user action 4: commit `relay/package-lock.json` and switch the build to `npm ci`. |
| S16-09 | **Low** (documented limit) | `web/connector/install.sh`, `install.ps1` | The installers verify the `.pyz` against `SHA256SUMS` fetched **from the same origin**. That catches corruption and truncation, not a compromised Vercel deployment (T5). | An attacker who controls the site serves a matching `.pyz` and `SHA256SUMS`. | **accepted** | The digest is published in README, which is versioned in git. The installer prints it, and the user compares it (README "Bağlayıcı"). There is no auto-update. Recommendation: sign releases later (minisign or Sigstore). |
| S16-10 | **Low**: defence in depth | `vercel.json`, `dev_server.py` | The live CSP lacks `object-src 'none'` and `form-action`, and there is no `Permissions-Policy`. | Narrows any future injection, e.g. a form to a foreign target or a plugin object. | **fixed** (live after deploy) | `object-src 'none'; form-action 'self'` and `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=()`. Test: `HardeningHeaders`. |
| S16-11 | **Info** | `docs/ARCHITECTURE.md` (history since `305baa5`) | The example agent JSON used a real Claude Code session UUID from the author's machine. It is not a credential: resuming needs local access to `~/.claude`. | None remotely. | **fixed** in the tree (placeholder UUID). History kept. |
| S16-12 | **Info** | git history `4fe71f3` `docs/V3.md` (removed again in `3fef576`) | Absolute paths under the author's home directory (`~/.local/bin/claude`, `~/.lmstudio/bin/lms`, written out in full) reveal the local username and tool locations. | Recon only. The username is already public via the GitHub handle. | **accepted**: no rewrite needed. |
| S16-13 | **Info** | git metadata, all 11 commits | The author e-mail `yurdakantimur@gmail.com` is public in commit metadata. This is normal for git, but it can be avoided. | Spam or phishing. | **needs-user** (optional): user action 5. |
| S16-14 | **Info** | `web/sprite-preview.html` | Its inline `<script type=module>` is blocked by the production CSP (`script-src 'self'`), so the page is blank on Vercel. It reads no untrusted input. | None (functional only). | **accepted**: it is a local dev tool (README: served by the local server). |
| S16-15 | **Low**: availability of free tiers | relay on `workers.dev`, Vercel functions | Every request counts toward the 100k/day Worker quota and Vercel invocations, even one rejected with 401. A distributed flood can take v3 control offline until 00:00 UTC (T19). Per-instance in-memory limiters (S16-03) don't stop many IPs. | DDoS-style abuse. | **accepted** | Recommendation: move the relay to a custom domain on a Cloudflare zone and add a free WAF rate-limiting rule. Set usage alerts (user action 7). |
| S16-16 | **Info** | `pixel_cloud/vercel.py` `client_ip` | Rate limits key on the first `X-Forwarded-For` entry. On Vercel this is safe: Vercel overwrites `X-Forwarded-For` and does not forward client-supplied values. `dev_server.py` trusts it, but it is local-only. | Spoofing XFF only works against a self-hosted deployment behind no proxy. | **accepted** | Documented here. Anyone self-hosting must sit behind a proxy that overwrites XFF. |
| S16-17 | **Info** | Vercel HSTS | `strict-transport-security: max-age=63072000` without `includeSubDomains`/`preload`. The apex and plain-http requests redirect with 308 to https://www. | First-visit SSL strip on other subdomains. | **accepted**: recommendation only, because enabling `includeSubDomains` needs a check that every subdomain serves HTTPS. |
| S16-18 | **Info** | CORS | `Access-Control-Allow-Origin: *` on static files and `/api/*`, and `OPTIONS` allows `Authorization`. | None: no endpoint uses cookies, every write needs a bearer write key, and responses carry no per-user data beyond what the room URL already reveals. | **accepted** |
| S16-19 | **Info** | `/api/rooms` | Room creation requires `PIXEL_CREATE_TOKEN` (live: `createRequiresToken: true`, invalid token returns 403). The limit is 5 per hour per IP, counted before the token check, so the token can't be brute-forced. | None found. | **verified**, no change |
| S16-20 | **Info** | test suite | The S16-02 Host check made 4 raw-socket SSE tests (`Host: x`) loop forever on `readline()`, and the suite never finished. | None (tests only). | **fixed**: those tests send `Host: 127.0.0.1`. |

### Verified without findings (this phase)

- **Remote command execution.** View-only visitors and room members can't reach the connector: commands need an E2E channel
  from a browser that was paired with the code and confirmed with the SAS in the terminal. The relay can't inject (AEAD with
  AAD `ch|dir|seq`, strict seq). Runtimes are referenced by id only, use a fixed argv and `shell=False`. Model names match
  `^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$`, so no leading `-`, and are passed as their own argv element. The prompt goes in on
  stdin. `auto` is refused unless enabled locally, both at create/update time and per task. Workspaces are re-resolved per
  task. `FORBIDDEN_ARGS` are never built. `child_env` strips `PIXEL_*`, `UPSTASH_*`, `KV_REST_*` and `RELAY_SECRET` (Phase 20: replaced by an allow-list, S20-02).
- **`/onay` page.** It enforces a strict Host check and a page token that is separate from the control token (Phase 15 F1).
  The cookie is HttpOnly, `SameSite=Strict` and `Path=/onay`. Each form carries an HMAC-derived CSRF token, which a
  same-site page on another local port can't read. The CSP is `default-src 'none'` with `frame-ancestors 'none'`, and every
  field goes through `html.escape`.
- **Server.** Write keys are `HMAC(PIXEL_SECRET, room)`, compared in constant time (`compare_digest`); auth runs before the
  body is read (live: a 300 KB body with a bad key returns 401). Tickets are HMAC-verified before parsing, valid ≤ 120 s and
  bound to their room. Redis keys are built only from regex-validated ids and sent as JSON to the Upstash REST API, so there
  is no protocol injection. Responses use fixed header values (no CRLF). Static serving uses `resolve()` and
  `is_relative_to(root)` in both the v1 server and `dev_server.py`. There is no server-side fetch of user-supplied URLs (no
  SSRF). Errors are generic JSON, with internal exceptions logged by type name only, and storage errors never include the
  token or URL.
- **Client.** Every `innerHTML` sink in `ui.js` escapes with `esc()` (`& < > " '`). Colours are hex-validated and state
  colours are looked up with `Object.hasOwn`. v3 output, names and paths go through `textContent`. `rehber.html`
  (`guide.js`) only uses a regex-validated `room` and a fixed `ilk` flag. There are no `postMessage`, `eval`, `new Function`
  or `document.write` calls. The SW never intercepts `/api/*`, `/hook/*` or `/connector/*`; it only caches same-origin
  `basic` responses and is purged by `VERSION` (bumped to `po-v3.0.2`). The `#key=` fragment is read, stored in localStorage
  and removed with `history.replaceState`, so it is never sent to a server; `Referrer-Policy: no-referrer` is set
  everywhere. E2E keys are non-extractable `CryptoKey`s in IndexedDB. Write keys in localStorage are readable by any XSS,
  which the strict CSP mitigates; write keys are not control credentials (T6). There are no external script, style or font
  loads.
- **Crypto (re-checked).** Fresh ephemeral ECDH keys on every connection, so there is no (key, IV) reuse across reconnects
  or restarts. Direction-separated keys and the `dir` in the AAD prevent role confusion. The SAS is 6 digits (2^-20) on top
  of a code-keyed MAC transcript with one attempt per code. PBKDF2-SHA256 runs 600,000 iterations. Failures are never
  answered (no oracle). Comparisons use `compare_digest` and `subtle.verify`.
- **Privacy promise.** The hook clients send only allow-listed fields, and the server additionally forces `lastPrompt`,
  `lastMessage`, `title`, `projectPath` and `model` to `null` for cloud rooms (`derive.py`). Connector prompts and outputs
  travel only inside E2E frames. The audit log stores a SHA-256 and length unless `audit.storePrompts` is set (it is off on
  this machine).
- **Hook installer and `~/.claude/settings.json`.** The installer makes a backup first, refuses invalid JSON, follows a
  symlinked settings file instead of replacing it, removes only its own marker entries, and uses exec form (an absolute
  interpreter and an args list, no shell). Shell form is `shlex.quote`d.
- **.pyz build.** It is reproducible: `tools/build_connector.py --check` rebuilds byte-identically. Every historical `.pyz`
  contains only `pixel_connector/*`, `__main__.py` and `pixel_agent.py`, with no local paths and no `__pycache__`.

## 3. Secrets scan (public history)

**Result: clean. No rotation needed. No history rewrite needed.**

- No token of any listed format, no private key, no `.env` or credential file ever committed, and no `PIXEL_SECRET`,
  `RELAY_SECRET` or Upstash value.
- None of this machine's real values appear anywhere in the history: room ids and write keys from both configs, the hook
  salt and owner id, the device keys, and the control token. The checked files were `connector.json`, `keys.json`,
  `control.json` and `agents.json`.
- The room ids in history are all synthetic (`abcdefghijklmnopqrstuvwxyz`, `zyxwv…`, `k7q2m4x5…`). The only "keys" are
  RFC/Wycheproof test vectors (`000102…`).
- Personal data:
  - the author e-mail in commit metadata (S16-13)
  - absolute home-directory tool paths in `4fe71f3:docs/V3.md`, removed in `3fef576` (S16-12)
  - one real Claude session UUID in `docs/ARCHITECTURE.md` since `305baa5`, now replaced (S16-11)
  - the host `pixel-relay.yurdakantimur.workers.dev`, which is public by design
- No phone numbers.
- Test fixtures use placeholders only (`{CWD}`, `{SESSION}`, `/home/u`, `/Users/u`).
- `.gitignore` now also ignores `*.key`, `*.p12`, `.npmrc` and `.claude/settings.local.json`.
- `.vercelignore` keeps `tests/`, `docs/`, `tools/`, `relay/`, `pixel_connector/`, `server.py`, `pixel_office/` and
  `PROJECT.md` out of the deployment. `outputDirectory: web` means only `web/` is served statically.

## 4. Live configuration (2026-10-19, before this phase is deployed)

| Check | Result |
|---|---|
| Public paths | `/.git/`, `/.git/config`, `/README.md`, `/tests/`, `/pixel_cloud/`, `/pixel_cloud/app.py`, `/api/index.py`, `/.env`, `/relay/`, `/docs/`, `/docs/V3.md`, `/PROJECT.md`, `/vercel.json`, `/.vercelignore`, `/server.py`, `/pixel_connector/` all return **404**. `/api/state.py` returns a JSON 404 (routed to the function, no source). `/hook/pixel_hook.py` and `/connector/SHA256SUMS` return 200, as intended. |
| TLS | TLS 1.3 (X25519MLKEM768). TLS 1.1 is refused. The cert is `*.pixeloffice.com.tr`, valid until 2027-01-14. The relay also uses TLS 1.3. `http://` and the apex both return 308 to `https://www.` |
| Headers (HTML and `/api/*`) | `Content-Security-Policy` (`default-src 'self'`, `script-src 'self'`, `connect-src 'self' wss://pixel-relay.yurdakantimur.workers.dev`, `frame-ancestors 'none'`, `base-uri 'none'`), `Strict-Transport-Security: max-age=63072000`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`. Missing: `Permissions-Policy`, `object-src` and `form-action` (S16-10, fixed in the repo). `Access-Control-Allow-Origin: *` (S16-18). |
| `/api/rooms` | GET → 405. PUT → 405. POST with a wrong `createToken` → 403. |
| `/api/status` | No auth → 401. Wrong bearer → 401. 300 KB body with a wrong bearer → 401 (auth before body). Bad room → 404. GET → 405. |
| `/api/relay_ticket`, `/api/rename`, `/api/residents`, `/api/cc_hook` | No or invalid auth → 401. |
| `/api/state` | DELETE → 405. No room or `../../etc` → 404. **`room=<id>%0A` → 200 with a newline in the id (S16-04, fixed in the repo).** |
| CORS preflight from `https://evil.example` | 204, `*`, `Authorization, Content-Type`. Acceptable: bearer auth, no cookies. |
| Errors | Generic JSON (`{"error","message"}`). Unknown `/api/*` paths get Vercel's plain 404. No stack traces. |
| Relay | `/health` → `{"ok":true,"v":1}`, `no-store`. Plain GET on the WS path → 426. WS upgrade without a ticket (no Origin, the right Origin, or `https://evil.example`) → 401. Ticket in the URL (`?ticket=`) → 401 `bad_ticket`, because only `Sec-WebSocket-Protocol` is read. Bogus ticket in the protocol → 401. The Origin check runs after ticket verification and returns 403 `bad_origin`; it is covered by `relay/test/core.test.js` and the conformance suite, since a live test needs a valid ticket, which needs a write key. `/` → 404. |

## 5. Recommendations

1. Deploy this phase, then re-run the two live checks in the user action list.
2. Commit a relay lockfile and build with `npm ci` (S16-08).
3. For sensitive machines, keep `confirm.task: "always"`. T4 (a compromised origin) remains inherent to web-based E2E.
4. Later (v4): sign connector releases (minisign/Sigstore) and pin the public key in the installer, instead of trusting
   same-origin `SHA256SUMS` (S16-09). Also require proof of device-key possession when minting `device` tickets (Phase 15
   A1).
5. Turn on GitHub secret scanning with push protection, and Dependabot alerts (free for public repos).
6. Set usage alerts on Upstash, Vercel and Cloudflare. Consider moving the relay to a custom domain with a WAF
   rate-limiting rule (S16-15).
7. HSTS `includeSubDomains` once every subdomain serves HTTPS (S16-17).

### Constraints for Aşama 17 (workspace setup fix)

- The default workspace `~/PixelOfficeIsler` must be a new folder made by `mkdir` under home, with mode 0700. If the path
  already exists as a symlink or a non-directory, refuse it. Then register it via `realpath`. Never default to `~`, an
  ancestor of home (S16-06), or an existing repository.
- `pixel-connector workspace add|list|remove` is a **local CLI only**. It must not become a remote E2E command, nor a `/ctl`
  route reachable with the `/onay` page token (Phase 15 F1). A browser, which may be a compromised origin (T4), must never
  widen the allow-list.
- `workspace add` must run the same `check_denied()` + `realpath` logic as `config-check`, deny list and ancestor rule
  included, and write `connector.json` atomically with 0600. `remove` must take effect for the next task (tasks re-resolve
  their workspace).
- The UI hint for "no workspaces" is static text plus the local command. It must not take a path from the browser, and any
  device-supplied text goes through `textContent` only.
- Keep `EXEC_SENSITIVE_DENY`/`SECRET_READ_DENY` unchanged. The installer must not edit `~/.claude` (the connector never
  touches Claude credentials, T22).
- Clock skew in `doctor`: compute it from a `Cache-Control: no-store` endpoint (e.g. `/api/health`, made `no-store`, or the
  relay `/health`, which already is), or subtract the `Age` header. Do **not** widen the ticket `iat ≤ now + 30 s` window
  to hide skew. If a rebuilt connector is shipped, update `SHA256SUMS` and the README digest (test-enforced).

## 6. User action list

1. **Review and push this phase** (`git add -A && git commit && git push`; Vercel deploys automatically). Then check:
   - `curl -sI https://www.pixeloffice.com.tr/ | grep -iE "permissions-policy|object-src"` shows both.
   - `curl -s -o /dev/null -w '%{http_code}\n' "https://www.pixeloffice.com.tr/api/state?room=aaaaaaaaaaaaaaaaaaaaaaaaaa%0A"`
     prints `404`.
2. **Update the connector on this computer** so the new Claude deny rules (S16-01) take effect. Re-run the Linux install
   command from Cihazlar → Cihaz ekle. Compare the printed SHA-256 with the one in README
   (`18a4148d7cc2362ea00c7225fe4996170f60738a753a1d6088c5040c979020d9` (geçmiş)). Then run `pixel-connector doctor`. Do the same on
   every other computer that runs the connector.
3. If you open the **v1 local server** (`server.py`) under a host name other than an IP, `localhost` or the computer name
   (e.g. a Tailscale name), start it with `PIXEL_OFFICE_ALLOWED_HOSTS=<name>`.
4. **Relay lockfile** (S16-08), on any machine with Node 22: `cd relay && npm install --package-lock-only --ignore-scripts
   --no-audit --no-fund`, commit `relay/package-lock.json`, and in Cloudflare (pixel-relay → Settings → Build) change the
   build command to `npm ci --no-audit --no-fund && npm test`.
5. Optional (S16-13): to stop publishing your Gmail address in future commits, run
   `git config --global user.email <id>+timuryurdakan@users.noreply.github.com`, and turn on GitHub → Settings → Emails →
   "Block command line pushes that expose my email". The old commits can stay as they are.
6. Optional: GitHub repo → Settings → Code security → enable **Secret scanning** and **Push protection**.
7. Optional: usage alerts in Upstash, Vercel and Cloudflare, plus the weekly usage check that is already on the deploy
   list.

**No secrets need rotating** (`PIXEL_SECRET`, `RELAY_SECRET`, `PIXEL_CREATE_TOKEN`, Upstash and room keys were never
committed).

## 7. Files changed in this phase

`pixel_connector/engines/claude.py`, `pixel_connector/workspaces.py` (rebuilt `web/connector/pixel-connector.pyz`,
`SHA256SUMS`, README digest), `pixel_office/httpd.py`, `pixel_cloud/{app,ids,settings,validate}.py`, `dev_server.py`,
`vercel.json`, `web/hook/install.sh`, `web/hook/install.ps1`, `web/sw.js` (`po-v3.0.2`), `.gitignore`,
`docs/ARCHITECTURE.md` (placeholder UUID), `docs/V3.md` (§6 deny rules), `README.md` (deny rules,
`PIXEL_OFFICE_ALLOWED_HOSTS`, digest), `tests/test_phase16_security.py` (new), `tests/test_connector_claude.py`,
`tests/test_{httpd,rename,local_status}.py` (Host header).

## 8. Phase 20 / Package 1 — Application security follow-up (2026-10-21)

Source: the full review in `v2.md` (items H1, H7, H8, H13, H30). Tests: `tests/test_phase20_security.py` (23 tests; written
first and confirmed failing against the previous code). The connector was rebuilt (`web/connector/pixel-connector.pyz`,
`SHA256SUMS`, README digest); its version number is unchanged (that is Package 2).

| ID | v2.md | Severity | Component | Finding | Status | Fix / test |
|---|---|---|---|---|---|---|
| S20-01 | H1 | **High** (edit → exec, completes S16-01) | `pixel_connector/engines/claude.py` | `Edit(**/.git/**)` did not match a `.git` **file**. An `edit`-profile agent could write `gitdir: <dir>` plus `<dir>/config` with `core.fsmonitor=<cmd>`; the next `git status` (allowed in `dev`) ran it. `.claude/hooks/*` and other scripts referenced from `.claude/settings*.json` were writable, as were JetBrains `.idea/` run configurations. | **fixed** | New Edit rules, each in `//**/` and `**/` form: `.git` (the file), `.claude/**`, `.idea/**`, `*.code-workspace`, `.devcontainer/**`; plus `~/.npmrc`. Selection rule (documented in code): an edit runs code without a further deliberate step by the user, or lives outside version control. Tracked build files stay editable. Defence in depth: the Claude child env pins git at command scope (beats repository config) via `GIT_CONFIG_COUNT`: `core.fsmonitor=false`, `core.hooksPath=<os.devnull>` (`/dev/null`; `nul` on Windows, not tested there), `core.pager=cat`. Inherited `GIT_*` variables (including a user's own `GIT_CONFIG_*`, `GIT_DIR`, `GIT_SSH_COMMAND`) are dropped by the allow-list, so the pins replace them on purpose. `diff.external` is **not** pinned: git has no "unset" value, and an empty one makes every `git diff` fail with "cannot run" (checked with git 2.53); it can only come from repository config, which the Edit rules cover. Tests: `EditToExecDeny` (rules in `--disallowedTools` for all four profiles), `GitPinsInChildEnv` (real git: a planted gitdir config runs fsmonitor, pager and a pre-commit hook without the pins, and nothing with them). |
| S20-02 | H7 | Medium | `claude.py` `child_env`, `app.py` | Only `PIXEL_*`, `UPSTASH_*`, `KV_REST_*` and `RELAY_SECRET` were removed. `GH_TOKEN`, `AWS_*`, OpenAI keys etc. reached a run that may be prompt-injected and has a shell (`dev`/`auto`). | **fixed** | Allow-list `CHILD_ENV_NAMES` (`PATH HOME USER LOGNAME USERNAME SHELL LANG LANGUAGE TERM COLORTERM TMPDIR TEMP TMP TZ`, Windows `SYSTEMROOT SYSTEMDRIVE COMSPEC PATHEXT APPDATA LOCALAPPDATA USERPROFILE PROGRAMDATA PROGRAMFILES PROGRAMFILES(X86) COMMONPROGRAMFILES WINDIR HOMEDRIVE HOMEPATH NUMBER_OF_PROCESSORS PROCESSOR_ARCHITECTURE OS`, `HTTP(S)_PROXY NO_PROXY ALL_PROXY` in either case, `NODE_EXTRA_CA_CERTS SSL_CERT_FILE SSL_CERT_DIR`) and `CHILD_ENV_PREFIXES` (`LC_ XDG_ ANTHROPIC_ CLAUDE_`); the secret prefixes still win. Every configured `engines.openai[].apiKeyEnv` is removed explicitly (even an allow-listed name). Checked: the installed Claude Code 2.1.304 still reports its version and `authMethod: claude.ai` with this env. Limit: Bedrock/Vertex credentials in env variables (`AWS_*`, `GOOGLE_APPLICATION_CREDENTIALS`) are not passed; use the providers' credential files. Ollama/OpenAI engines spawn no processes (HTTP only). Tests: `ChildEnvAllowList` (unit, real fake-claude process, connector wiring). |
| S20-03 | H8 | Medium | `approvals.ctl_request`, `relay.fetch_ticket`, `__main__` (`doctor`, clock skew), `web/hook/pixel_hook.py`, `web/hook/pixel_agent.py` | With `http_proxy` set and no `no_proxy`, urllib sent CLI → service calls (with the `X-Pixel-Ctl` token) and local-mode traffic (with the write key) to the proxy, and the CLI broke. | **fixed** | `pixel_connector/localhttp.urlopen` (and a copy in each standalone hook) opens loopback URLs (`127.0.0.0/8`, `::1`, `localhost`, `*.localhost`) with `ProxyHandler({})`; other URLs keep normal proxy handling. `http.client` users (Ollama, runtimes, the agent proxy) never used proxies. Tests: `LoopbackBypassesProxy` (unreachable proxy in env: control request still reaches a local server; non-loopback still goes through a proxy; no plain `urlopen` left). |
| S20-04 | H30 | Low | `claude.py` `SECRET_READ_DENY`, `workspaces.py` `HOME_DENY_DIRS` | Missing read-deny entries: other agents' logins, registry tokens, keyrings, browser profiles. | **fixed** | Read rules: `~/.gemini/**`, `~/.codex/**`, `~/.npmrc`, `~/.pypirc`, `~/.cargo/credentials`, `~/.cargo/credentials.toml`, `~/.config/gcloud/**`, `~/.azure/**`, `~/.local/share/keyrings/**`, `~/Library/Keychains/**`, `~/.mozilla/**`, `~/.config/google-chrome/**`, `~/.config/chromium/**`. Workspace deny adds `.gemini .codex .docker .kube .azure .cargo .mozilla .local/share .local/bin Library/Keychains Library/LaunchAgents`. A test checks that every `Read(~/<dir>/…)` folder is a denied workspace. Limit: macOS browser profiles under `~/Library/Application Support` (rule paths can't contain spaces). Tests: `SecretReadDeny`. |
| S20-05 | H13 | Low | `dev_server.py --relay` | A `RELAY_SECRET` taken from the environment (possibly the production value) was printed as "this run only". | **fixed** | Printed only when generated for this run; otherwise `RELAY_SECRET: ortamdan alındı (gösterilmiyor)`. Tests: `DevServerRelaySecret`. |

Not verified on this machine: real Claude Code rule matching for the new `Edit(...)` patterns in a live `claude -p` run
(the argv is test-checked; the gitignore-style matching itself belongs to Claude Code), and git's handling of
`core.hooksPath=nul` on Windows.

## 9. Phase 19 / Package 5 — Remote approvals threat model (2026-10-21)

Feature: Claude Code permission requests and `AskUserQuestion` answered from paired browsers (design: `docs/V3.md` §6.9).
Off by default; only `pixel-connector remote-approvals on` (local CLI, writes `connector.json`) enables it. Tests:
`tests/test_phase19_approvals.py`, `tests/test_phase19_ui.py`.

| Threat | Mitigation | Residual risk |
|---|---|---|
| **Stolen / lost phone** answers requests | Only paired, non-revoked browsers with an open E2E channel get `approval.request`; revoke from the CLI (`pixel-connector peers revoke <bid>`) or another browser closes its channels; every decision is audited with the browser id; Claude Code deny rules still win over an allow; no "approve all" | Until revocation, the phone (its unlocked browser profile) can approve each request one by one — the same window as giving it tasks (V3.md T8) |
| **Replay / duplicate answers** | E2E seq numbers + per-connection keys reject replayed frames; each request has a random single-use id (`a` + 96 bits), expiry (`timeoutS` ≤ 280 s) and first-answer-wins under a lock; later answers get `unknown_approval` and change nothing | — |
| **Wrong peer** (unpaired room member, revoked browser, relay) | The relay and the server only see ciphertext; an unpaired browser has no channel; a revoked peer is skipped and its channel closed; remote peers can answer only `toolApproval` items, never the local confirmations (task/agentCreate/runtime/peerRevoke) | — |
| **Malformed answers** | `approval.answer` allow-list: id regex, `decision` ∈ allow/deny, `answers` only for questions, one index list per question, indices in range, exactly one for single choice, no duplicates; the hook checks the indices again against Claude Code's original input | — |
| **CSRF / page token on `/onay`** | Unchanged Phase 15 design: HttpOnly SameSite=Strict cookie holding a page token derived from the control token, CSRF token on every form, Host check; the page token never unlocks `/ctl` (tested); questions can only be denied there | A local page cannot read the cookie or the CSRF token; the user must open the CLI-printed link |
| **Hook spoofing** (a local process calls `/ctl/approvals/tool` or answers) | `/ctl` needs the control token from `~/.pixel-office/connector/control.json` (0600) — exactly what the CLI uses; Host check against DNS rebinding; at most 16 pending requests; a 401 closes the connection | Any process running as the same OS user can read the token and post fake cards or approve them (same as the CLI, V3.md T14). Connector-run agents can't read it (`Read(~/.pixel-office/**)` denied) — unless the user approves such a read from the phone |
| **Information leak** | Summary only: command ≤ 200 chars, file basename, URL host; MCP tool input never sent; text cleaned of control/zero-width/bidi characters; rendered with `textContent` (XSS test); audit stores a 12-hex hash, not the text; the office still gets only `waiting_for_user` | The command text is visible to every paired browser of the owner (by design) |
| **Fail-open** | Feature off, old async hook entry, connector down, timeout, withdrawal, malformed reply, exception → no output, Claude Code shows its own dialog; the hook never allows by itself | — |

## 10. H39 — Edit allow rule was not limited to the workspace (2026-10-22)

| ID | v2.md | Severity | Component | Finding | Status | Fix / test |
|---|---|---|---|---|---|---|
| S22-01 | H39 | **High** (workspace boundary) | `pixel_connector/engines/claude.py` `build_argv` | The `edit` and `dev` profiles passed bare `--allowedTools Edit Write NotebookEdit`. Without a path specifier the rule matches every path, so a connector Claude agent could create or overwrite any file the deny list did not name (shell start-up files are denied; documents, the Desktop, other projects were not). Found while reproducing a user report: with Claude Code 2.1.306, `edit` profile, cwd `PixelOfficeIsler` (temp copy), the prompt "masaüstünde bir txt dosyası oluştur" made Claude write an empty `~/Desktop/yeni_dosya.txt` (removed right after). | **fixed** (3.2.4) — gerçek Claude ile doğrulandı (2026-10-22: klasör içine yazma çalışıyor, masaüstüne yazma reddedildi) | Allow rule `Edit(./**)` (cwd = the resolved workspace; `Edit(...)` covers Edit/Write/NotebookEdit, as the existing deny rules already rely on). The same real run with the scoped rule: Bash and Read on the Desktop denied, nothing written. Plus a boundary system prompt (`--append-system-prompt`, no secrets) so the agent does not try. Tests: `tests/test_h39_e11_boundary_delete.py` (`EditScopeTests`: no unscoped allow rule in any profile), `test_phase20_security`, `test_connector_claude`. |

Doğrulama (2026-10-22, kullanıcı, gerçek Claude Code): `edit` ajanı çalışma klasörünün **içine** dosya yazabildi,
masaüstüne yazma isteği reddedildi. `Edit(./**)` biçimi doğru çalışıyor; mutlak `Edit(//<workspace>/**)` biçimine gerek yok.
`dev` profile commands (`profiles.dev.allow`, e.g. `Bash(npm test *)`) can still write wherever the command writes; that is
the profile's documented trade-off.

## 11. Son güvenlik taraması (2026-10-23)

Proje "tamamlandı" ilan edilmeden önceki son, bağımsız uygulama güvenliği incelemesi. Taban: `main` `12403e9`
(A8 Görev Tahtası birleştirmesi dahil). Sonuç: **2 Orta, 3 Düşük (biri devreden), 4 Bilgi**. Orta ve kolay düzeltilebilen
Düşük bulguların hepsi **düzeltildi, her biri için önce başarısız olduğu görülen bir regresyon testi var**
(`tests/test_phase23_security.py`, `tests/js/approvals_cut.test.js`). Bağlayıcı **3.2.5** olarak yeniden derlendi
(`pixel-connector.pyz` SHA-256 `a039f66952bb1aea7a1317de9991cceb629c58b8bb4b4c5730e3215b5b7d8a1b`), web `po-v3.0.13`.

### 11.1 Kapsam ve yöntem

| Alan | Yapılan |
|---|---|
| Bağlayıcı yetki ve kum havuzu | `engines/claude.py` argv'si (dört profil), `Edit(./**)`, deny listeleri, ortam izin listesi, `--append-system-prompt` metninin temizlenmesi, cwd'nin her zaman çözülmüş çalışma klasörü olduğu (`resolve_workspace` → `popen(cwd=…)`), `workspaces.resolve` (realpath, `..`, sembolik bağ, ata/deny kuralları), `--add-dir` (kullanılmıyor), H38 yol yenileme güven kuralı (`trusted_binary`, çalışma klasörleri hariç), ajan oluşturma/güncelleme/silme yetkileri ve onayları, bütçe, denetim kaydı içeriği. Kurulu Claude Code 2.1.306 ikili dosyasında `--setting-sources` seçeneği ve geçerli değerleri **yalnızca dizgi araması** ile doğrulandı (Claude çalıştırılmadı). |
| Uzaktan onay | Tek kullanımlık kimlik, süre, ilk cevap kazanır, yanlış eş, `/onay` (çerez, CSRF, Host), `/ctl` belirteci, kancanın güvenli başarısızlığı (asla kendiliğinden izin vermez), `AskUserQuestion` cevaplarının özgün girdiye karşı yeniden doğrulanması, özet temizliği (kontrol/bidi/sıfır genişlik), kısaltma. |
| E2E, eşleştirme, röle | Bilet kapsamı/süresi, cihaz bileti (Aşama 15 A1), çerçeve sınırları, Origin listesi (yeni özel alan adı), connector tarafında röle URL doğrulaması (`wss://` zorunlu), TLS doğrulaması, CSP `connect-src` tek ana makine. |
| Bulut API | Doğrulama, hız sınırları, oda/anahtar işleme, hata sızıntısı, eski `residents` kalıntıları (yok). |
| Web | Tüm `innerHTML`/`href`/`postMessage` kullanımları (yeni `board.js`, `approvals.js`, `prompt.js` gruplu retler, `devices.js` dahil), CSP uyumu, servis çalışanı önbelleği ve güncelleme mesajı, localStorage, tıklama hırsızlığı. |
| Tedarik zinciri | Kurulum betikleri (yalnızca https, SHA doğrulaması, `main()` sarmalı), `.pyz`'nin git'te izlenen dosyalardan yeniden üretilebilir derlenmesi (`--check`), tüm geçmişte (56 commit, 4,6 MB yama) gizli bilgi taraması: `ghp_`/`github_pat_`/`gho_`, `AKIA`, `sk-ant-`, Slack, Upstash `AX…`, JWT biçimi, özel anahtar başlıkları, `KV_REST_API_TOKEN`/`RELAY_SECRET`/`PIXEL_SECRET`/`PIXEL_CREATE_TOKEN`/`VERCEL_TOKEN`/`CLOUDFLARE_API_TOKEN` atamaları, `#key=` ve `--key` ile gerçek anahtarlar, 26 karakterlik oda kimlikleri, 32 karakterlik yazma anahtarı biçimi, ev dizini yolları. `.gitignore`. |
| Kanca istemcileri | Gönderilen alanlar (gizlilik), loopback'te vekil sunucu atlaması, Windows kilidi, Antigravity izleyicisi. |
| Canlı (salt okunur) | `GET /` başlıkları, `/api/config`, `/api/health`, röle `/health`, `/sw.js` sürümü, `/connector/SHA256SUMS`. Oda oluşturulmadı, deneme yanılma ve yük yok. |
| Testler | Başta ve sonda `python3 -m unittest discover -s tests`: **750 → 759 test, OK (1 atlandı: `pwsh` yok)**; `tools/build_connector.py --check` geçiyor. |

### 11.2 Bulgular

| ID | Önem | Alan | Bulgu | Durum | Düzeltme / test |
|---|---|---|---|---|---|
| S23-01 | **Orta** (profil sınırı kullanıcının kendi ayarlarına bağlıydı) | `engines/claude.py`, `config.py` | Claude Code izin kurallarını (ve kancaları, `additionalDirectories`, `enableAllProjectMcpServers`) **tüm** ayar dosyalarından birleştirir. Kullanıcının `~/.claude/settings.json` dosyasındaki `"Bash(npm test *)"` ya da çıplak `"Edit"` gibi kurallar ve çalışma klasöründeki git'e girmeyen `.claude/settings.local.json` ("her zaman izin ver" cevapları) bağlayıcının çalıştırdığı **her** ajana ekleniyordu: "Sadece okuma"/"Düzenleyebilir" profiline kabuk açılabiliyor, H39 (S22-01) geri alınabiliyordu. | **düzeltildi** (3.2.5) | Varsayılan `--setting-sources project` (yalnızca sürüm kontrolündeki proje ayarları; güvenilmeyen depo zaten T11). `engines.claude.settingSources` (boş olmayan, tekrarsız `user`/`project`/`local` alt kümesi; geçersizse hata ve varsayılan) ile isteğe bağlı geri açılır (ör. `apiKeyHelper`). Testler: `SettingSourcesTests` (dört profil, sıra, geçersiz değerler argv'ye ulaşmaz, yapılandırma doğrulaması, `initial_config`), `EngineWiringTests` (çalışan bağlayıcı → sahte `claude` argv'si). |
| S23-02 | **Orta** (görülmeyen komut telefondan onaylanabiliyordu) | `web/hook/pixel_hook.py`, `web/js/approvals.js` | `PermissionRequest` kancası uzun bir Bash komutunu 200 karakterde **sessizce** kesiyordu; bağlayıcıya ≤ 200 karakter ulaştığı için o da "…" eklemiyordu. Dolgu ile uzatılmış bir komutun (`npm test -- … ; curl … \| sh`) telefonda yalnızca zararsız görünen başı, kesildiğine dair hiçbir işaret olmadan görünüyordu. | **düzeltildi** | Kanca kesilen özeti "…" ile bitirir; onay kartı "Komut kısaltıldı: sonunu görmeden onaylamayın…" uyarısını gösterir (`summaryWasCut`). Testler: `ApprovalSummaryTruncationTests`, `tests/js/approvals_cut.test.js`. Kanca dosyası `.pyz` içinde değil: kancayı kuranların kurulum betiğini yeniden çalıştırması gerekir (kullanıcı eylemi 2). |
| S23-03 | Düşük (kullanıcının açtığı onay atlanabiliyordu) | `pixel_connector/app.py` `h_agent_update` | `confirm.agentCreate: true` iken yeni ajan yerel onay istiyordu, ama var olan bir ajanın profili `agent.update` ile onaysız yükseltilebiliyordu (onaylanmış "Sadece okuma" ajanı → "Geliştirici"). | **düzeltildi** | Profil değişikliği aynı yerel onayı ister (`Profil değişikliği: <ad> → <profil>`); yalnızca ad/model değişikliği ve aynı profil sormaz; geçersiz profil önce reddedilir. Ortak yardımcı `_confirm_then` (oluşturma da onu kullanır). Test: `ProfileChangeConfirmTests`. |
| S23-04 | Düşük | çalışma klasörleri | Çalışma klasörünün **içinde önceden var olan**, dışarıyı gösteren bir sembolik bağ: `Edit(./**)` kuralının bağın yolunu mu hedefini mi eşlediği Claude Code'un davranışıdır ve canlı denenmedi. `edit` profili bağ oluşturamaz (kabuk yok); `dev` komutları oluşturabilir. | **kabul** | Belgelendi. Öneri: çalışma klasörlerine dışarıyı gösteren bağ koymayın; `workspaces.resolve` alt klasör seçiminde bağları zaten çözer ve dışarı çıkanı reddeder. |
| S23-05 | Bilgi | onay kartı | Read/Edit/Write isteklerinde yalnızca dosya **adı** gösterilir (gizlilik tasarımı, §9); dosyanın konumu telefonda görünmez. | **kabul** | Tasarım gereği. Emin olunmayan istek reddedilmeli; Claude Code penceresi tam yolu gösterir. |
| S23-06 | Bilgi (kullanıcı eylemi) | canlı site | Canlı site depodan geride: `sw.js` `po-v3.0.10`, `/connector/SHA256SUMS` eski `.pyz` (`f0edc6ec…`). 3.2.4 (H39) ve 3.2.5 düzeltmeleri push ve bağlayıcı güncellemesine kadar etkin değil. Başlıklar (CSP tek röle adresi, HSTS, `Permissions-Policy`, `nosniff`, `no-referrer`) doğru. | **kullanıcı** | Kullanıcı eylemi 1. |
| S23-07 | Düşük (devreden S16-08) | `relay/` | Röle derlemesi için kilit dosyası hâlâ yok. | **kullanıcı** | §6 eylem 4 (Node gerekir; bu ortamda indirme yapılmadı). |
| S23-08 | Bilgi (devreden Aşama 15 A1) | röle biletleri | Yazma anahtarı olan biri `device` bileti alıp bir cihazın röle yuvasını devralabilir (hizmet engelleme). E2E sayesinde okuma ve komut enjeksiyonu yok. | **kabul** | v4: cihaz bileti için cihaz anahtarına sahiplik kanıtı. |
| S23-09 | Bilgi | `relay/wrangler.jsonc` | `workers_dev: true`: röle `*.workers.dev` adresinde de açık; özel alan adına konacak bir WAF hız kuralı orayı kapsamaz. Tarayıcı CSP'si ve `/api/config` yalnızca özel alan adını kullanıyor. | **kullanıcı** (isteğe bağlı) | Hiçbir istemcinin `workers.dev` kullanmadığından emin olunca `workers_dev: false` (altyapı dosyası, kullanıcı onayıyla). |

### 11.3 Bulgusuz doğrulananlar

- **cwd ve klasör sınırı.** Claude ajanı çalışma klasörü olmadan oluşturulamaz ve başlatılamaz; her görevde klasör yeniden
  çözülür (realpath, `..` yok, alt klasör kökün altında kalmalı, ata/deny kuralları) ve süreç `cwd` olarak onunla başlar.
  `--add-dir` hiç verilmez. Okuma, Claude Code'un varsayılanıyla cwd ile sınırlı; yazma `Edit(./**)` ile (S22-01, gerçek
  çalıştırmayla doğrulandı). WebFetch/WebSearch ve MCP araçları `dontAsk` altında izin kuralı olmadan reddedilir.
- **Sistem istemi.** `boundary_prompt` yalnızca klasörün görünen adını (≤ 60) ve `~/…` yolunu (≤ 120) içerir; kontrol,
  satır sonu, bidi karakterleri ve `cmd.exe` için tehlikeli `" % ^ !` temizlenir; gizli bilgi ve kullanıcı adı yok.
- **Ortam, deny listeleri, git pinleri, H38.** S20-01/02 ve H38 değişmeden yerinde; otomatik bulunan `claude` yalnızca
  kullanıcı/root'a ait, başkasının yazamadığı yoldan ve çalışma klasörü dışından alınır.
- **Ajan silme (E11).** `remote.agentDelete` ile yönetilir, görevleri iptal eder, geç gelen oturum yazımını düşürür
  (`save_session` dönemi), denetimde istem yok.
- **Denetim kaydı.** İstemler SHA-256 + uzunluk (`storePrompts` kapalı), onay özetleri 12 haneli özet; model çıktısı yok.
- **Uzaktan onay.** §9 tehdit modeli yeniden kontrol edildi: kanca bağlayıcı kapalı/zaman aşımı/bozuk cevap/istisna
  durumunda çıktı vermez; `PIXEL_OFFICE_DISABLE=1` olan bağlayıcı ajanlarında kanca hiçbir şey yapmaz; cevaplar hem
  bağlayıcıda hem kancada özgün girdiye karşı denetlenir; `/onay` soruyu yalnızca reddedebilir.
- **Röle ve E2E.** Aşama 15/16 incelemesinden beri protokol değişmedi. Origin listesi yalnızca üretim adresleri;
  bağlayıcı yalnızca `wss://` (yerelde `ws://127.0.0.1`) röle adresi kabul eder ve TLS'i doğrular.
- **Bulut API.** Aşama 16 ve A11'den beri değişmedi; `residents` uç noktası ve kodu tamamen kaldırılmış.
- **Web.** `ui.js` dışında `innerHTML` yok (orada her değer `esc()`/sayı/izin listesi); Görev Tahtası, onay kartları,
  gruplu retler ve cihaz sayfası yalnızca `textContent` kullanır. `postMessage` yalnızca sitenin kendi servis çalışanından
  (sürüm dizgisi regex ile denetlenir). CSP satır içi betik izni vermez; `frame-ancestors 'none'`.
- **Gizli bilgi taraması.** Temiz; döndürülecek sır yok. Geçmişteki tek kişisel iz §3'tekilerle aynı (S16-12/13).
- **Sürüm paketi.** `.pyz` yalnızca `pixel_connector/*`, `__main__.py`, `pixel_agent.py` içerir, yerel yol yok,
  `--check` bayt bayt aynı.

### 11.4 Kullanıcı eylemleri

1. **Push** (Vercel otomatik dağıtır). Sonra `curl -s https://www.pixeloffice.com.tr/sw.js | grep VERSION` →
   `po-v3.0.13`; `/connector/SHA256SUMS` → `a039f669…`.
2. **Bağlayıcıyı güncelleyin** (her bilgisayar, arkadaşınızınki dahil): Cihazlar → Cihaz ekle → kurulum komutu; yazılan
   SHA-256'yı README ile karşılaştırın; `pixel-connector doctor`. Uzaktan onay kullanan bilgisayarlarda **kanca kurulumunu
   da yeniden çalıştırın** (S23-02, `pixel_hook.py`).
3. Kullanıcı ayarlarınızda Claude Code için gerekli bir şey varsa (`apiKeyHelper`, `env`), `connector.json` →
   `"engines": {"claude": {"settingSources": ["user", "project"]}}`; bunun o dosyadaki izin kurallarını da her profile
   eklediğini bilerek.
4. Devreden: röle kilit dosyası (§6 eylem 4), GitHub gizli bilgi taraması + push koruması (§6 eylem 6), kullanım
   uyarıları; isteğe bağlı `workers_dev: false` (S23-09) ve özel alan adında Cloudflare WAF hız kuralı (S16-15).

**Döndürülmesi gereken sır yok.**

### 11.5 Değişen dosyalar

`pixel_connector/{engines/claude.py,config.py,app.py,__init__.py}` (3.2.5; `web/connector/pixel-connector.pyz`,
`SHA256SUMS`, README özeti), `web/hook/pixel_hook.py`, `web/js/approvals.js`, `web/css/style.css`, `web/sw.js` +
`web/js/cloud.js` (`po-v3.0.13`), `docs/V3.md` (§8.1.1), `README.md`, `tests/test_phase23_security.py` (yeni),
`tests/js/approvals_cut.test.js` (yeni), `tests/test_phase20_connector.py` + `tests/test_e2e_protocol.py` (sürüm 3.2.5).
