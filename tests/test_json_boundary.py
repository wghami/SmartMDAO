"""
Inputs across the JSON boundary. Roadmap 6.9-6.13, design record 009.

- Whole numbers become floats in run_pipeline / compare_runs, only where every
  consumer wants a float, and the response says so (F-011).
- Everything JSON cannot carry is built by the project, inside the run, with
  `inputs_from` (F-011, F-016, F-017).
- `Literal` and non-runtime Protocols no longer crash the type checker (F-018).
- A run's stderr no longer opens with runpy's warning (F-014), and every tool
  taking a path advertises python= and project= (F-010).
"""
import asyncio
import textwrap
from pathlib import Path
from typing import Dict, Literal, Optional, Protocol, Tuple, Union

import pytest

from smartmdao import HybridSolver, Pipeline, StandardTypeChecker, validate
from smartmdao.mcp import worker
from smartmdao.mcp.boundary import EXACT_FLOAT_LIMIT, InputsFromError, build_inputs, whole_number_floats
from smartmdao.mcp.environment import EXPLICIT, Interpreter
from smartmdao.mcp.handlers import compare_runs, run_pipeline

FLOAT_PROBE = """
from smartmdao import Pipeline
pipeline = Pipeline(inputs=["x_km"])

@pipeline.step(outputs=["y_km"])
def double(x_km: float) -> float:
    return 2 * x_km
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))
    return path


def consuming(*annotations):
    """A pipeline whose steps read `x` with the given annotations (None: undeclared)."""
    pipeline = Pipeline()
    for index, annotation in enumerate(annotations):
        def step(x):
            return x
        if annotation is not None:
            step.__annotations__ = {"x": annotation}
        step.__name__ = f"s{index}"
        pipeline.add(step, outputs=[f"y{index}"])
    return pipeline


# ==============================================================================
# Whole numbers (F-011)
# ==============================================================================

@pytest.mark.parametrize("annotations", [(float,), (float, Optional[float])])
def test_a_whole_number_becomes_a_float_where_every_consumer_wants_one(annotations):
    values, coerced, refusal = whole_number_floats([consuming(*annotations)], {"x": 2})
    assert values == {"x": 2.0} and type(values["x"]) is float
    assert coerced == ["x"] and refusal is None


@pytest.mark.parametrize("annotations, value", [
    ((float,), True),                        # a bool is a mistake, never repaired
    ((float, None), 2),                      # an undeclared consumer might index with it
    ((Union[int, float],), 2),               # the int is already acceptable
    ((int,), 2),                             # an int was wanted
    ((float,), 2.5),                         # not an int at all
])
def test_anything_else_is_left_alone(annotations, value):
    values, coerced, _ = whole_number_floats([consuming(*annotations)], {"x": value})
    assert values == {"x": value} and coerced == []


def test_a_name_nobody_reads_is_left_alone():
    assert whole_number_floats([consuming(float)], {"other": 2})[1] == []


def test_every_pipeline_must_agree():
    """compare_runs sends one set of inputs to both sides."""
    assert whole_number_floats([consuming(float), consuming(float)], {"x": 2})[1] == ["x"]
    assert whole_number_floats([consuming(float), consuming(int)], {"x": 2})[1] == []


def test_a_number_too_large_to_convert_exactly_is_refused():
    values, coerced, refusal = whole_number_floats([consuming(float)], {"x": EXACT_FLOAT_LIMIT + 1})
    assert "too large to become a float exactly" in refusal
    assert values == {"x": EXACT_FLOAT_LIMIT + 1} and coerced == []


def test_run_pipeline_takes_a_whole_number_and_says_so(tmp_path):
    result = run_pipeline(str(write(tmp_path / "probe.py", FLOAT_PROBE)), inputs={"x_km": 2}, rung="full")

    assert result["ok"] is True, result.get("error")
    assert result["state"]["y_km"] == 4.0
    assert result["inputs_used"]["coerced"] == ["x_km"]


def test_run_pipeline_refuses_an_integer_it_cannot_convert(tmp_path):
    result = run_pipeline(str(write(tmp_path / "probe.py", FLOAT_PROBE)), inputs={"x_km": 2 ** 60})
    assert result["refused"] == "json-boundary"


def test_python_callers_keep_the_strict_rule(tmp_path):
    """Only a tool call is repaired - the cookbook's pitfall still holds in Python."""
    from smartmdao import TypeMismatchError
    from smartmdao.mcp.loader import load_pipeline

    pipeline = load_pipeline(write(tmp_path / "probe.py", FLOAT_PROBE)).pipeline
    with pytest.raises(TypeMismatchError):
        pipeline.run(x_km=2)


def test_compare_runs_repairs_and_reports_too(tmp_path):
    probe = str(write(tmp_path / "probe.py", FLOAT_PROBE))
    result = compare_runs(probe, probe, inputs={"x_km": 2})
    assert result["ok"] is True and result["match"] is True
    assert result["inputs_used"]["coerced"] == ["x_km"]

    assert compare_runs(probe, probe, inputs={"x_km": 2 ** 60})["refused"] == "json-boundary"


# ==============================================================================
# Inputs built by the project (F-011, F-016, F-017)
# ==============================================================================

STRUCTURED = """
from typing import Dict, Tuple
from smartmdao import Pipeline
pipeline = Pipeline(inputs=["bbox", "weights"])

@pipeline.step(outputs=["area"])
def area(bbox: Tuple[float, float, float, float], weights: Dict[float, float]) -> float:
    return (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) * sum(weights.values())
"""

BASELINE = """
def inputs(scenario, scale=1.0):
    widths = {"small": 2.0, "large": 4.0}
    return {"bbox": (0.0, 0.0, widths[scenario] * scale, 3.0), "weights": {0.5: 1.0, 1.5: 2.0}}

def not_a_mapping():
    return [1, 2]

def bad_keys():
    return {1: 2.0}
"""


@pytest.fixture
def structured(tmp_path):
    write(tmp_path / "baseline.py", BASELINE)
    return str(write(tmp_path / "structured.py", STRUCTURED))


def test_json_cannot_carry_a_tuple(structured):
    """The problem, as reported: a tuple arrives as a list and is refused."""
    result = run_pipeline(structured, inputs={"bbox": [0.0, 0.0, 2.0, 3.0], "weights": {"0.5": 1.0}})
    assert result["ok"] is False and "bbox" in result["error"]


def test_the_project_builds_what_json_cannot_carry(structured):
    result = run_pipeline(structured, inputs_from="baseline:inputs", inputs_args=["large"],
                          inputs_kwargs={"scale": 0.5}, rung="full")

    assert result["ok"] is True, result.get("error")
    assert result["state"]["area"] == 18.0                    # tuple and float keys intact
    assert result["inputs_used"]["built_by_project"] == ["bbox", "weights"]


def test_what_the_call_supplies_wins_over_what_the_project_builds(structured):
    result = run_pipeline(structured, inputs={"weights": {"x": 1.0}}, inputs_from="baseline:inputs",
                          inputs_args=["small"], rung="full")

    assert result["state"]["area"] == 6.0
    assert result["inputs_used"]["supplied"] == ["weights"]
    assert result["inputs_used"]["built_by_project"] == ["bbox"]


def test_what_the_project_builds_wins_over_the_files_own_literals(tmp_path):
    write(tmp_path / "baseline.py", BASELINE)
    source = STRUCTURED + '\nif __name__ == "__main__":\n    pipeline.run(bbox=(0.0, 0.0, 1.0, 1.0), weights={0.5: 1.0})\n'
    path = str(write(tmp_path / "with_literals.py", source))

    result = run_pipeline(path, inputs_from="baseline:inputs", inputs_args=["small"], rung="full")

    assert result["state"]["area"] == 18.0                    # the project's, not the literal's 1.0
    assert result["inputs_used"]["found_in_source"] == []


def test_declared_inputs_the_project_built_are_not_missing(tmp_path):
    write(tmp_path / "baseline.py", BASELINE)
    source = STRUCTURED.replace('inputs=["bbox", "weights"]', 'inputs=["bbox", "weights", "unused"]')
    result = run_pipeline(str(write(tmp_path / "declared.py", source)),
                          inputs_from="baseline:inputs", inputs_args=["small"], rung="full")
    assert result["inputs_used"]["declared_not_supplied"] == ["unused"]


@pytest.mark.parametrize("spec, args, says", [
    ("baseline", [], "must look like"),
    ("baseline:nope", [], "could not be found"),
    ("no_such_module:inputs", [], "could not be found"),
    ("baseline:inputs", ["huge"], "raised KeyError"),
    ("baseline:not_a_mapping", [], "must return a mapping"),
    ("baseline:bad_keys", [], "must return a mapping"),
])
def test_what_cannot_build_inputs_is_reported(structured, spec, args, says):
    result = run_pipeline(structured, inputs_from=spec, inputs_args=args, rung="full")
    assert result["ok"] is False and says in result["error"]


def test_a_module_in_the_entry_files_package_is_found(tmp_path):
    package = tmp_path / "study"
    write(package / "__init__.py", "")
    write(package / "baseline.py", BASELINE)
    entry = write(package / "pipeline.py", STRUCTURED)

    assert build_inputs("study.baseline:inputs", ["small"], None, entry)["bbox"][2] == 2.0
    with pytest.raises(InputsFromError):
        build_inputs("study.baseline:inputs", ["nope"], None, entry)


def test_compare_runs_builds_the_same_inputs_on_both_sides(structured):
    result = compare_runs(structured, structured, inputs_from="baseline:inputs", inputs_args=["small"])
    assert result["ok"] is True and result["match"] is True
    assert result["inputs_used"]["built_by_project"] == ["bbox", "weights"]


def test_analysis_tools_never_take_inputs_from():
    """They do not execute project code (invariant 1)."""
    import inspect
    from smartmdao.mcp import handlers

    for tool in (handlers.analyze_pipeline, handlers.validate_pipeline,
                 handlers.explain_pipeline, handlers.render_pipeline_diagram):
        assert "inputs_from" not in inspect.signature(tool).parameters


# ==============================================================================
# Old workers, new arguments
# ==============================================================================

def fake_interpreter(tmp_path, body):
    script = tmp_path / "bin" / "python"
    write(script, f"#!/bin/sh\ncat > {tmp_path}/request.json\n{body}\n")
    script.chmod(0o755)
    return Interpreter(str(script), str(tmp_path), EXPLICIT, "1.27.0")


@pytest.mark.skipif(__import__("os").name == "nt", reason="fake interpreters are shell scripts")
def test_an_old_worker_that_does_not_know_an_argument_is_a_version_refusal(tmp_path):
    body = "echo \"TypeError: _run() got an unexpected keyword argument 'inputs_from'\" >&2; exit 1"
    result, _ = worker.call(fake_interpreter(tmp_path, body), "run", {"inputs_from": "a:b"}, timeout=5)
    assert result["refused"] == "version"
    assert "does not know the argument 'inputs_from'" in result["error"]


@pytest.mark.skipif(__import__("os").name == "nt", reason="fake interpreters are shell scripts")
def test_arguments_nobody_gave_are_not_sent_to_a_worker(tmp_path, monkeypatch):
    """So a plain call still works against a project pinned to an older SmartMDAO."""
    import json
    from smartmdao.mcp import handlers

    interpreter = fake_interpreter(tmp_path, 'echo \'{"protocol": 1, "smartmdao": "1.27.0", "result": {"ok": true}}\'')
    monkeypatch.setattr(handlers, "resolve", lambda *args: (interpreter, None))

    assert run_pipeline("model.py", inputs={"a": 1.0})["ok"] is True
    sent = json.loads((tmp_path / "request.json").read_text())["args"]
    assert "inputs_from" not in sent and "inputs_args" not in sent and "python" not in sent


# ==============================================================================
# Literal and Protocol (F-018)
# ==============================================================================

Method = Literal["steiner", "shortest"]


class Shape(Protocol):
    def area(self) -> float: ...


@pytest.mark.parametrize("value, ok", [("steiner", True), ("dijkstra", False), (3, False)])
def test_a_literal_is_checked_on_its_values(value, ok):
    assert StandardTypeChecker().check_value(value, Method) is ok


def test_a_literal_option_must_match_in_type_too():
    """1 == True in Python; Literal[1] still does not accept True."""
    assert not StandardTypeChecker().check_value(True, Literal[1])
    assert StandardTypeChecker().check_value(None, Optional[Method])


@pytest.mark.parametrize("produced, expected, ok", [
    (Method, Method, True),
    (Literal["steiner"], Method, True),
    (Method, Literal["steiner"], False),
    (Method, str, True),
    (str, Method, False),
    (Union[Literal["steiner"], Literal["shortest"]], Method, True),
])
def test_a_literal_fits_within_a_wider_one(produced, expected, ok):
    assert StandardTypeChecker().check_types(produced, expected) is ok


def test_a_literal_input_no_longer_crashes_a_run():
    """Until 1.28.0 every value raised TypeError, valid ones included."""
    from smartmdao import TypeMismatchError

    pipeline = Pipeline(inputs=["method"])

    @pipeline.step(outputs=["cost"])
    def route(method: Method) -> float:
        return 1.0

    assert pipeline.run(method="steiner")["cost"] == 1.0
    with pytest.raises(TypeMismatchError, match="steiner"):
        pipeline.run(method="dijkstra")


def test_a_wider_literal_into_a_narrower_one_is_a_finding():
    pipeline = Pipeline(inputs=["seed"])

    @pipeline.step(outputs=["method"])
    def choose(seed: float) -> Method:
        return "steiner"

    @pipeline.step(outputs=["cost"])
    def route(method: Literal["steiner"]) -> float:
        return 1.0

    assert [f.code for f in validate(pipeline)] == ["type-mismatch"]


def test_two_consumers_with_disjoint_options_are_a_finding():
    pipeline = Pipeline(inputs=["method"])

    @pipeline.step(outputs=["a"])
    def one(method: Literal["steiner"]) -> float:
        return 1.0

    @pipeline.step(outputs=["b"])
    def other(method: Literal["shortest"]) -> float:
        return 1.0

    assert "type-mismatch" in [f.code for f in validate(pipeline)]

    overlapping = Pipeline(inputs=["method"])
    overlapping.add(one, outputs=["a"])

    def wide(method: Method) -> float:
        return 1.0

    overlapping.add(wide, outputs=["b"])
    assert "type-mismatch" not in [f.code for f in validate(overlapping)]


def test_a_protocol_that_isinstance_cannot_test_is_unchecked():
    """Invariant 2: type information that cannot be checked is never an error."""
    pipeline = Pipeline(inputs=["shape"])

    @pipeline.step(outputs=["size"])
    def measure(shape: Shape) -> float:
        return 1.0

    assert pipeline.run(shape=object())["size"] == 1.0
    assert StandardTypeChecker().check_types(Shape, Shape)


# ==============================================================================
# F-014 and F-010
# ==============================================================================

def test_a_runs_stderr_has_no_runpy_warning(tmp_path):
    result = run_pipeline(str(write(tmp_path / "probe.py", FLOAT_PROBE)), inputs={"x_km": 2.5})
    assert "RuntimeWarning" not in result.get("stderr", "")


def test_every_tool_taking_a_path_advertises_python_and_project():
    from smartmdao.mcp import create_server

    async def schemas():
        return {tool.name: tool.input_schema for tool in await create_server().list_tools()}

    for name, schema in asyncio.run(schemas()).items():
        properties = set(schema.get("properties", {}))
        if "path" in properties:
            assert {"python", "project"} <= properties, name


# ==============================================================================
# The same paths in-process - the run process is not measured by coverage
# ==============================================================================

@pytest.mark.parametrize("spec, says", [
    ("baseline", "must look like"),
    ("baseline:nope", "could not be found"),
    ("baseline:not_a_mapping", "must return a mapping"),
])
def test_build_inputs_reports_what_it_cannot_do(structured, spec, says):
    with pytest.raises(InputsFromError, match=says):
        build_inputs(spec, None, None, Path(structured))


def test_the_run_process_builds_inputs_before_running(structured):
    from smartmdao.mcp._runner import run

    built = run({"path": structured, "rung": "full", "inputs_from": "baseline:inputs",
                 "inputs_args": ["small"], "fallback_inputs": {"weights": {9.0: 9.0}}})
    assert built["ok"] is True and built["state"]["area"] == 18.0     # project beats fallback
    assert built["built_inputs"] == ["bbox", "weights"]

    refused = run({"path": structured, "rung": "full", "inputs_from": "baseline:nope"})
    assert refused["ok"] is False and "could not be found" in refused["error"]
