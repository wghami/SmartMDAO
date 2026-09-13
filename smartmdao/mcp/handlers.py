"""
Tool implementations, as plain functions.

Deliberately free of any MCP import: `server.py` is a thin registration layer
over these, so the behaviour can be tested directly instead of through a live
protocol session. Every one of them returns JSON-serialisable data.
"""
import logging
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Sequence

from ..analysis import analyze, explain, validate
from .loader import PipelineLoadError, load_pipeline
from .rendering import render_xdsm

logger = logging.getLogger(__name__)

#: Lists longer than this are truncated in tool responses. Analysis output is
#: normally small, but a generated pipeline with hundreds of steps should not
#: be able to flood the client's context window.
MAX_ITEMS = 200


def _truncate(items: Sequence[Any]) -> List[Any]:
    values = list(items)
    if len(values) <= MAX_ITEMS:
        return values
    hidden = len(values) - MAX_ITEMS
    return values[:MAX_ITEMS] + [f"... {hidden} more omitted"]


def _load(path: str, variable: Optional[str]):
    """Loads a pipeline, turning load failures into a structured error dict."""
    try:
        return load_pipeline(path, variable), None
    except PipelineLoadError as error:
        return None, {"ok": False, "error": str(error)}


def analyze_pipeline(
    path: str,
    variable: Optional[str] = None,
    inputs: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """What a solve would do: order, loops, seeds, recommended solver."""
    loaded, failure = _load(path, variable)
    if failure:
        return failure

    analysis = analyze(loaded.pipeline, inputs or ())

    return {
        "ok": True,
        "pipeline": loaded.variable,
        "source": loaded.source,
        "path": str(loaded.path),
        "steps": _truncate(analysis.steps),
        "execution_order": _truncate(analysis.execution_order),
        "cycles": [
            {
                "steps": list(cycle.steps),
                "feedback_variables": list(cycle.feedback_variables),
            }
            for cycle in analysis.cycles
        ],
        "external_inputs": _truncate(analysis.external_inputs),
        "terminal_outputs": _truncate(analysis.terminal_outputs),
        "initial_guesses_required": [
            asdict(guess) for guess in analysis.initial_guesses_required
        ],
        "recommended_solver": analysis.recommended_solver,
        "reason": analysis.reason,
    }


def validate_pipeline(
    path: str,
    variable: Optional[str] = None,
    inputs: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Everything statically wrong with the pipeline, worst first."""
    loaded, failure = _load(path, variable)
    if failure:
        return failure

    findings = validate(loaded.pipeline, inputs or ())
    counts: Dict[str, int] = {}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1

    return {
        "ok": True,
        "pipeline": loaded.variable,
        "source": loaded.source,
        "path": str(loaded.path),
        "valid": not any(finding.severity == "error" for finding in findings),
        "counts": counts,
        "findings": [asdict(finding) for finding in _truncate(findings)],
    }


def explain_pipeline(
    path: str,
    variable: Optional[str] = None,
    inputs: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """A prose description of the pipeline, for review or documentation."""
    loaded, failure = _load(path, variable)
    if failure:
        return failure

    return {
        "ok": True,
        "pipeline": loaded.variable,
        "source": loaded.source,
        "path": str(loaded.path),
        "explanation": explain(loaded.pipeline, inputs or ()),
    }


def render_pipeline_diagram(
    path: str,
    output_path: str,
    variable: Optional[str] = None,
    inputs: Optional[Sequence[str]] = None,
    orientation: str = "TB",
    graph_type: str = "flow",
) -> Dict[str, Any]:
    """Writes an XDSM diagram to disk and reports where it went."""
    loaded, failure = _load(path, variable)
    if failure:
        return failure

    try:
        destination = render_xdsm(
            loaded.pipeline,
            output_path,
            inputs=inputs or (),
            orientation=orientation,
            graph_type=graph_type,
        )
    except ValueError as error:
        return {"ok": False, "error": str(error)}

    return {
        "ok": True,
        "pipeline": loaded.variable,
        "source": loaded.source,
        "output_path": str(destination),
    }
