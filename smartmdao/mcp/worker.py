"""
The server's side of the worker: send one request, get one answer, never raise.

See `_worker.py` for the other side and docs/design/007 for the design. Every
way a worker can fail - not starting, not answering in time, dying, answering
in something other than JSON, or speaking no common protocol - comes back as
`{"ok": False, ...}`, so the agent reads the reason instead of losing its turn
to an exception.
"""
import json
import logging
import subprocess
from typing import Any, Dict, Optional, Tuple

from ._worker import PROTOCOL
from .environment import MIN_PROJECT_VERSION, Interpreter

logger = logging.getLogger(__name__)

#: Wall clock for analyse / validate / explain / render in another
#: environment. Analysing a file imports it, and a file whose top-level code
#: hangs must not hang the server.
ANALYSIS_TIMEOUT_SECONDS = 60.0

#: Added to a run's own wall clock for the worker around it: starting an
#: interpreter and importing the project costs time before the run's clock
#: starts.
STARTUP_ALLOWANCE_SECONDS = 30.0

_TAIL = 2000


def call(
    interpreter: Interpreter, op: str, args: Dict[str, Any], timeout: float
) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    Runs `op` in `interpreter`'s environment.

    Returns `(result, version)`: the handler's result, and the SmartMDAO
    version that produced it - or `(failure, None)`.
    """
    request = json.dumps({"protocol": list(PROTOCOL), "op": op, "args": args})
    where = interpreter.environment

    try:
        completed = subprocess.run(
            [interpreter.path, "-m", "smartmdao.mcp._worker"],
            input=request, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"Worker in {where} killed after {timeout}s.")
        return {
            "ok": False,
            "timed_out": True,
            "error": f"The worker in {where} did not answer within {timeout:g}s and was killed.",
        }, None
    except OSError as error:
        return {"ok": False, "error": f"Could not start {interpreter.path}: {error}"}, None

    stderr = (completed.stderr or "")[-_TAIL:]

    if completed.returncode != 0:
        if "No module named" in stderr and ("_worker" in stderr or "'smartmdao'" in stderr):
            found = interpreter.smartmdao or "none found"
            return {
                "ok": False,
                "refused": "version",
                "error": (
                    f"The environment at {where} cannot run the worker (SmartMDAO: {found}); "
                    f"it needs smartmdao>={MIN_PROJECT_VERSION}."
                ),
                "stderr": stderr,
            }, None
        return {
            "ok": False,
            "error": (
                f"The worker in {where} died with exit code {completed.returncode} - a "
                f"crash rather than an exception."
            ),
            "stderr": stderr,
        }, None

    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": f"The worker in {where} answered with something that is not JSON.",
            "stdout": (completed.stdout or "")[-_TAIL:],
            "stderr": stderr,
        }, None

    if "error" in response:
        return {
            "ok": False,
            "refused": response["error"].get("kind", "worker"),
            "error": response["error"].get("message", "The worker refused the request."),
        }, None

    result = response["result"]
    if stderr and "stderr" not in result:
        # Logs and prints from the user's code: not a failure, not to be lost.
        result["stderr"] = stderr
    return result, response.get("smartmdao")
