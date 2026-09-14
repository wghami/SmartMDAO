"""
The MCP protocol layer.

Deliberately thin. Everything with behaviour lives in `handlers.py`, which has
no MCP dependency and is tested directly; this module only registers those
functions, plus the resources and prompts that ground a client's own code
generation.

Requires the optional extra:  pip install smartmdao[mcp]
"""
import logging
from pathlib import Path
from typing import List, Optional

from . import handlers

logger = logging.getLogger(__name__)

_DOCS = Path(__file__).resolve().parents[2] / "docs"
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"

INSTRUCTIONS = """\
Static analysis for SmartMDAO pipelines.

Use these tools to check pipeline code you have written or been given. They
read signatures, type annotations and the dependency graph - no discipline
function is ever called, and no pipeline is ever run.

Recommended loop when authoring a pipeline:
  1. analyze_pipeline  - see the execution order, feedback loops, and which
                         variables need an initial guess
  2. validate_pipeline - get every structural problem at once
  3. fix, repeat
  4. render_pipeline_diagram - produce an XDSM for a human to look at

Note that analysis is solver-aware: IterativeSolver runs steps in registration
order and ignores the dependency graph, so its answers differ from
HybridSolver's for the same steps.
"""


def _version() -> str:
    """The installed package version, reported to the client in `serverInfo`."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("smartmdao")
    except PackageNotFoundError:        # pragma: no cover - running from a checkout
        return "0.0.0+unknown"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:                        # pragma: no cover
        return f"(unavailable: {error})"


def create_server(name: str = "smartmdao"):
    """
    Builds the MCP server.

    Imported lazily so that `import smartmdao` never pulls in the MCP SDK and
    its ~27 transitive dependencies.
    """
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as error:  # pragma: no cover - exercised via monkeypatch
        raise ImportError(
            "The MCP server requires the optional 'mcp' extra. "
            "Install it with: pip install smartmdao[mcp]"
        ) from error

    server = MCPServer(name=name, version=_version(), instructions=INSTRUCTIONS)

    # ----- Tools -------------------------------------------------------------

    @server.tool(
        description=(
            "Describe what running a SmartMDAO pipeline would do: execution "
            "order, detected feedback loops, which variables need an initial "
            "guess, and which solver to use. Executes nothing."
        )
    )
    def analyze_pipeline(
        path: str,
        variable: Optional[str] = None,
        inputs: Optional[List[str]] = None,
    ) -> dict:
        return handlers.analyze_pipeline(path, variable, inputs)

    @server.tool(
        description=(
            "Report every structural problem in a SmartMDAO pipeline at once: "
            "type mismatches, duplicate output names, missing inputs, "
            "unseeded feedback variables and solver misconfiguration."
        )
    )
    def validate_pipeline(
        path: str,
        variable: Optional[str] = None,
        inputs: Optional[List[str]] = None,
    ) -> dict:
        return handlers.validate_pipeline(path, variable, inputs)

    @server.tool(
        description=(
            "Describe an existing SmartMDAO pipeline in prose - useful for "
            "documentation, code review, or understanding unfamiliar code."
        )
    )
    def explain_pipeline(
        path: str,
        variable: Optional[str] = None,
        inputs: Optional[List[str]] = None,
    ) -> dict:
        return handlers.explain_pipeline(path, variable, inputs)

    @server.tool(
        description=(
            "Render an XDSM diagram of a SmartMDAO pipeline to a file. "
            "Format is inferred from the extension, defaulting to PDF."
        )
    )
    def render_pipeline_diagram(
        path: str,
        output_path: str,
        variable: Optional[str] = None,
        inputs: Optional[List[str]] = None,
        orientation: str = "TB",
        graph_type: str = "flow",
    ) -> dict:
        return handlers.render_pipeline_diagram(
            path, output_path, variable, inputs, orientation, graph_type
        )

    # ----- Resources ---------------------------------------------------------
    # Grounding for the client's own generation: current docs beat whatever
    # version of the API the model happens to have memorised.

    @server.resource(
        "smartmdao://docs/architecture",
        name="SmartMDAO architecture",
        description="How the library works internally: execution path, solvers, graph layer.",
        mime_type="text/markdown",
    )
    def architecture_doc() -> str:
        return _read(_DOCS / "architecture.md")

    @server.resource(
        "smartmdao://docs/known-issues",
        name="SmartMDAO known issues",
        description="Sharp edges and traps, each with a severity and a fix direction.",
        mime_type="text/markdown",
    )
    def known_issues_doc() -> str:
        return _read(_DOCS / "known-issues.md")

    @server.resource(
        "smartmdao://examples/sellar",
        name="Sellar example",
        description="The canonical coupled MDO problem, end to end.",
        mime_type="text/x-python",
    )
    def sellar_example() -> str:
        return _read(_SCRIPTS / "readme_quick_start.py")

    @server.resource(
        "smartmdao://examples/agent-as-discipline",
        name="Agent-as-discipline example",
        description="A model discipline inside and alongside an MDA feedback loop.",
        mime_type="text/x-python",
    )
    def agent_example() -> str:
        return _read(_SCRIPTS / "agent_as_discipline_demo.py")

    # ----- Prompts -----------------------------------------------------------
    # Generation stays in the client, where the capable model already is. What
    # the server contributes is grounding and a verification loop.

    @server.prompt(
        name="pipeline_from_prose",
        description="Turn a description of a multidisciplinary problem into a SmartMDAO pipeline.",
    )
    def pipeline_from_prose(description: str) -> str:
        return f"""\
Write a SmartMDAO pipeline for the following problem.

{description}

Follow these rules:
  - One `@pipeline.step(outputs=[...])` per discipline; plain Python functions.
  - Annotate every parameter and return type - static validation depends on it.
  - Use HybridSolver if any discipline consumes a value another produces
    downstream of it; that is a feedback loop and DAGSolver will raise.
  - Every feedback loop needs an initial guess passed to `run()`.
  - Give each output a distinct name; duplicates are silently overwritten.

Then verify before claiming it works:
  1. call `analyze_pipeline` to confirm the loops and execution order are what
     you intended, and to learn which variables need initial guesses
  2. call `validate_pipeline` and fix every error it reports
  3. only then present the code
"""

    @server.prompt(
        name="review_pipeline",
        description="Review an existing SmartMDAO pipeline for structural problems.",
    )
    def review_pipeline(path: str) -> str:
        return f"""\
Review the SmartMDAO pipeline in {path}.

  1. `explain_pipeline` - understand what it does
  2. `analyze_pipeline` - check the execution order and feedback loops match
     the intent, and that every cycle is properly seeded
  3. `validate_pipeline` - collect structural problems
  4. Read the `smartmdao://docs/known-issues` resource and check the code
     against the traps listed there, particularly duplicate output names and
     solver choice.

Report what is wrong and why it matters, not just what the tools printed.
"""

    return server


def main() -> None:  # pragma: no cover - process entry point
    """Console entry point: runs the server over stdio."""
    logging.basicConfig(level=logging.WARNING)
    create_server().run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
