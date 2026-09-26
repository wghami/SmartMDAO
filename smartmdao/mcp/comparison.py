"""
Running two pipelines on the same inputs and diffing the answers.

This exists for translation. Converting hand-written code to SmartMDAO is a
stated use case, and its failure mode is **silent semantic drift**: the
translation looks cleaner, so it gets trusted, and it quietly returns a
different number. The README's own hand-rolled loop illustrates it — it
converges on `y2` alone, while the obvious translation converges on a `max()`
across `y1` and `y2`, which can stop at a different iteration or not at all.

Neither the agent nor static analysis can catch that. Only running both and
comparing can, which is why this is a better justification for execution
tooling than a generic "run my pipeline".

Also useful for: faithful-versus-idiomatic translations, a refactor you want to
prove was behaviour-preserving, and the same model under two solvers.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

from ._runner import DEFAULT_BUDGET_SWEEPS, FULL, RUNGS
from .execution import DEFAULT_TIMEOUT_SECONDS, run_in_subprocess

logger = logging.getLogger(__name__)

#: Relative and absolute tolerance for calling two numbers "the same".
#: 1e-6 relative is loose enough to absorb a different iteration count and
#: tight enough that a changed convergence criterion shows up.
DEFAULT_RTOL = 1e-6
DEFAULT_ATOL = 1e-9

#: Differences reported in full. The worst offenders are the informative ones.
MAX_DIFFERENCES = 25


def _close(left: Any, right: Any, rtol: float, atol: float) -> Tuple[bool, Optional[Dict]]:
    """
    Whether two values agree, and by how much they miss if not.

    Numbers compare within a tolerance; everything else compares exactly,
    because there is no meaningful "nearly" for a frozenset of decisions.
    """
    numeric = (int, float)

    # Booleans are ints in Python, so they would otherwise compare numerically
    # and `True` would match `1.0` - not a match anyone wants in a result diff.
    if isinstance(left, bool) or isinstance(right, bool):
        if left is right or left == right:
            return True, None
        return False, {"a": left, "b": right}

    if isinstance(left, numeric) and isinstance(right, numeric):
        difference = abs(left - right)
        scale = max(abs(left), abs(right))
        if difference <= atol or (scale and difference / scale <= rtol):
            return True, None
        return False, {
            "a": left,
            "b": right,
            "absolute": difference,
            "relative": (difference / scale) if scale else None,
        }

    if left == right:
        return True, None
    return False, {"a": left, "b": right}


def _summary(result: Dict[str, Any]) -> Dict[str, Any]:
    """The part of a run worth reporting alongside a diff."""
    reports = result.get("convergence_reports") or []
    return {
        "pipeline": result.get("pipeline"),
        "path": result.get("path"),
        "converged": result.get("converged"),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "iterations": [report.get("iterations") for report in reports],
        "statuses": [report.get("status") for report in reports],
    }


def diff_states(
    left: Dict[str, Any],
    right: Dict[str, Any],
    rtol: float = DEFAULT_RTOL,
    atol: float = DEFAULT_ATOL,
) -> Dict[str, Any]:
    """
    Compares two result states, worst mismatch first.

    Ordered by relative difference so the variable that moved most is the first
    thing read - a translation that shifts one number by 40% and five by 1e-7
    has one problem, not six.
    """
    only_left = sorted(set(left) - set(right))
    only_right = sorted(set(right) - set(left))

    differences: List[Dict[str, Any]] = []
    identical = 0

    for name in sorted(set(left) & set(right)):
        same, detail = _close(left[name], right[name], rtol, atol)
        if same:
            identical += 1
        else:
            differences.append({"variable": name, **detail})

    differences.sort(
        key=lambda entry: (entry.get("relative") or 0.0, entry.get("absolute") or 0.0),
        reverse=True,
    )

    hidden = max(0, len(differences) - MAX_DIFFERENCES)
    return {
        "match": not differences and not only_left and not only_right,
        "identical": identical,
        "differences": differences[:MAX_DIFFERENCES],
        "differences_omitted": hidden,
        "only_in_a": only_left,
        "only_in_b": only_right,
        "tolerance": {"rtol": rtol, "atol": atol},
    }


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
) -> Dict[str, Any]:
    """
    Runs both pipelines on the **same** inputs and reports where they disagree.

    **Both files really execute**, so every declared side effect would run
    twice - once per file. "Compare two translations" should not quietly mean
    "send every notification twice", so a pipeline declaring effects is refused
    unless `allow_effects=True`. The check loads each file statically, which
    registers steps without evaluating any of them.

    The rung defaults to `full` here, unlike `run_pipeline`: a comparison of two
    single sweeps says almost nothing, since the interesting divergence is in
    *where each one settles*. Smoke both first with `run_pipeline` if you want
    the cost before committing.

    Inputs must be identical for the comparison to mean anything, so unlike a
    single run, values are not taken from each file separately. Whatever the
    caller supplies goes to both; if the caller supplies nothing, the values are
    recovered from `path_a` and used for both, and that is reported.
    """
    if rung not in RUNGS:
        return {"ok": False, "error": f"Unknown rung {rung!r}; expected one of {list(RUNGS)}."}

    from .loader import _UNRESOLVED, PipelineLoadError, input_map_in_source, load_pipeline

    from ..discretisation import effective_steps
    from ..effects import effects_refusal_message

    supplied = dict(inputs or {})
    recovered: Dict[str, Any] = {}

    loaded = {}
    for label, path, variable in (("a", path_a, variable_a), ("b", path_b, variable_b)):
        try:
            loaded[label] = load_pipeline(path, variable)
        except PipelineLoadError as error:
            return {"ok": False, "error": str(error)}

    if not allow_effects:
        for label, path in (("a", path_a), ("b", path_b)):
            declared = [s for s in effective_steps(loaded[label].pipeline) if s.has_effects]
            if declared:
                return {
                    "ok": False,
                    "error": f"{path}: " + effects_refusal_message(
                        declared,
                        "compare_runs executes BOTH files, so every side effect "
                        "would run twice, once per file",
                    ),
                    "refused": "side-effects",
                }

    if not supplied:
        loaded_a = loaded["a"]
        recovered = {
            name: value
            for name, value in input_map_in_source(loaded_a.path).items()
            if value is not _UNRESOLVED
        }

    shared = {**recovered, **supplied}

    runs = {}
    for label, path, variable in (("a", path_a, variable_a), ("b", path_b, variable_b)):
        runs[label] = run_in_subprocess(
            path=str(path),
            inputs=shared,
            variable=variable,
            rung=rung,
            budget_sweeps=budget_sweeps,
            timeout_seconds=timeout_seconds,
        )

    failed = [label for label, result in runs.items() if not result.get("ok")]
    if failed:
        return {
            "ok": False,
            "error": (
                f"Cannot compare: {' and '.join(sorted(failed))} did not run. "
                f"A comparison needs both sides."
            ),
            "runs": {label: result for label, result in runs.items()},
            "inputs_used": {"supplied": sorted(supplied), "recovered_from_a": sorted(recovered)},
        }

    comparison = diff_states(
        runs["a"].get("state", {}), runs["b"].get("state", {})
    )

    # A different destination is a difference even when the numbers are close.
    summaries = {label: _summary(result) for label, result in runs.items()}
    convergence_differs = summaries["a"]["converged"] != summaries["b"]["converged"]
    if convergence_differs:
        comparison["match"] = False

    return {
        "ok": True,
        **comparison,
        "convergence_differs": convergence_differs,
        "runs": summaries,
        "inputs_used": {
            "supplied": sorted(supplied),
            "recovered_from_a": sorted(recovered),
            "note": (
                "The same inputs were used for both sides; a comparison on "
                "different inputs would mean nothing."
            ),
        },
    }
