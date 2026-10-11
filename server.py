#!/usr/bin/env python3
"""Pixel Office — entry point.

    python3 server.py                 # watch real Claude Code sessions (read-only)
    python3 server.py --demo          # fake agents
    python3 server.py --port 9000 --projects-dir ~/.claude/projects
    python3 server.py --names-file /tmp/names.json        # where UI renames are kept (CLOUD.md §15.4)
    python3 server.py --host 0.0.0.0 --write-key SECRET   # status/rename API needs the key off loopback (§16)
"""

import argparse
import logging
import secrets
import signal
import sys
from pathlib import Path

from pixel_office import __version__
from pixel_office.config import DEFAULT_HOST, DEFAULT_PORT, DEFAULT_PROJECTS_DIR, POLL_INTERVAL, WEB_ROOT
from pixel_office.httpd import make_server
from pixel_office.names_store import Namer, NamesStore, default_names_path
from pixel_office.reported import ReportedAgents
from pixel_office.simulator import DemoSimulator
from pixel_office.store import AgentStore
from pixel_office.watcher import Watcher

log = logging.getLogger("pixel_office")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="server.py", description="Pixel Office: your Claude Code agents as pixel-art office workers.")
    p.add_argument("--demo", action="store_true", help="run with simulated agents instead of real transcripts")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"HTTP port (default {DEFAULT_PORT})")
    p.add_argument("--host", default=DEFAULT_HOST, help=f"bind address (default {DEFAULT_HOST})")
    p.add_argument("--projects-dir", type=Path, default=DEFAULT_PROJECTS_DIR,
                   help="Claude Code projects dir (default ~/.claude/projects)")
    p.add_argument("--poll", type=float, default=POLL_INTERVAL, help=f"poll interval in seconds (default {POLL_INTERVAL})")
    p.add_argument("--names-file", type=Path, default=None,
                   help=f"UI rename storage (default {default_names_path()}); created on the first rename")
    p.add_argument("--write-key", default=None,
                   help="Bearer key for POST /api/status and /api/agents/rename (required, and generated if "
                        "missing, when --host is not loopback)")
    p.add_argument("-v", "--verbose", action="store_true", help="debug/info logging")
    p.add_argument("--version", action="version", version=f"Pixel Office {__version__}")
    args = p.parse_args(argv)
    args.projects_dir = Path(args.projects_dir).expanduser()
    if args.poll <= 0:
        p.error("--poll must be > 0")
    if args.write_key is not None and (len(args.write_key) < 8 or not args.write_key.isprintable()
                                       or any(c.isspace() for c in args.write_key)):
        p.error("--write-key must be at least 8 printable characters without spaces")
    return args


def is_loopback(host: str) -> bool:
    return host in ("localhost", "::1") or host.startswith("127.")


def _sigterm(signum, frame) -> None:  # noqa: ANN001
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")

    mode = "demo" if args.demo else "live"
    names = NamesStore(args.names_file)
    store = AgentStore(mode, namer=Namer(names))
    reported = ReportedAgents(store)
    write_key = args.write_key
    generated_key = False
    if write_key is None and not is_loopback(args.host):
        write_key = secrets.token_urlsafe(24)
        generated_key = True
    try:
        srv = make_server(args.host, args.port, store, WEB_ROOT, reported=reported, names=names,
                          write_key=write_key)
    except OSError as e:
        print(f"Cannot listen on {args.host}:{args.port}: {e}", file=sys.stderr)
        return 1

    producer = DemoSimulator(store) if args.demo else Watcher(args.projects_dir, store, args.poll)
    producer.start()
    reported.start()

    shown_host = "localhost" if args.host in ("127.0.0.1", "0.0.0.0", "::", "") else args.host
    port = srv.server_address[1]
    print(f"Pixel Office running at http://{shown_host}:{port}  (mode: {mode})", flush=True)
    if not args.demo:
        print(f"Watching {args.projects_dir} (read-only). Press Ctrl+C to stop.", flush=True)
    else:
        print("Press Ctrl+C to stop.", flush=True)
    if generated_key:
        print(f"Status API key (not on loopback): Authorization: Bearer {write_key}", flush=True)

    try:
        # Ctrl+C must work even if SIGINT was inherited as ignored (e.g. started with `&`).
        signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.signal(signal.SIGTERM, _sigterm)
    except ValueError:
        pass  # not in main thread (e.g. tests)

    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nShutting down…", flush=True)
    finally:
        srv.shutting_down.set()
        producer.stop()
        reported.stop()
        srv.shutdown()
        srv.server_close()
        producer.join(timeout=2)
        reported.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
