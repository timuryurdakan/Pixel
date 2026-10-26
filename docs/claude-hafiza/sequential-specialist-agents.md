---
name: sequential-specialist-agents
description: "User wants big builds split into phases, each handled by a specialist subagent, strictly one after another, tracked in PROJECT.md"
metadata:
  node_type: memory
  type: feedback
  originSessionId: d3c4193c-5d1e-4ef1-94ba-fa9fbb3a6d39
  modified: 2026-10-11T14:26:45.380Z
---

For larger projects the user asks Claude to split the work into phases and give each to a specialist subagent (architect, pixel artist, backend, frontend, QA…), running them strictly sequentially — never start the next before the previous finishes. Progress is tracked in a Turkish PROJECT.md (phase table + checklists + dated "İlerleme Günlüğü"), with a code-facing spec in docs/.

**Why:** User explicitly requested it for Pixel Office ("bir ajanın işi bitmeden diğeri başlamasın", "projenin md dosyasını da oluştur buna göre ilerle").

**How to apply:** Run Agent calls in the foreground one at a time; each agent reads PROJECT.md/spec first and updates PROJECT.md when done. User writes in Turkish, reply in Turkish. See [[pixel-office-project]].
