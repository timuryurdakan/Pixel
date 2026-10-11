"""All constants and thresholds (ARCHITECTURE.md §2.4)."""

from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
NAMES_FILE = Path(__file__).resolve().parent.parent / "names.json"  # {agent_id: name}, optional

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

NAMES = [
    "Ada", "Linus", "Grace", "Alan", "Barbara", "Ken", "Margaret", "Dennis",
    "Hedy", "Guido", "Radia", "Bjarne", "Frances", "Edsger", "Katherine", "Donald",
    "Anita", "Niklaus", "Joan", "Yukihiro", "Sophie", "Brian", "Lynn", "Larry",
]
