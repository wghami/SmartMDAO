"""
Stub steps: declared, not yet written. Roadmap 6.2.

A contract-first pipeline starts as signatures whose bodies only raise
`NotImplementedError`. Until now, analyze and explain showed those exactly like
finished disciplines, and progress was tracked in a list kept by hand.

The check reads source with `ast` and never calls the function. A step whose
source cannot be read is *unknown* - never reported as a stub (invariant 2).
"""
import pytest

from smartmdao import (
    HybridSolver,
    MemoryBackend,
    Pipeline,
    analyze,
    cached,
    explain,
    validate,
)
from smartmdao.analysis import stub_status
from smartmdao.mcp.handlers import analyze_pipeline, run_pipeline
from smartmdao.models import Step


def status(fn):
    return stub_status(Step(fn, ["out"]))


# ==============================================================================
# What counts as a stub
# ==============================================================================

def test_a_bare_raise_is_a_stub():
    def step(x: float) -> float:
        raise NotImplementedError

    assert status(step) is True


def test_a_raise_with_a_message_and_a_docstring_is_a_stub():
    def step(x: float) -> float:
        """Lift from the vortex-lattice model."""
        raise NotImplementedError("waiting on the aero team")

    assert status(step) is True


def test_an_async_stub_is_a_stub():
    async def step(x: float) -> float:
        raise NotImplementedError

    assert status(step) is True


def test_a_stub_is_seen_through_decorators():
    """`@cached` wraps the function; the check reads the one underneath."""
    @cached(MemoryBackend())
    def step(x: float) -> float:
        raise NotImplementedError

    assert status(step) is True


@pytest.mark.parametrize("body", [
    "return x",
    "pass",                                           # returns None: a bug, not a stub
    "...",
    "raise ValueError('no')",
    "raise",
    "y = x\n    raise NotImplementedError",           # does work first
    "'''Only a docstring.'''",
])
def test_anything_else_is_not(tmp_path, body):
    module = tmp_path / "shapes.py"
    module.write_text(f"def step(x):\n    {body}\n")
    namespace = {}
    exec(compile(module.read_text(), str(module), "exec"), namespace)
    import linecache
    linecache.checkcache(str(module))

    assert status(namespace["step"]) is False


def test_a_lambda_is_never_a_stub():
    assert status(lambda x: x) is False


# ==============================================================================
# Unknown is not a stub
# ==============================================================================

def test_a_function_made_by_exec_is_unknown():
    namespace = {}
    exec("def step(x):\n    raise NotImplementedError\n", namespace)

    assert status(namespace["step"]) is None


def test_a_builtin_is_unknown():
    assert status(len) is None


def test_a_class_used_as_a_step_is_unknown():
    class Discipline:
        def __init__(self, x: float):
            self.x = x

    assert status(Discipline) is None


def test_unknown_steps_are_not_listed_as_stubs():
    namespace = {}
    exec("def step(x: float) -> float:\n    raise NotImplementedError\n", namespace)
    pipeline = Pipeline(inputs=["x"])
    pipeline.add(namespace["step"], outputs=["y"])

    assert analyze(pipeline).stubs == ()


# ==============================================================================
# Where they are reported
# ==============================================================================

def half_written():
    pipeline = Pipeline(solver=HybridSolver(), inputs=["z", "x", "y2"])

    @pipeline.step(outputs=["y1"])
    def aerodynamics(z: float, x: float, y2: float) -> float:
        raise NotImplementedError

    @pipeline.step(outputs=["y2"])
    def structures(z: float, y1: float) -> float:
        return z + y1

    @pipeline.step(outputs=["cost"])
    def costing(y2: float) -> float:
        """Not yet."""
        raise NotImplementedError("needs the supplier's rates")

    return pipeline


def test_analyze_lists_the_stubs_in_step_order():
    assert analyze(half_written()).stubs == ("aerodynamics", "costing")


def test_validate_reports_each_as_information():
    findings = [f for f in validate(half_written()) if f.code == "stub-step"]

    assert [f.step for f in findings] == ["aerodynamics", "costing"]
    assert {f.severity for f in findings} == {"info"}


def test_a_stubbed_pipeline_is_still_valid():
    """Contract-first is stubs on purpose; nothing else about it is wrong."""
    assert {f.severity for f in validate(half_written())} == {"info"}


def test_explain_summarises_them_once():
    text = explain(half_written())

    assert "Not written yet (2 of 3 steps" in text
    assert "aerodynamics, costing" in text
    assert "stub" not in text.split("Not written yet")[1]      # no per-step repeat


# ==============================================================================
# Over MCP
# ==============================================================================

HALF_WRITTEN_FILE = '''
from smartmdao import Pipeline

pipeline = Pipeline(inputs=["a"])

@pipeline.step(outputs=["b"])
def double(a: float) -> float:
    return a * 2

@pipeline.step(outputs=["c"])
def later(b: float) -> float:
    raise NotImplementedError
'''


@pytest.fixture
def half_written_file(tmp_path):
    path = tmp_path / "half.py"
    path.write_text(HALF_WRITTEN_FILE)
    return path


def test_analyze_pipeline_lists_stubs(half_written_file):
    assert analyze_pipeline(str(half_written_file))["stubs"] == ["later"]


def test_run_pipeline_names_the_stub_it_will_stop_at(half_written_file):
    """
    Named, not refused: the run still happens, so the steps already written are
    exercised. Without this the cause was only in stderr - the executor reports
    the failure as a RuntimeError.
    """
    result = run_pipeline(str(half_written_file), inputs={"a": 1.0})

    assert result["ok"] is False
    assert result["stubs"] == ["later"]
    assert "first sweep" in result["stubs_note"]


def test_run_pipeline_says_nothing_about_stubs_when_there_are_none(tmp_path):
    path = tmp_path / "done.py"
    path.write_text(HALF_WRITTEN_FILE.replace("raise NotImplementedError", "return b + 1"))
    result = run_pipeline(str(path), inputs={"a": 1.0})

    assert result["ok"] is True
    assert "stubs" not in result
