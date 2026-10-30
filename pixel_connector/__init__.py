"""Pixel Office v3 connector (V3.md §5): pairs browsers, runs E2E-encrypted tasks on local engines (Claude Code,
Ollama, OpenAI-compatible servers), reports agents to the office. Stdlib only, Python ≥ 3.8. Entry point:
`python3 -m pixel_connector` or the zipapp web/connector/pixel-connector.pyz (tools/build_connector.py)."""

__version__ = "3.12.0"

# The build id is a short hash of the bundled sources, written into the zipapp as `pixel_connector/_build.py` by
# tools/build_connector.py (v2.md E1). That module is never committed: a source checkout reports "dev".
try:
    from ._build import BUILD as __build__
except ImportError:
    __build__ = "dev"


def version_string() -> str:
    return "%s (build %s)" % (__version__, __build__)
