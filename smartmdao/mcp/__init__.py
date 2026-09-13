"""
MCP connector for SmartMDAO - static analysis exposed to a coding agent.

Requires the optional extra::

    pip install smartmdao[mcp]

The tool handlers, loader and renderer have no MCP dependency and import
freely; only `create_server` and `main` need the SDK, so they are resolved
lazily through `__getattr__`. That keeps `import smartmdao.mcp.handlers`
working in a base install, and keeps the SDK's ~27 transitive packages out of
anyone's way until they actually run a server.

See docs/design/001-mcp-connector.md for why this server verifies rather than
authors.
"""
from .handlers import (
    analyze_pipeline,
    explain_pipeline,
    render_pipeline_diagram,
    validate_pipeline,
)
from .loader import LoadedPipeline, PipelineLoadError, load_pipeline
from .rendering import render_xdsm

__all__ = [
    "analyze_pipeline",
    "validate_pipeline",
    "explain_pipeline",
    "render_pipeline_diagram",
    "load_pipeline",
    "LoadedPipeline",
    "PipelineLoadError",
    "render_xdsm",
    "create_server",
    "main",
]


def __getattr__(name: str):
    """Defers importing the MCP SDK until a server is actually requested."""
    if name in ("create_server", "main"):
        from . import server

        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
