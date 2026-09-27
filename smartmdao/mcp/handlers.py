"""
Tool implementations, as plain functions.

Deliberately free of any MCP import: `server.py` is a thin registration layer
over these, so the behaviour can be tested directly instead of through a live
protocol session. Every one of them returns JSON-serialisable data.
"""
import logging
from dataclasses import asdict, replace
from typing import Any, Dict, List, Optional, Sequence

from ..analysis import analyze, explain, stub_status, unit_connections, validate
from ..discretisation import effective_steps
from .protocol import DEFAULT_BUDGET_SWEEPS, SMOKE
from .loader import _UNRESOLVED
from .comparison import DEFAULT_ATOL, DEFAULT_RTOL, _summary, diff_states
from .protocol import FULL, RUNGS
from .execution import DEFAULT_TIMEOUT_SECONDS, run_in_subprocess
from .loader import PipelineLoadError, input_map_in_source, load_pipeline
from .rendering import render_xdsm
from .boundary import whole_number_floats
from . import worker
from .environment import resolve

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


def _unit_coverage(pipeline) -> Dict[str, Any]:
    """How many connections are unit-checked, and which are not (F-013)."""
    connections = unit_connections(effective_steps(pipeline))
    unchecked = [c for c in connections if c["missing"]]
    return {
        "checked": len(connections) - len(unchecked),
        "total": len(connections),
        "unchecked": _truncate(unchecked),
    }


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


def _analyze(
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
        "unit_coverage": _unit_coverage(loaded.pipeline),
        "recommended_solver": analysis.recommended_solver,
        "reason": analysis.reason,
    }


def _validate(
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


def _explain(
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


def _render(
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

    result = {
        "ok": True,
        "pipeline": loaded.variable,
        "source": loaded.source,
        "inputs_used": provenance,
        "output_path": str(destination),
    }
    # A split group is visible in the picture, but the reason is not - and the
    # caller may never look at the picture.
    # Always a list, empty when nothing was split, so a client can read it
    # without first asking whether it is there (F-012).
    result["group_notes"] = [
        f.message for f in validate(loaded.pipeline, effective) if f.code == "groups-interleaved"
    ]
    return result


def _run(
    path: str,
    inputs: Optional[Dict[str, Any]] = None,
    variable: Optional[str] = None,
    rung: str = SMOKE,
    budget_sweeps: int = DEFAULT_BUDGET_SWEEPS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    inputs_from: Optional[str] = None,
    inputs_args: Optional[Sequence[Any]] = None,
    inputs_kwargs: Optional[Dict[str, Any]] = None,
    targets: Optional[Sequence[str]] = None,
    use_source_literals: bool = True,
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

    if targets:
        # Only what those outputs need (F-019); everything below, and the run
        # itself, sees the smaller pipeline.
        try:
            loaded = replace(loaded, pipeline=loaded.pipeline.upstream(*targets))
        except ValueError as error:
            return {"ok": False, "error": str(error)}

    supplied, coerced, refusal = whole_number_floats([loaded.pipeline], dict(inputs or {}))
    if refusal:
        return {"ok": False, "refused": "json-boundary", "error": refusal}

    # Names alone are not enough to *run* a pipeline, so the literal values in
    # the file's own run() call are recovered too. The caller always wins.
    # compare_runs passes use_source_literals=False: it hands both sides the
    # same inputs, and each side's own literals would make them differ.
    in_source = input_map_in_source(loaded.path) if use_source_literals else {}
    from_source = {
        name: value
        for name, value in in_source.items()
        if name not in supplied and value is not _UNRESOLVED
    }
    unresolved = [
        name
        for name, value in in_source.items()
        if name not in supplied and value is _UNRESOLVED
    ]

    result = run_in_subprocess(
        path=str(loaded.path),
        inputs=supplied,
        fallback_inputs=from_source,
        inputs_from=inputs_from,
        inputs_args=inputs_args,
        inputs_kwargs=inputs_kwargs,
        targets=targets,
        variable=variable,
        rung=rung,
        budget_sweeps=budget_sweeps,
        timeout_seconds=timeout_seconds,
    )
    built = [name for name in result.pop("built_inputs", []) if name not in supplied]

    result.setdefault("pipeline", loaded.variable)
    result.setdefault("source", loaded.source)
    result["path"] = str(loaded.path)
    result["inputs_used"] = {
        "supplied": sorted(supplied),
        "found_in_source": sorted(name for name in from_source if name not in built),
    }
    if inputs_from:
        result["inputs_used"]["built_by_project"] = built
    if coerced:
        # JSON cannot tell 2.0 from 2 (docs/design/009). Said, not done silently.
        result["inputs_used"]["coerced"] = coerced
    # Declared names are only names: they say what must be supplied, not what
    # the value is. Naming the ones nobody supplied turns a failed run's
    # traceback into a list the caller can act on before retrying.
    not_supplied = [
        name for name in loaded.pipeline.inputs
        if name not in supplied and name not in from_source and name not in unresolved
        and name not in built
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



# ==============================================================================
# The tools: resolve the environment, then run the op here or in its worker
# ==============================================================================

#: What the worker can be asked to do. The worker calls these same functions,
#: so a project's environment and the server's run one implementation.
def _effects(path: str, variable: Optional[str] = None, because: str = "") -> Dict[str, Any]:
    """
    Loads a file and reports the steps that declare side effects - in the file's
    own environment, without running anything. What compare_runs asks of both
    files before it runs either.
    """
    from ..effects import effects_refusal_message

    loaded, failure = _load(path, variable)
    if failure:
        return failure
    declared = [step for step in effective_steps(loaded.pipeline) if step.has_effects]
    return {
        "ok": True,
        "declared": [step.name for step in declared],
        "refusal": effects_refusal_message(declared, because) if declared else None,
    }


LOCAL_OPS = {
    "effects": _effects,
    "analyze": _analyze,
    "validate": _validate,
    "explain": _explain,
    "render": _render,
    "run": _run,
}


def _dispatch(op: str, path: str, python: Optional[str], project: Optional[str],
              timeout: float, **args) -> Dict[str, Any]:
    """
    Runs `op` for `path` in the environment it belongs to, and says which.

    In-process when that is the server's own environment - a fresh interpreter
    costs 0.8-0.9 s, against 7 ms for an analysis here (docs/design/007) - and
    in a worker process otherwise.
    """
    interpreter, refusal = resolve(path, python, project)
    if refusal:
        return refusal

    if interpreter.is_server:
        result = LOCAL_OPS[op](path=path, **args)
    else:
        # Only arguments actually given: a project pinned to an older SmartMDAO
        # has an older handler, and a None it has never heard of would break
        # a call it can otherwise answer.
        given = {key: value for key, value in args.items() if value is not None}
        result, answered_by = worker.call(interpreter, op, {"path": path, **given}, timeout)
        if answered_by:
            interpreter = replace(interpreter, smartmdao=answered_by)

    result["interpreter"] = interpreter.describe()
    return result


def analyze_pipeline(path: str, variable: Optional[str] = None,
                     inputs: Optional[Sequence[str]] = None,
                     python: Optional[str] = None, project: Optional[str] = None) -> Dict[str, Any]:
    """What a solve would do: order, loops, seeds, recommended solver, stubs."""
    return _dispatch("analyze", path, python, project, worker.ANALYSIS_TIMEOUT_SECONDS,
                     variable=variable, inputs=inputs)


def validate_pipeline(path: str, variable: Optional[str] = None,
                      inputs: Optional[Sequence[str]] = None,
                      python: Optional[str] = None, project: Optional[str] = None) -> Dict[str, Any]:
    """Everything statically wrong with the pipeline, worst first."""
    return _dispatch("validate", path, python, project, worker.ANALYSIS_TIMEOUT_SECONDS,
                     variable=variable, inputs=inputs)


def explain_pipeline(path: str, variable: Optional[str] = None,
                     inputs: Optional[Sequence[str]] = None,
                     python: Optional[str] = None, project: Optional[str] = None) -> Dict[str, Any]:
    """A prose description of the pipeline, for review or documentation."""
    return _dispatch("explain", path, python, project, worker.ANALYSIS_TIMEOUT_SECONDS,
                     variable=variable, inputs=inputs)


def render_pipeline_diagram(path: str, output_path: str, variable: Optional[str] = None,
                            inputs: Optional[Sequence[str]] = None,
                            python: Optional[str] = None, project: Optional[str] = None) -> Dict[str, Any]:
    """Writes an XDSM diagram to disk and reports where it went."""
    return _dispatch("render", path, python, project, worker.ANALYSIS_TIMEOUT_SECONDS,
                     output_path=output_path, variable=variable, inputs=inputs)


def run_pipeline(path: str, inputs: Optional[Dict[str, Any]] = None,
                 variable: Optional[str] = None, rung: str = SMOKE,
                 budget_sweeps: int = DEFAULT_BUDGET_SWEEPS,
                 timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
                 python: Optional[str] = None, project: Optional[str] = None,
                 inputs_from: Optional[str] = None,
                 inputs_args: Optional[Sequence[Any]] = None,
                 inputs_kwargs: Optional[Dict[str, Any]] = None,
                 targets: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """
    Executes a pipeline in a child process, under a wall clock. See `_run`.

    In another environment the worker runs this same handler there, which
    starts the run process with that environment's interpreter - so the run's
    own wall clock still applies, and the worker's allows for startup on top.
    """
    return _dispatch("run", path, python, project,
                     timeout_seconds + worker.STARTUP_ALLOWANCE_SECONDS,
                     inputs=inputs, variable=variable, rung=rung,
                     budget_sweeps=budget_sweeps, timeout_seconds=timeout_seconds,
                     inputs_from=inputs_from, inputs_args=inputs_args, inputs_kwargs=inputs_kwargs,
                     targets=targets)


def compare_runs(
    path_a: str,
    path_b: str,
    inputs: Optional[Dict[str, Any]] = None,
    variable_a: Optional[str] = None,
    variable_b: Optional[str] = None,
    rung: str = FULL,
    budget_sweeps: int = DEFAULT_BUDGET_SWEEPS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    allow_effects: bool = False,
    inputs_from: Optional[str] = None,
    inputs_args: Optional[Sequence[Any]] = None,
    inputs_kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Runs two pipelines on the same inputs and reports where they disagree.

    Built for translation. A converted pipeline that quietly returns a different
    number is worse than no conversion, because it looks cleaner and gets
    trusted. Neither the agent nor static analysis can catch that; only running
    both and comparing can.

    Each side is resolved and run in its own project's environment (since
    1.30.0; docs/design/007), through the same `run` op as run_pipeline. Before
    either runs, both are loaded there and checked for declared side effects:
    both files really execute, so every effect would run twice, and one side's
    must not fire before the other is refused. `allow_effects=True` permits it.

    The rung defaults to `full`: a comparison of two single sweeps says little,
    since the divergence that matters is where each one settles. Inputs are
    identical on both sides - what the caller supplies, or else the literals in
    path_a's own run() call - and neither side adds its own literals.
    """
    if rung not in RUNGS:
        return {"ok": False, "error": f"Unknown rung {rung!r}; expected one of {list(RUNGS)}."}

    sides = (("a", path_a, variable_a), ("b", path_b, variable_b))
    because = "compare_runs executes BOTH files, so every side effect would run twice, once per file"
    for label, path, variable in sides:
        checked = _dispatch("effects", path, None, None, worker.ANALYSIS_TIMEOUT_SECONDS,
                            variable=variable, because=because)
        if not checked.get("ok"):
            return checked
        if checked["refusal"] and not allow_effects:
            return {"ok": False, "error": f"{path}: {checked['refusal']}", "refused": "side-effects"}

    supplied = dict(inputs or {})
    recovered: Dict[str, Any] = {}
    if not supplied:
        recovered = {
            name: value for name, value in input_map_in_source(path_a).items() if value is not _UNRESOLVED
        }
    shared = {**recovered, **supplied}

    runs = {}
    for label, path, variable in sides:
        runs[label] = _dispatch(
            "run", path, None, None, timeout_seconds + worker.STARTUP_ALLOWANCE_SECONDS,
            inputs=shared, variable=variable, rung=rung, budget_sweeps=budget_sweeps,
            timeout_seconds=timeout_seconds, inputs_from=inputs_from, inputs_args=inputs_args,
            inputs_kwargs=inputs_kwargs, use_source_literals=False,
        )
        if runs[label].get("refused"):
            # Refused before running (a value that cannot cross the boundary,
            # an argument an older project does not know): say so as itself,
            # and do not run the other side for nothing.
            return {**runs[label], "error": f"{path}: {runs[label].get('error')}"}

    coerced = sorted({n for r in runs.values() for n in r.get("inputs_used", {}).get("coerced", [])})
    built = sorted({n for r in runs.values() for n in r.get("inputs_used", {}).get("built_by_project", [])})
    inputs_used = {
        "supplied": sorted(supplied),
        "recovered_from_a": sorted(recovered),
        **({"coerced": coerced} if coerced else {}),
        **({"built_by_project": built} if inputs_from else {}),
    }

    failed = [label for label, result in runs.items() if not result.get("ok")]
    if failed:
        return {
            "ok": False,
            "error": (
                f"Cannot compare: {' and '.join(sorted(failed))} did not run. "
                f"A comparison needs both sides."
            ),
            "runs": runs,
            "inputs_used": inputs_used,
        }

    comparison = diff_states(runs["a"].get("state", {}), runs["b"].get("state", {}))
    summaries = {label: {**_summary(result), "interpreter": result.get("interpreter")}
                 for label, result in runs.items()}
    convergence_differs = summaries["a"]["converged"] != summaries["b"]["converged"]
    if convergence_differs:
        comparison["match"] = False

    return {
        "ok": True,
        **comparison,
        "convergence_differs": convergence_differs,
        "runs": summaries,
        "inputs_used": {
            **inputs_used,
            "note": (
                "The same inputs were used for both sides; a comparison on "
                "different inputs would mean nothing."
            ),
        },
    }
