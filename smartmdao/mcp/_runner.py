"""
The child process that actually runs a pipeline.

Reads one JSON request on stdin, writes one JSON response on stdout, and is
spawned fresh per run by `execution.py`. Nothing here is imported by the MCP
server itself — keeping the run in a separate process is what makes a wall
clock enforceable, and it means a discipline that segfaults or exhausts memory
takes this process down rather than the server.

To be clear about what that does and does not buy: it is **reliability, not
safety**. The agent driving the connector already has a shell and can run the
file itself; what the subprocess adds is a hard kill for a solve that will not
stop, a typed result instead of scraped stdout, and crash isolation. See the
correction in docs/design/001-mcp-connector.md.

Run directly for debugging:

    echo '{"path": "model.py", "rung": "smoke"}' | python -m smartmdao.mcp._runner
"""
import json
import sys
import time
import traceback
from typing import Any, Dict, List

#: Cost rungs, cheapest first. `smoke` exists to answer "does this execute at
#: all" and to *measure the unit cost*, so the estimate for every larger run
#: falls out of one cheap run.
SMOKE = "smoke"
BUDGETED = "budgeted"
FULL = "full"
RUNGS = (SMOKE, BUDGETED, FULL)

#: Sweeps allowed on the `budgeted` rung unless the caller says otherwise.
DEFAULT_BUDGET_SWEEPS = 25

#: Longest list or array preview returned. A converged `memory` holds numpy
#: arrays and a residual history per cyclic block; unsummarised, the first real
#: run floods the client's context window.
PREVIEW_ITEMS = 10


def _apply_rung(solver, rung: str, budget_sweeps: int) -> Dict[str, Any]:
    """
    Caps the solver's iterations for this rung, and says what it did.

    `DAGSolver` has no iteration count - one pass *is* the full run - so every
    rung is identical for an acyclic pipeline, and saying so is more useful
    than silently doing nothing.
    """
    original = getattr(solver, "max_iterations", None)

    if original is None:
        return {"applied": False, "reason": "solver does not iterate; every rung is one pass"}

    if rung == SMOKE:
        solver.max_iterations = 1
    elif rung == BUDGETED:
        solver.max_iterations = min(original, budget_sweeps)

    return {
        "applied": solver.max_iterations != original,
        "max_iterations": solver.max_iterations,
        "solver_default": original,
    }


def _instrument(pipeline) -> Dict[str, List[float]]:
    """
    Wraps each discipline to record how long every invocation takes.

    `Step.get_signature` unwraps through `functools.wraps`, so the dependency
    graph is unaffected - the wrapper is invisible to everything but the clock.
    """
    import functools

    timings: Dict[str, List[float]] = {}

    for step in pipeline.steps:
        original = step.fn
        record = timings.setdefault(step.name, [])

        @functools.wraps(original)
        def timed(*args, _original=original, _record=record, **kwargs):
            started = time.perf_counter()
            try:
                return _original(*args, **kwargs)
            finally:
                _record.append(time.perf_counter() - started)

        step.fn = timed

    return timings


def summarise(value: Any, depth: int = 0) -> Any:
    """
    Turns a result value into something JSON-safe and context-sized.

    Large arrays become a shape plus a statistical summary plus a short
    preview; the full data stays on the engineer's machine where it already is.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value

    if isinstance(value, float):
        # inf/nan are not JSON, and a diverged residual is exactly when you
        # most want to see them.
        if value != value or value in (float("inf"), float("-inf")):
            return repr(value)
        return value

    if hasattr(value, "shape") and hasattr(value, "dtype"):          # numpy-like
        try:
            flat = value.reshape(-1).tolist()
        except Exception:                                            # pragma: no cover
            return repr(value)[:200]
        summary = {
            "type": "array",
            "shape": list(value.shape),
            "dtype": str(value.dtype),
            "preview": [summarise(item, depth + 1) for item in flat[:PREVIEW_ITEMS]],
        }
        if flat and all(isinstance(item, (int, float)) for item in flat):
            summary.update(min=min(flat), max=max(flat))
        if len(flat) > PREVIEW_ITEMS:
            summary["omitted"] = len(flat) - PREVIEW_ITEMS
        return summary

    if isinstance(value, (list, tuple)):
        items = [summarise(item, depth + 1) for item in list(value)[:PREVIEW_ITEMS]]
        if len(value) > PREVIEW_ITEMS:
            items.append(f"... {len(value) - PREVIEW_ITEMS} more omitted")
        return items

    if isinstance(value, (set, frozenset)):
        return summarise(sorted(value, key=repr), depth + 1)

    if isinstance(value, dict):
        return {str(key): summarise(item, depth + 1) for key, item in value.items()}

    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import fields

        return {
            field.name: summarise(getattr(value, field.name), depth + 1)
            for field in fields(value)
        }

    return repr(value)[:200]


def run(request: Dict[str, Any]) -> Dict[str, Any]:
    """Loads, runs and summarises. Never raises: failures come back as data."""
    from .loader import PipelineLoadError, load_pipeline

    rung = request.get("rung", SMOKE)
    if rung not in RUNGS:
        return {"ok": False, "error": f"Unknown rung {rung!r}; expected one of {list(RUNGS)}."}

    try:
        loaded = load_pipeline(request["path"], request.get("variable"))
    except PipelineLoadError as error:
        return {"ok": False, "error": str(error)}

    inputs = dict(request.get("inputs") or {})
    rung_detail = _apply_rung(
        loaded.pipeline.solver, rung, request.get("budget_sweeps", DEFAULT_BUDGET_SWEEPS)
    )
    timings = _instrument(loaded.pipeline)

    started = time.perf_counter()
    try:
        state = loaded.pipeline.run(**inputs)
    except Exception as error:
        return {
            "ok": False,
            "error": f"{type(error).__name__}: {error}",
            "traceback": traceback.format_exc()[-2000:],
            "elapsed_seconds": round(time.perf_counter() - started, 4),
            "rung": rung,
        }
    elapsed = time.perf_counter() - started

    reports = [summarise(report) for report in state.pop("convergence_reports", [])]
    state.pop("residual_history", None)          # per-block, already in the reports

    per_step = {
        name: {"calls": len(samples), "total_seconds": round(sum(samples), 6)}
        for name, samples in timings.items()
        if samples
    }

    return {
        "ok": True,
        "rung": rung,
        "rung_detail": rung_detail,
        "converged": all(report.get("status") == "converged" for report in reports)
        if reports
        else True,
        "convergence_reports": reports,
        "elapsed_seconds": round(elapsed, 4),
        "per_step": per_step,
        "state": {key: summarise(value) for key, value in state.items()},
    }


def main() -> None:  # pragma: no cover - exercised as a subprocess
    try:
        request = json.load(sys.stdin)
    except Exception as error:
        json.dump({"ok": False, "error": f"Malformed request: {error}"}, sys.stdout)
        return
    # stdout is this process's answer. A discipline that prints writes to
    # stderr instead, which the parent returns alongside the result.
    answer = sys.stdout
    sys.stdout = sys.stderr
    try:
        result = run(request)
    finally:
        sys.stdout = answer
    json.dump(result, answer)


if __name__ == "__main__":  # pragma: no cover
    main()
