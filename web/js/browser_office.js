// Pixel Office — the browser device's characters in this office (E4, docs/FAZ3_TASARIM.md D6). They never go to the
// room: main.js keeps them in its own map and merges them into every room snapshot.

const OFFICE_STATES = new Set(['working', 'thinking', 'waiting_for_user', 'idle', 'sleeping']);
const LOCAL_ID_RE = /^browser-[A-Za-z0-9_-]{1,24}$/;

/** browser_device.js office record → v1 Agent JSON for this office (unknown states → idle). */
export function toLocalAgent(raw, prev = null, now = Date.now()) {
  if (!raw || typeof raw.id !== 'string' || !LOCAL_ID_RE.test(raw.id)) return null;
  const state = OFFICE_STATES.has(raw.state) ? raw.state : 'idle';
  const name = typeof raw.name === 'string' && raw.name.trim() ? raw.name.trim().slice(0, 40) : 'Tarayıcı ajanı';
  let seed = 0;
  for (const ch of raw.id) seed = (seed * 31 + ch.charCodeAt(0)) >>> 0;
  return {
    id: raw.id, name, project: null, projectPath: null, title: null, state,
    tool: typeof raw.tool === 'string' ? raw.tool.slice(0, 24) : null, detail: null, lastMessage: null,
    lastPrompt: null, isSubagent: false, parentId: null, agentType: null, model: null, colorSeed: seed % 1000,
    toolCount: 0, startedAt: prev ? prev.startedAt : now, stateSince: prev && prev.state === state ? prev.stateSince : now,
    updatedAt: now, nameSource: 'auto', defaultName: name, owner: 'Bu tarayıcı', source: 'browser',
    control: raw.control && typeof raw.control === 'object' ? raw.control : null,
  };
}
