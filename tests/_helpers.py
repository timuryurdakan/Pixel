"""Shared test helpers: makes the project importable and builds synthetic transcript lines."""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Keep expected warnings (malformed lines etc.) out of the test output.
logging.getLogger("pixel_office").setLevel(logging.CRITICAL)

T0 = 1_800_000_000.0   # fixed fake epoch for tests


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def user_prompt(t: float, text: str = "Fix the bug please", **kw) -> dict:
    return {"type": "user", "timestamp": iso(t), "cwd": "/home/x/proj", "sessionId": "s",
            "message": {"role": "user", "content": text}, **kw}


def user_text_block(t: float, text: str) -> dict:
    return {"type": "user", "timestamp": iso(t), "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def tool_use(t: float, tid: str, name: str, inp: dict | None = None, stop: str | None = "tool_use") -> dict:
    return {"type": "assistant", "timestamp": iso(t),
            "message": {"role": "assistant", "model": "claude-opus-5-5", "stop_reason": stop,
                        "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp or {}}]}}


def tool_result(t: float, tid: str) -> dict:
    return {"type": "user", "timestamp": iso(t),
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}}


def assistant_text(t: float, text: str, stop: str | None = None) -> dict:
    return {"type": "assistant", "timestamp": iso(t),
            "message": {"role": "assistant", "model": "claude-opus-5-5", "stop_reason": stop,
                        "content": [{"type": "text", "text": text}]}}


def dumps(*objs: dict) -> str:
    return "".join(json.dumps(o) + "\n" for o in objs)
