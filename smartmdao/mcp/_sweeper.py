"""
A campaign worker: loads a pipeline once, then runs point after point.

Started by a campaign's coordinator as `<project python> -m smartmdao.mcp._sweeper`
(docs/design/006). A fresh process per point was measured at about 1,000x the
cost of a small point, so a worker stays up and reads JSON lines on stdin:

    setup  {"path": ..., "outputs": [...], "inputs_from": ..., ...}
    reply  {"ready": true, "model_hash": ..., "smartmdao": ...}   (or ready: false, with why)
    point  {"key": ..., "params": {...}, "seed": 7}
    reply  {"key": ..., "status": "ok", "elapsed": 0.08, "outputs": {...}}

stdout carries only those replies; anything the user's code prints goes to
stderr. A point's inputs are rebuilt here, by the project's own code, so the
store never has to hold them - whatever their type.
"""
import hashlib
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: Largest array or list an output may be, in elements, for the store to hold
#: it as JSON. A larger one fails that point with a reason: reduce it in a step.
OUTPUT_ELEMENT_CAP = 10_000

from .protocol import ERROR, NOT_CONVERGED, OK  # noqa: E402


class OutputNotStorable(TypeError):
    """An output the store cannot hold as JSON; the message says which."""


def to_json(value: Any, name: str, budget: Optional[List[int]] = None) -> Any:
    """`value` as JSON-ready data, or `OutputNotStorable` saying why not."""
    budget = budget if budget is not None else [OUTPUT_ELEMENT_CAP]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "tolist") and hasattr(value, "shape"):          # numpy array or scalar
        size = getattr(value, "size", 1)
        if size > budget[0]:
            raise OutputNotStorable(
                f"output '{name}' has {size} elements, more than the {OUTPUT_ELEMENT_CAP} a "
                f"campaign stores; reduce it in a step (a mean, a percentile) and record that."
            )
        budget[0] -= size
        return value.tolist()
    if isinstance(value, (list, tuple)):
        if len(value) > budget[0]:
            raise OutputNotStorable(
                f"output '{name}' has {len(value)} elements, more than the {OUTPUT_ELEMENT_CAP} "
                f"a campaign stores; reduce it in a step and record that."
            )
        budget[0] -= len(value)
        return [to_json(item, name, budget) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: to_json(item, name, budget) for key, item in value.items()}
    raise OutputNotStorable(
        f"output '{name}' is a {type(value).__name__}, which a campaign cannot store as JSON; "
        f"record numbers, strings or lists of them."
    )


def model_hash(source_files: Tuple[str, ...], extra: Tuple[Path, ...] = ()) -> str:
    """
    A hash of the model's own source: the entry file, the user modules loading
    it imported, and the `inputs_from` module. Paths are hashed by name, not by
    location, so moving a project does not invalidate its campaigns.
    """
    digest = hashlib.sha256()
    for path in sorted({Path(p) for p in source_files} | set(extra), key=lambda p: p.name + str(p)):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _inputs_from_file(spec: Optional[str], entry: Path) -> Tuple[Path, ...]:
    """The file `inputs_from` names, when it can be found without running it."""
    if not spec:
        return ()
    from .boundary import locate
    from .loader import search_directory

    # Beside the project's own files, the one `build_inputs` will call. An
    # installed package is not hashed: its version is part of the environment.
    found = locate(spec.partition(":")[0], search_directory(entry))
    return (found,) if found else ()


def prepare(setup: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """
    Loads the pipeline for a campaign. Returns `(state, reply)`: `state` is what
    `run_point` needs, or None when the reply refuses.
    """
    from importlib.metadata import version

    from ..analysis import analyze
    from ..discretisation import effective_steps
    from ..effects import effects_refusal_message
    from .loader import PipelineLoadError, load_pipeline

    try:
        loaded = load_pipeline(setup["path"], setup.get("variable"))
    except PipelineLoadError as error:
        return None, {"ready": False, "error": str(error)}

    outputs = list(setup.get("outputs") or analyze(loaded.pipeline).terminal_outputs)
    try:
        pipeline = loaded.pipeline.upstream(*outputs)
    except ValueError as error:
        return None, {"ready": False, "error": str(error)}

    declared = [step for step in effective_steps(pipeline) if step.has_effects]
    if declared and not setup.get("allow_effects"):
        return None, {
            "ready": False,
            "refused": "side-effects",
            "error": effects_refusal_message(declared, "a campaign runs the pipeline once per point"),
        }

    seed_input = setup.get("seed_input")
    if seed_input:
        read = {name for step in effective_steps(pipeline) for name in step.get_signature().parameters}
        if seed_input not in read:
            return None, {
                "ready": False,
                "refused": "seed",
                "error": (
                    f"No step computing {outputs} reads the seed input '{seed_input}', so every "
                    f"realization would be the same run. Name the input the model's randomness "
                    f"comes from, or drop the seeds."
                ),
            }

    entry = Path(loaded.path)
    state = {
        "pipeline": pipeline,
        "path": entry,
        "outputs": outputs,
        "inputs_from": setup.get("inputs_from"),
        "inputs_args": setup.get("inputs_args") or [],
        "inputs_kwargs": setup.get("inputs_kwargs") or {},
        "inputs": setup.get("inputs") or {},
        "seed_input": seed_input,
    }
    return state, {
        "ready": True,
        "outputs": outputs,
        "model_hash": model_hash(loaded.source_files, _inputs_from_file(setup.get("inputs_from"), entry)),
        "smartmdao": version("smartmdao"),
    }


def run_point(state: Dict[str, Any], point: Dict[str, Any]) -> Dict[str, Any]:
    """Runs one point. Never raises: every outcome is a record."""
    from .boundary import InputsFromError, build_inputs, whole_number_floats

    record: Dict[str, Any] = {"key": point.get("key")}
    started = time.perf_counter()
    try:
        params = dict(point.get("params") or {})
        if state["inputs_from"]:
            values = build_inputs(
                state["inputs_from"], state["inputs_args"],
                {**state["inputs_kwargs"], **params}, state["path"],
            )
            from_json = dict(state["inputs"])
        else:
            # Without inputs_from a point's values ARE inputs, and the point,
            # being the more specific, overrides the campaign-wide ones.
            values = {}
            from_json = {**state["inputs"], **params}
        if state["seed_input"] and point.get("seed") is not None:
            from_json[state["seed_input"]] = point["seed"]
        # Values that came as JSON cross the same boundary as run_pipeline's (009).
        from_json, _, refusal = whole_number_floats([state["pipeline"]], from_json)
        if refusal:
            raise InputsFromError(refusal)

        result = state["pipeline"].run(**{**values, **from_json})
        record["elapsed"] = round(time.perf_counter() - started, 6)
        record["outputs"] = {name: to_json(result.get(name), name) for name in state["outputs"]}
        unsettled = [str(report) for report in result.get("convergence_reports", []) if not report.converged]
        record["status"] = NOT_CONVERGED if unsettled else OK
        if unsettled:
            record["reason"] = "; ".join(unsettled)
    except Exception as error:
        record["elapsed"] = round(time.perf_counter() - started, 6)
        record["status"] = ERROR
        record["error"] = f"{type(error).__name__}: {error}"
        record["traceback"] = traceback.format_exc()[-2000:]
    return record


def main() -> None:  # pragma: no cover - exercised as a subprocess
    answer = sys.stdout
    sys.stdout = sys.stderr                       # the user's prints never reach the protocol
    state, reply = prepare(json.loads(sys.stdin.readline()))
    answer.write(json.dumps(reply) + "\n")
    answer.flush()
    if state is None:
        return
    for line in sys.stdin:
        if not line.strip():
            continue
        answer.write(json.dumps(run_point(state, json.loads(line))) + "\n")
        answer.flush()


if __name__ == "__main__":  # pragma: no cover
    main()
