"""POST /api/relay_ticket?room= — short-lived realtime relay ticket, OPTIONS for CORS (V3.md §6.2, §13.4).

Thin Vercel entry point; all logic lives in pixel_cloud (shared by every api/*.py file)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pixel_cloud.vercel import ApiHandler  # noqa: E402


class handler(ApiHandler):
    """Vercel's entrypoint detection needs a literal top-level `class handler` (a BaseHTTPRequestHandler
    subclass); an assignment such as `handler = make_handler()` is not recognised and the file is skipped."""
