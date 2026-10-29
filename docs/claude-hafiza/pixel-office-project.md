---
name: pixel-office-project
description: "Pixel Office state — v1–v4 done; Patron Odası P1–P5 done 2026-10-29 on branch patron-odasi, P6 next"
metadata:
  node_type: memory
  type: project
  originSessionId: d3c4193c-5d1e-4ef1-94ba-fa9fbb3a6d39
  modified: 2026-10-11T14:26:49.558Z
---

Pixel Office (~/Desktop/Pixel): pixel-art office where AI agents type/drink coffee/sleep. v1 (local Python server reading ~/.claude/projects) finished 2026-10-11.

v2 goal (started 2026-10-11): deploy on Vercel so friends can join shared rooms via link, install as PWA with an always-on-top mini window (bottom-right), connect Claude Code via hooks AND local AI agents (Ollama, LM Studio, aider, custom scripts), rename agents from the UI. User chose: shared cloud office (Upstash Redis) + PWA/Document PiP over Electron. Privacy promise: prompts/file contents never leave the machine.

**Why:** User wants to share it with friends via GitHub/Vercel.

**How to apply:** Keep local mode working; no Node/git on the dev machine (as of 2026-10-11), deployment requires user's own GitHub/Vercel accounts. See [[sequential-specialist-agents]].

Update 2026-10-19: v3 (connector, relay on Cloudflare `pixel-relay.yurdakantimur.workers.dev`, domain www.pixeloffice.com.tr) code done; Aşama 16 security audit done. User also uses Antigravity IDE: coordination via `docs/GOREV_PANOSU.md`; Antigravity works in worktree `~/Desktop/Pixel-antigravity` (branch `antigravity/gorevler`), Claude on `main` and does merges; user pushes. User wants to be asked before Aşama 17 starts.

Update 2026-10-26: v4 A8–A10 (Task Board, handoff, workflow engine) done and pushed; connector 3.4.1, web po-v4.0.1; S26 v4 security review done. User authorized Claude to push and to update this machine's connector ("ne istiyorsan yap"). Relay `wrangler` pinned 4.145.0 with lockfile + npm overrides (undici 7.30.1, sharp 0.35.5) — S26-01 fixed; drop overrides when wrangler is upgraded. S26-04 and S26-08 fixed in 3.5.0. Relay moved to wss://relay.pixeloffice.com.tr.

User has only ONE computer (this Linux machine, 2026-10-26) — don't tell them to update connectors on "other computers".

Update 2026-10-26 (later): connector 3.5.0 adds `pixel-connector update` (user-run, verifies SHA256SUMS incl. hook files) and an office banner when the paired connector is outdated; web po-v4.0.2. To update this machine after a release: `pixel-connector update`. No browser-triggered/auto update by design.

Update 2026-10-29: machine was reformatted 2026-10-27; old chat transcripts are gone (state lives in PROJECT.md §6, docs/gunluk/2026-10.md, docs/GOREV_PANOSU.md). Patron Odası (docs/PATRON_ODASI.md) P1–P5 done on branch `patron-odasi` (P5 commit bd88461, web po-v4.0.4, connector 3.8.0); next is P6 (Boss panel + Task Board), then P7–P9. After the format no Chrome and no git identity: browser tests run by putting a `chromium` symlink to /opt/brave.com/brave/brave first on PATH (scratchpad), commits use `git -c user.name=timuryurdakan -c user.email=…` until the user sets it.
