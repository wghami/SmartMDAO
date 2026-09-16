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
from ._runner import DEFAULT_BUDGET_SWEEPS, SMOKE
from .loader import _UNRESOLVED
from .execution import DEFAULT_TIMEOUT_SECONDS, run_in_subprocess
from .loader import PipelineLoadError, declared_input_map, load_pipeline
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


def _effective_inputs(loaded, requested: Optional[Sequence[str]]):
    """
    What the pipeline will actually receive, and where each name came from.

    The caller's list is intent; the file's own `run()` call is evidence. Both
    count, so they are unioned. Reporting the split matters: an agent that
    guessed the design variables and forgot the cycle's seed used to be told the
    seed was missing - a working file reported as broken.
    """
    asked = tuple(requested or ())
    inferred = tuple(
        name for name in loaded.declared_inputs if name not in set(asked)
    )
    return tuple(sorted(set(asked) | set(inferred))), {
        "requested": list(asked),
        "found_in_source": list(inferred),
    }


def analyze_pipeline(
    path: str,
    variable: Optional[str] = None,
    inputs: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """What a solve would do: order, loops, seeds, recommended solver."""
    loaded, failure = _load(path, variable)
    if failure:
        return failure

    effective, provenance = _effective_inputs(loaded, inputs)
    analysis = analyze(loaded.pipeline, effective)

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
        "inputs_used": provenance,
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

    effective, provenance = _effective_inputs(loaded, inputs)
    findings = validate(loaded.pipeline, effective)
    counts: Dict[str, int] = {}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1

    return {
        "ok": True,
        "pipeline": loaded.variable,
        "source": loaded.source,
        "path": str(loaded.path),
        "inputs_used": provenance,
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

    effective, provenance = _effective_inputs(loaded, inputs)

    return {
        "ok": True,
        "pipeline": loaded.variable,
        "source": loaded.source,
        "path": str(loaded.path),
        "inputs_used": provenance,
        "explanation": explain(loaded.pipeline, effective),
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


def run_pipeline(
    path: str,
    inputs: Optional[Dict[str, Any]] = None,
    variable: Optional[str] = None,
    rung: str = SMOKE,
    budget_sweeps: int = DEFAULT_BUDGET_SWEEPS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """
    Executes a pipeline in a child process, under a wall clock.

    `rung` defaults to `smoke` deliberately. An unbounded run is not a
    reasonable thing to ask someone to consent to blindly, so the expensive
    path has to be chosen explicitly - and the cheap one measures the unit cost
    so the choice can be made with a number rather than a guess.

    Inputs the caller does not supply are filled in from the file's own `run()`
    call where they can be read statically, so a pipeline that works when you
    run the script also works here.
    """
    loaded, failure = _load(path, variable)
    if failure:
        return failure

    supplied = dict(inputs or {})

    # Names alone are not enough to *run* a pipeline, so the literal values in
    # the file's own run() call are recovered too. The caller always wins.
    from_source = {
        name: value
        for name, value in declared_input_map(loaded.path).items()
        if name not in supplied and value is not _UNRESOLVED
    }
    unresolved = [
        name
        for name, value in declared_input_map(loaded.path).items()
        if name not in supplied and value is _UNRESOLVED
    ]

    result = run_in_subprocess(
        path=str(loaded.path),
        inputs={**from_source, **supplied},
        variable=variable,
        rung=rung,
        budget_sweeps=budget_sweeps,
        timeout_seconds=timeout_seconds,
    )

    result.setdefault("pipeline", loaded.variable)
    result.setdefault("source", loaded.source)
    result["path"] = str(loaded.path)
    result["inputs_used"] = {
        "supplied": sorted(supplied),
        "found_in_source": sorted(from_source),
    }
    if unresolved:
        result["inputs_used"]["unresolved_in_source"] = sorted(unresolved)
        result["inputs_used"]["note"] = (
            "These are passed to run() in the file but computed rather than "
            "literal, so their values could not be read. Supply them yourself "
            "if the run failed for want of them."
        )
    return result
