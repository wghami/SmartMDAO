"""
Running only what an output needs: `Pipeline.upstream(*outputs)`. Roadmap 6.16
(F-019).

The result is an ordinary pipeline, planned by the same planner, so analyze,
validate, run and visualize work on it unchanged.
"""
import pytest

from smartmdao import Bands, Discretisation, HybridSolver, Pipeline, analyze, validate
from smartmdao.mcp.handlers import run_pipeline


def branches():
    calls = []
    pipeline = Pipeline(solver=HybridSolver(), inputs=["a", "slow_input", "y2"])

    @pipeline.step(outputs=["y1"])
    def loop_one(a: float, y2: float) -> float:
        calls.append("loop_one"); return a + 0.1 * y2

    @pipeline.step(outputs=["y2"])
    def loop_two(y1: float) -> float:
        calls.append("loop_two"); return y1 / 2

    @pipeline.step(outputs=["report"])
    def summarise(y1: float) -> float:
        calls.append("summarise"); return y1

    @pipeline.step(outputs=["slow"])
    def expensive(slow_input: float) -> float:
        calls.append("expensive"); raise RuntimeError("should not run")

    return pipeline, calls


def test_only_the_needed_steps_are_kept_and_a_loop_stays_whole():
    pipeline, _ = branches()
    smaller = pipeline.upstream("report")

    assert [s.name for s in smaller.steps] == ["loop_one", "loop_two", "summarise"]
    assert smaller.inputs == ("a", "y2")                  # slow_input is no longer needed
    assert len(analyze(smaller).cycles) == 1
    assert validate(smaller) == ()


def test_running_it_never_touches_the_rest():
    pipeline, calls = branches()
    result = pipeline.upstream("report").run(a=1.0, y2=0.0)

    assert "expensive" not in calls
    assert result["report"] == pytest.approx(pipeline.upstream("y1").run(a=1.0, y2=0.0)["y1"])


def test_the_original_is_untouched_and_the_solver_is_a_copy():
    pipeline, _ = branches()
    smaller = pipeline.upstream("y2")

    assert len(pipeline.steps) == 4
    assert smaller.solver is not pipeline.solver
    assert smaller.steps[0] is pipeline.steps[0]          # steps are shared, not copied


def test_bands_are_kept_only_where_something_kept_reads_them():
    pipeline = Pipeline(inputs=["mass", "power"], discretisation=Discretisation(
        mass_band=Bands("mass", edges=[10.0], names=["light", "heavy"]),
        power_band=Bands("power", edges=[5.0], names=["low", "high"]),
    ))

    @pipeline.step(outputs=["verdict"])
    def judge(mass_band: str) -> str:
        return mass_band

    @pipeline.step(outputs=["budget"])
    def plan(power_band: str) -> str:
        return power_band

    smaller = pipeline.upstream("verdict")
    assert list(smaller.discretisation.bands) == ["mass_band"]
    assert smaller.inputs == ("mass",)
    assert smaller.run(mass=12.0)["verdict"] == "heavy"


@pytest.mark.parametrize("outputs, says", [((), "at least one"), (("nope",), "No step produces")])
def test_what_cannot_be_asked_is_refused(outputs, says):
    pipeline, _ = branches()
    with pytest.raises(ValueError, match=says):
        pipeline.upstream(*outputs)


def test_run_pipeline_targets_over_mcp(tmp_path):
    source = tmp_path / "branches.py"
    source.write_text(
        "from smartmdao import Pipeline\n"
        "pipeline = Pipeline(inputs=['a', 'slow_input'])\n"
        "@pipeline.step(outputs=['b'])\n"
        "def cheap(a: float) -> float: return a * 2\n"
        "@pipeline.step(outputs=['c'])\n"
        "def expensive(slow_input: float) -> float: raise RuntimeError('should not run')\n"
    )
    result = run_pipeline(str(source), inputs={"a": 1.5}, targets=["b"], rung="full")
    assert result["ok"] is True and result["state"] == {"a": 1.5, "b": 3.0}
    assert "declared_not_supplied" not in result["inputs_used"]

    refused = run_pipeline(str(source), inputs={"a": 1.5}, targets=["nope"])
    assert refused["ok"] is False and "No step produces" in refused["error"]


def test_the_run_process_builds_the_smaller_pipeline_itself(tmp_path):
    from smartmdao.mcp._runner import run

    source = tmp_path / "branches.py"
    source.write_text(
        "from smartmdao import Pipeline\n"
        "pipeline = Pipeline(inputs=['a', 'slow_input'])\n"
        "@pipeline.step(outputs=['b'])\n"
        "def cheap(a: float) -> float: return a * 2\n"
        "@pipeline.step(outputs=['c'])\n"
        "def expensive(slow_input: float) -> float: raise RuntimeError('should not run')\n"
    )
    assert run({"path": str(source), "rung": "full", "inputs": {"a": 1.0}, "targets": ["b"]})["ok"] is True
