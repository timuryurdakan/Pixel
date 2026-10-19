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
| S16-01 | **High**: escapes the "edit = no shell" promise and leads to arbitrary code execution as the user | `pixel_connector/engines/claude.py` | The built-in deny rules only covered reading `~/.ssh` etc. Every profile that can edit (`edit`, `dev`, `auto`) could write files that become code later and are mostly invisible in `git diff`: `.git/hooks/*`, `.git/config` (`core.fsmonitor`, `core.hooksPath`), `.claude/settings*.json` (hooks), `.mcp.json` (MCP servers), `.vscode/` tasks, `.envrc`, shell start-up files, `~/.config` (autostart, systemd user units), `~/.local/bin` shims, and the Claude binary itself (`~/.local/share/claude`, `~/.vscode/extensions`). | A prompt injection in repo content or a web page (T10), or a compromised origin acting as a paired browser (T4), asks an `edit`-profile agent to write `.claude/settings.json` with a `PreToolUse` hook, or `.git/config` with `core.fsmonitor=<cmd>`. The next task, or the dev profile's allowed `git status`, runs the command. | **fixed** | `EXEC_SENSITIVE_DENY` (Edit rules in both `//**/` and `**/` form, plus `~/…`) and `SECRET_READ_DENY` (`~/.claude.json`, `~/.netrc`, `~/.git-credentials`, `~/.config/gh`, `~/.docker/config.json`, `~/.kube`) are always passed in `--disallowedTools`. Tests: `ClaudeExecSensitiveDeny`, `test_connector_claude`. Residual: edits to tracked build files inside the workspace (Makefile, package.json scripts) are the feature, so review them with git before running them. |
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
  task. `FORBIDDEN_ARGS` are never built. `child_env` strips `PIXEL_*`, `UPSTASH_*`, `KV_REST_*` and `RELAY_SECRET`.
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
   (`18a4148d7cc2362ea00c7225fe4996170f60738a753a1d6088c5040c979020d9`). Then run `pixel-connector doctor`. Do the same on
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
