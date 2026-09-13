import asyncio
import json
import logging
import tempfile
from pathlib import Path

from smartmdao import configure_logging
from smartmdao.mcp import (
    analyze_pipeline,
    explain_pipeline,
    load_pipeline,
    render_pipeline_diagram,
    validate_pipeline,
)

logger = logging.getLogger(__name__)

# ==============================================================================
# WHY THIS DEMO EXISTS
# ==============================================================================
# The MCP connector lets a coding agent inspect SmartMDAO pipelines it has
# written, instead of guessing. This script exercises it WITHOUT an MCP client,
# so you can see exactly what an agent would see.
#
# The design choice worth understanding: the server VERIFIES, it does not
# AUTHOR. A coding agent is already a capable language model - it can write
# `@pipeline.step` code from the README. What it cannot do is notice that the
# code it just wrote closed a feedback loop, or that two steps claim the same
# output name. That is what these tools are for.
#
# Nothing here executes a discipline. The tools import the module that defines
# the pipeline - which does run module-level code - but they never call a step.
# ==============================================================================

# A pipeline written to a file, because that is how an agent would hand one
# over: a path on disk, not an object in memory.
AGENT_WRITTEN_PIPELINE = '''\
"""A wing sizing model - as a coding agent might first draft it."""
from smartmdao import Pipeline, HybridSolver

pipeline = Pipeline(solver=HybridSolver())


@pipeline.step(outputs=["lift"])
def compute_lift(span: float, chord: float, speed: float) -> float:
    return 0.5 * 1.225 * (speed ** 2) * (span * chord) * 1.2


@pipeline.step(outputs=["structural_mass"])
def size_structure(required_lift: float, span: float) -> float:
    return 0.08 * required_lift * span / 9.81


@pipeline.step(outputs=["total_mass"])
def total_mass(structural_mass: float, payload: float) -> float:
    return structural_mass + payload


@pipeline.step(outputs=["required_lift"])
def required_lift(total_mass: float) -> float:
    return total_mass * 9.81


@pipeline.step(outputs=["residual"])
def lift_balance(lift: float, required_lift: float) -> float:
    return lift - required_lift
'''


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def run_mcp_connector_demo():
    configure_logging(level=logging.WARNING)

    workspace = Path(tempfile.mkdtemp(prefix="smartmdao_mcp_demo_"))
    model = workspace / "wing_model.py"
    model.write_text(AGENT_WRITTEN_PIPELINE)

    print("An agent has just written a pipeline to:")
    print(f"  {model}")
    print("It has not run it, and cannot see its structure. So it asks.")

    # ==========================================================================
    # 1. Loading
    # ==========================================================================
    section("load_pipeline - find the Pipeline object in a file")
    loaded = load_pipeline(model)
    print(f"  found variable : {loaded.variable!r}")
    print(f"  steps          : {len(loaded.pipeline.steps)}")
    print("  -> Ambiguity is reported, never guessed at: a file with two")
    print("     pipelines and no `variable` argument is an error listing both.")

    # ==========================================================================
    # 2. analyze_pipeline
    # ==========================================================================
    section("analyze_pipeline - what would happen if this ran")
    result = analyze_pipeline(
        str(model), inputs=["span", "chord", "speed", "payload"]
    )
    print(f"  execution order : {' -> '.join(result['execution_order'])}")
    print(f"  solver          : {result['recommended_solver']}")
    for cycle in result["cycles"]:
        print(f"  feedback loop   : {' -> '.join(cycle['steps'])}")
        print(f"                    coupling on {', '.join(cycle['feedback_variables'])}")
    for guess in result["initial_guesses_required"]:
        print(f"  NEEDS A GUESS   : {guess['variable']} (read by {guess['consumed_by']})")
    print()
    print("  -> Five plausible-looking disciplines, and three of them close a")
    print("     mass-growth loop: a heavier wing needs more lift, more lift")
    print("     needs more structure, more structure is heavier. Nothing in the")
    print("     source says so - you have to hold the whole graph in your head")
    print("     to see it. This is the class of mistake the tools exist for.")

    # ==========================================================================
    # 3. validate_pipeline
    # ==========================================================================
    section("validate_pipeline - the fix list")
    report = validate_pipeline(
        str(model), inputs=["span", "chord", "speed", "payload"]
    )
    print(f"  valid  : {report['valid']}")
    print(f"  counts : {report['counts']}")
    for finding in report["findings"]:
        print(f"    [{finding['severity']}] {finding['code']}: {finding['message'][:88]}...")

    print("\n  ...and once the agent declares the initial guess it was told about:")
    fixed = validate_pipeline(
        str(model),
        inputs=["span", "chord", "speed", "payload", "total_mass"],
    )
    print(f"  valid  : {fixed['valid']}   findings: {len(fixed['findings'])}")

    # ==========================================================================
    # 4. Errors are data
    # ==========================================================================
    section("errors come back as data, not exceptions")
    broken = analyze_pipeline(str(workspace / "does_not_exist.py"))
    print(f"  ok    : {broken['ok']}")
    print(f"  error : {broken['error']}")
    print("  -> A missing file is something the agent should read and act on,")
    print("     not an exception that aborts its turn.")

    # ==========================================================================
    # 5. explain_pipeline
    # ==========================================================================
    section("explain_pipeline - the same facts, as prose")
    explanation = explain_pipeline(
        str(model),
        inputs=["span", "chord", "speed", "payload", "total_mass"],
    )["explanation"]
    for line in explanation.splitlines()[:12]:
        print(f"  {line}")
    print("  ...")

    # ==========================================================================
    # 6. render_pipeline_diagram
    # ==========================================================================
    section("render_pipeline_diagram - headless XDSM")
    diagram = render_pipeline_diagram(
        str(model),
        str(Path("results") / "mcp_connector_demo.png"),
        inputs=["span", "chord", "speed", "payload", "total_mass"],
    )
    print(f"  wrote: {diagram['output_path']}")
    print("  -> Agg is forced and view=False is passed, so this never tries to")
    print("     open a window - which would hang a server with no display.")

    # ==========================================================================
    # 7. The protocol surface
    # ==========================================================================
    section("what an MCP client actually sees")
    try:
        from smartmdao.mcp import create_server
    except ImportError:
        print("  (install the extra to see this: pip install smartmdao[mcp])")
        return

    server = create_server()

    async def collect():
        return (
            await server.list_tools(),
            await server.list_resources(),
            await server.list_prompts(),
        )

    tools, resources, prompts = asyncio.run(collect())

    print("  tools:")
    for tool in tools:
        print(f"    - {tool.name}")
    print("  resources (grounding, so the agent reads current docs not memory):")
    for resource in resources:
        print(f"    - {resource.uri}")
    print("  prompts (generation stays client-side; these route it through verify):")
    for prompt in prompts:
        print(f"    - {prompt.name}")

    section("a real round trip through the protocol")

    async def call():
        return await server.call_tool(
            "analyze_pipeline",
            {"path": str(model), "inputs": ["span", "chord", "speed", "payload"]},
        )

    response = asyncio.run(call())
    payload = json.loads(response.content[0].text)
    print(f"  is_error           : {response.is_error}")
    print(f"  recommended_solver : {payload['recommended_solver']}")
    print(f"  cycles found       : {len(payload['cycles'])}")

    print("\n  Run the server for real with:  smartmdao-mcp")


if __name__ == "__main__":
    run_mcp_connector_demo()
