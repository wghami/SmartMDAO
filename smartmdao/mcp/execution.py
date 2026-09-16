"""
Running a pipeline in a child process, under a wall clock.

What this buys, stated accurately: a **hard kill** for a solve that will not
stop, a **typed result** instead of scraped stdout, and **crash isolation** so
a segfaulting discipline does not take the server with it.

What it does not buy is safety. The agent driving this connector already has a
shell; refusing to run a pipeline does not create a boundary, it pushes the run
somewhere with less control. See the correction in
docs/design/001-mcp-connector.md.
"""
import json
import logging
import subprocess
import sys
from typing import Any, Dict, Optional, Sequence

from ._runner import BUDGETED, DEFAULT_BUDGET_SWEEPS, FULL, RUNGS, SMOKE

logger = logging.getLogger(__name__)

#: Wall clock applied to every run, whatever the rung. Long enough for a real
#: model, short enough that a hung solve does not strand the session. Always
#: enforced - there is no "no timeout" option, because the failure it prevents
#: is a client waiting forever with no way to interrupt.
DEFAULT_TIMEOUT_SECONDS = 60.0

#: Below this, a projection from one sweep is noise rather than an estimate.
_MEASURABLE_SECONDS = 1e-4


def estimate_from_smoke(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Turns one measured sweep into what a larger run would cost.

    This is why the smoke rung earns its place twice: it is the cheapest check
    that the code runs *and* it measures the unit cost, so the engineer can be
    quoted a number instead of a disclaimer before committing to anything
    bigger. Worst case only - how many sweeps a loop actually needs is not
    knowable in advance, and pretending otherwise would be the sort of
    confident guess this project exists to avoid.
    """
    if not result.get("ok") or result.get("rung") != SMOKE:
        return None

    unit = result.get("elapsed_seconds", 0.0)
    ceiling = (result.get("rung_detail") or {}).get("solver_default")

    estimate: Dict[str, Any] = {"one_sweep_seconds": unit}

    if unit < _MEASURABLE_SECONDS:
        estimate["note"] = "one sweep is too fast to project from; cost is not a concern here"
        return estimate

    estimate["budgeted_worst_case_seconds"] = round(unit * DEFAULT_BUDGET_SWEEPS, 3)
    if ceiling:
        estimate["full_worst_case_seconds"] = round(unit * ceiling, 3)
        estimate["note"] = (
            f"one sweep took {unit:.3g}s; a full run may need up to "
            f"{ceiling} sweeps, so budget up to "
            f"{unit * ceiling:.3g}s. Quote this before running it."
        )
    return estimate


def run_in_subprocess(
    path: str,
    inputs: Optional[Dict[str, Any]] = None,
    variable: Optional[str] = None,
    rung: str = SMOKE,
    budget_sweeps: int = DEFAULT_BUDGET_SWEEPS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """
    Runs `path`'s pipeline in a child process and returns a summarised result.

    Never raises for anything the child does: a crash, a timeout or a malformed
    reply all come back as `{"ok": False, ...}` so the agent can read and act on
    them rather than losing its turn to an exception.
    """
    if rung not in RUNGS:
        return {"ok": False, "error": f"Unknown rung {rung!r}; expected one of {list(RUNGS)}."}
    if timeout_seconds <= 0:
        return {"ok": False, "error": "timeout_seconds must be positive."}

    request = json.dumps(
        {
            "path": str(path),
            "variable": variable,
            "inputs": inputs or {},
            "rung": rung,
            "budget_sweeps": budget_sweeps,
        }
    )

    try:
        completed = subprocess.run(
            [sys.executable, "-m", "smartmdao.mcp._runner"],
            input=request,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"Run of {path} killed after {timeout_seconds}s.")
        return {
            "ok": False,
            "timed_out": True,
            "error": (
                f"Killed after {timeout_seconds}s. The pipeline was still running - "
                f"a feedback loop may not be converging. Try rung='smoke' to see "
                f"what one sweep costs, or raise timeout_seconds deliberately."
            ),
            "rung": rung,
        }

    if completed.returncode != 0:
        # A segfault, an OOM kill, or a discipline calling sys.exit. The whole
        # reason for the child process: the server is still standing.
        return {
            "ok": False,
            "error": (
                f"The run process died with exit code {completed.returncode}. "
                f"This is a crash rather than an exception - a discipline may "
                f"have segfaulted or been killed for memory."
            ),
            "stderr": (completed.stderr or "")[-2000:],
            "rung": rung,
        }

    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": "The run process returned output that was not valid JSON.",
            "stdout": (completed.stdout or "")[-2000:],
            "stderr": (completed.stderr or "")[-2000:],
            "rung": rung,
        }

    estimate = estimate_from_smoke(result)
    if estimate:
        result["cost_estimate"] = estimate

    if completed.stderr:
        # Disciplines log; that is not a failure, but it should not be lost.
        result["stderr"] = completed.stderr[-2000:]

    return result
