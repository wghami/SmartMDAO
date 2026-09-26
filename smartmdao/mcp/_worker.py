"""
The process that handles a pipeline in its own project's environment.

Started by the server as `<project python> -m smartmdao.mcp._worker`, so the
code running here is the project's SmartMDAO, with the project's libraries. It
reads one JSON request on stdin and writes one JSON response on stdout:

    {"protocol": [1, 1], "op": "validate", "args": {"path": ..., ...}}
    {"protocol": 1, "smartmdao": "1.26.0", "result": {...}}

Every op calls the same handler function the server calls in-process when the
environment is its own - one implementation, two transports. See
docs/design/007 for why the request carries a RANGE of protocol versions: the
worker answers in the highest one both sides speak, so no handshake round trip
is needed, and at most of a second per spawn that matters.

Run directly for debugging:

    echo '{"protocol": [1, 1], "op": "analyze", "args": {"path": "model.py"}}' \\
        | python -m smartmdao.mcp._worker
"""
import json
import sys
from typing import Any, Dict

#: Protocol versions this worker speaks, lowest and highest. Change the request
#: or response shape -> raise the upper bound, and keep answering the old one
#: for as long as servers speaking it are expected to be around.
PROTOCOL = (1, 1)


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("smartmdao")
    except PackageNotFoundError:                     # pragma: no cover - a bare checkout
        return "unknown"


def handle(request: Dict[str, Any]) -> Dict[str, Any]:
    """Answers one request. Never raises: every failure comes back as data."""
    from .handlers import LOCAL_OPS

    own = _version()
    try:
        low, high = request.get("protocol") or (None, None)
        chosen = min(int(high), PROTOCOL[1])
        acceptable = chosen >= max(int(low), PROTOCOL[0])
    except (TypeError, ValueError):
        acceptable = False
    if not acceptable:
        return {
            "protocol": None,
            "smartmdao": own,
            "error": {
                "kind": "protocol",
                "message": (
                    f"No common protocol version: the server asked for "
                    f"{request.get('protocol')!r}, this worker (SmartMDAO {own}) speaks "
                    f"{PROTOCOL[0]}..{PROTOCOL[1]}."
                ),
                "supported": list(PROTOCOL),
            },
        }

    op = request.get("op")
    if op not in LOCAL_OPS:
        return {
            "protocol": chosen,
            "smartmdao": own,
            "error": {"kind": "op", "message": f"Unknown op {op!r}; expected one of {sorted(LOCAL_OPS)}."},
        }

    return {"protocol": chosen, "smartmdao": own, "result": LOCAL_OPS[op](**(request.get("args") or {}))}


def main() -> None:  # pragma: no cover - exercised as a subprocess
    # stdout is the answer. Anything the user's code prints goes to stderr,
    # which the server keeps; see loader.user_output_to_stderr.
    answer = sys.stdout
    sys.stdout = sys.stderr
    try:
        try:
            request = json.load(sys.stdin)
        except Exception as error:
            response = {"protocol": None, "error": {"kind": "request", "message": f"Malformed request: {error}"}}
        else:
            response = handle(request)
    finally:
        sys.stdout = answer
    json.dump(response, answer)


if __name__ == "__main__":  # pragma: no cover
    main()
