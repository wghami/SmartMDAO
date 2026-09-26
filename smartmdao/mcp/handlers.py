"""
Tool implementations, as plain functions.

Deliberately free of any MCP import: `server.py` is a thin registration layer
over these, so the behaviour can be tested directly instead of through a live
protocol session. Every one of them returns JSON-serialisable data.
"""
import logging
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Sequence

from ..analysis import analyze, explain, stub_status, validate
from ..discretisation import effective_steps
from ._runner import DEFAULT_BUDGET_SWEEPS, SMOKE
from .loader import _UNRESOLVED
from .comparison import DEFAULT_ATOL, DEFAULT_RTOL, compare_runs as _compare_runs
from .execution import DEFAULT_TIMEOUT_SECONDS, run_in_subprocess
from .loader import PipelineLoadError, input_map_in_source, load_pipeline
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

    The base list is the caller's when it passes one, otherwise the pipeline's
    own declaration (`Pipeline(inputs=[...])`) - an explicit argument wins, as
    it does in the library. The file's own `run()` call is evidence on top of
    either, so it is unioned in. Reporting the split matters: an agent that
    guessed the design variables and forgot the cycle's seed used to be told the
    seed was missing - a working file reported as broken.
    """
    asked = tuple(requested or ())
    declared = () if asked else tuple(loaded.pipeline.inputs)
    base = asked or declared
    in_source = tuple(name for name in loaded.inputs_in_source if name not in set(base))
    return tuple(sorted(set(base) | set(in_source))), {
        "requested": list(asked),
        "declared": list(declared),
        "found_in_source": list(in_source),
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
        "stubs": _truncate(analysis.stubs),
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
) -> Dict[str, Any]:
    """Writes an XDSM diagram to disk and reports where it went."""
    loaded, failure = _load(path, variable)
    if failure:
        return failure

    effective, provenance = _effective_inputs(loaded, inputs)

    # No try/except here on purpose: the only ValueErrors render_xdsm raised
    # were validating `orientation` and `graph_type`, and both were removed in
    # 1.21.0. The 100% coverage rule caught the handler still guarding against
    # them - a branch nothing could reach.
    destination = render_xdsm(loaded.pipeline, output_path, inputs=effective)

    return {
        "ok": True,
        "pipeline": loaded.variable,
        "source": loaded.source,
        "inputs_used": provenance,
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
        for name, value in input_map_in_source(loaded.path).items()
        if name not in supplied and value is not _UNRESOLVED
    }
    unresolved = [
        name
        for name, value in input_map_in_source(loaded.path).items()
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
    # Declared names are only names: they say what must be supplied, not what
    # the value is. Naming the ones nobody supplied turns a failed run's
    # traceback into a list the caller can act on before retrying.
    not_supplied = [
        name for name in loaded.pipeline.inputs
        if name not in supplied and name not in from_source and name not in unresolved
    ]
    if not_supplied:
        result["inputs_used"]["declared_not_supplied"] = not_supplied

    # Named, not refused. A stub stops a run within its first sweep whatever the
    # rung, so the most a run can waste is one partial sweep - while refusing
    # would block smoke-testing the steps already written upstream of it, which
    # is how a contract-first pipeline gets built. The executor reports the
    # failure as a RuntimeError, so without this the cause is only in stderr.
    stubs = [step.name for step in effective_steps(loaded.pipeline) if stub_status(step)]
    if stubs:
        result["stubs"] = _truncate(stubs)
        result["stubs_note"] = (
            f"{len(stubs)} step(s) only raise NotImplementedError. A run stops at "
            f"the first one it reaches, within the first sweep."
        )
    if unresolved:
        result["inputs_used"]["unresolved_in_source"] = sorted(unresolved)
        result["inputs_used"]["note"] = (
            "These are passed to run() in the file but computed rather than "
            "literal, so their values could not be read. Supply them yourself "
            "if the run failed for want of them."
        )
    return result


def compare_runs(
    path_a: str,
    path_b: str,
    inputs: Optional[Dict[str, Any]] = None,
    variable_a: Optional[str] = None,
    variable_b: Optional[str] = None,
    rung: str = "full",
    budget_sweeps: int = DEFAULT_BUDGET_SWEEPS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    allow_effects: bool = False,
) -> Dict[str, Any]:
    """
    Runs two pipelines on the same inputs and reports where they disagree.

    Built for translation. A converted pipeline that quietly returns a different
    number is worse than no conversion, because it looks cleaner and gets
    trusted. Neither the agent nor static analysis can catch that; only running
    both and comparing can.
    """
    return _compare_runs(
        path_a=path_a,
        path_b=path_b,
        inputs=inputs,
        variable_a=variable_a,
        variable_b=variable_b,
        rung=rung,
        budget_sweeps=budget_sweeps,
        timeout_seconds=timeout_seconds,
        allow_effects=allow_effects,
    )
