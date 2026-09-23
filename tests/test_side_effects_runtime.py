"""
Phase 5.2: what actually happens at run time to a step that touches the world.

Four behaviours, each with a counter so the claim is measured rather than
inferred from a return value:

  * `effects=True` that would repeat is refused **before anything executes**.
  * `effects="once"` executes once per `run()`, and its output is frozen for
    the rest of that run.
  * Anything that multiplies runs - an optimizer, `compare_runs` - refuses any
    declared effect unless `allow_effects=True`.
  * Loading a file for analysis no longer executes its top-level `run()`.
"""
import threading

import pytest

from smartmdao import (
    DAGSolver,
    HybridSolver,
    IterativeSolver,
    Pipeline,
    PipelineEvaluator,
    SideEffectError,
)
from smartmdao.effects import repeating_step_names


def mass_loop(effects, solver=None):
    """A converging two-step loop whose `notify` step declares `effects`."""
    calls = {"notify": 0}
    pipeline = Pipeline(solver=solver or HybridSolver(max_iterations=50))

    @pipeline.step(outputs=["notified"], effects=effects)
    def notify(mass: float) -> float:
        calls["notify"] += 1
        return mass

    @pipeline.step(outputs=["mass"])
    def size(notified: float) -> float:
        return notified * 0.5 + 10.0

    return pipeline, calls


# ==============================================================================
# Refusal
# ==============================================================================

def test_effects_true_in_a_loop_is_refused_before_anything_runs():
    pipeline, calls = mass_loop(True)

    with pytest.raises(SideEffectError, match="Nothing has been executed"):
        pipeline.run(mass=0.0)

    assert calls["notify"] == 0


def test_the_refusal_says_how_to_proceed():
    pipeline, _ = mass_loop(True)

    with pytest.raises(SideEffectError) as caught:
        pipeline.run(mass=0.0)

    message = str(caught.value)
    assert 'effects="once"' in message
    assert 'effects="every-sweep"' in message
    assert "side-effect-in-cycle" in message


def test_the_refusal_is_a_runtime_error():
    """Catchable as the ordinary thing it is, not a bespoke hierarchy."""
    assert issubclass(SideEffectError, RuntimeError)


def test_every_sweep_is_allowed_and_really_does_repeat():
    pipeline, calls = mass_loop("every-sweep")
    result = pipeline.run(mass=0.0)

    assert result["convergence_reports"][0].status == "converged"
    assert calls["notify"] > 1


def test_effects_true_outside_any_loop_runs_once_and_is_not_refused():
    calls = {"n": 0}
    pipeline = Pipeline(solver=HybridSolver())

    @pipeline.step(outputs=["summary"])
    def summarise(data: float) -> str:
        return str(data)

    @pipeline.step(outputs=["path"], effects=True)
    def write_report(summary: str) -> str:
        calls["n"] += 1
        return "out.pdf"

    assert pipeline.run(data=1.0)["path"] == "out.pdf"
    assert calls["n"] == 1


def test_iterative_solver_is_refused_even_without_a_cycle():
    """The question is *would it repeat*, and IterativeSolver repeats everything."""
    calls = {"n": 0}
    pipeline = Pipeline(solver=IterativeSolver(max_iterations=5))

    @pipeline.step(outputs=["y"], effects=True)
    def send(x: float) -> float:
        calls["n"] += 1
        return x

    with pytest.raises(SideEffectError, match="IterativeSolver"):
        pipeline.run(x=1.0)
    assert calls["n"] == 0


def test_several_unstated_steps_are_named_together():
    pipeline = Pipeline(solver=IterativeSolver(max_iterations=3))
    pipeline.add(lambda x: x, outputs=["a"], effects=True)

    def second(a: float) -> float:
        return a

    pipeline.add(second, outputs=["b"], effects=True)

    with pytest.raises(SideEffectError, match="have side effects"):
        pipeline.run(x=1.0)


# ==============================================================================
# The latch
# ==============================================================================

def test_once_executes_exactly_once_per_run():
    pipeline, calls = mass_loop("once")
    result = pipeline.run(mass=0.0)

    assert calls["notify"] == 1
    assert result["convergence_reports"][0].status == "converged"


def test_once_freezes_the_output_for_the_rest_of_the_run():
    """
    The real semantic cost of `"once"`, and why it is asked for rather than a
    default: the loop converges against the value from the first sweep.
    """
    pipeline, _ = mass_loop("once")
    result = pipeline.run(mass=0.0)

    # notify returned the seed (0.0) on sweep 1 and never again, so size()
    # settled at 0.0 * 0.5 + 10.0 rather than the loop's true fixed point, 20.
    assert result["notified"] == 0.0
    assert result["mass"] == 10.0


def test_a_second_run_is_a_second_study():
    pipeline, calls = mass_loop("once")
    pipeline.run(mass=0.0)
    pipeline.run(mass=0.0)

    assert calls["notify"] == 2


def test_the_latch_is_invisible_to_wiring_and_type_checking():
    """functools.wraps keeps the real signature reachable through unwrap."""
    calls = {"n": 0}
    pipeline = Pipeline(solver=HybridSolver(), runtime_type_checks=True)

    @pipeline.step(outputs=["notified"], effects="once")
    def notify(mass: float) -> float:
        calls["n"] += 1
        return mass

    @pipeline.step(outputs=["mass"])
    def size(notified: float) -> float:
        return notified * 0.5 + 10.0

    pipeline.run(mass=4.0)
    assert calls["n"] == 1


# ==============================================================================
# Multiplied runs: the optimizer
# ==============================================================================

def test_an_optimizer_refuses_a_pipeline_that_declares_effects():
    pipeline, calls = mass_loop("once")

    with pytest.raises(SideEffectError, match="once per evaluation"):
        PipelineEvaluator(pipeline, design_vars=["mass"])

    assert calls["notify"] == 0


def test_every_sweep_does_not_excuse_an_optimizer():
    """Both declarations are scoped to one run; an optimizer makes hundreds."""
    pipeline, _ = mass_loop("every-sweep")

    with pytest.raises(SideEffectError, match="allow_effects=True"):
        PipelineEvaluator(pipeline, design_vars=["mass"])


def test_allow_effects_is_the_explicit_yes():
    pipeline, calls = mass_loop("every-sweep")
    evaluator = PipelineEvaluator(pipeline, design_vars=["mass"], allow_effects=True)

    evaluator.evaluate([0.0])
    assert evaluator.allow_effects is True
    assert calls["notify"] > 0


def test_a_pure_pipeline_needs_no_permission():
    pipeline = Pipeline()

    @pipeline.step(outputs=["y"])
    def square(x: float) -> float:
        return x * x

    assert PipelineEvaluator(pipeline, design_vars=["x"]).evaluate([3.0])["y"] == 9.0


# ==============================================================================
# Which steps repeat
# ==============================================================================

def test_a_dag_solver_repeats_nothing():
    pipeline, _ = mass_loop(True, solver=DAGSolver())
    assert repeating_step_names(pipeline.solver, pipeline.steps, {"mass"}) == set()


def test_a_custom_solver_is_read_conservatively_as_following_the_graph():
    class MySolver:
        def solve(self, steps, inputs, type_checker=None):  # pragma: no cover
            return {}

    pipeline, _ = mass_loop(True, solver=MySolver())
    assert repeating_step_names(pipeline.solver, pipeline.steps, {"mass"}) == {
        "notify",
        "size",
    }


# ==============================================================================
# Loading a file must not run it
# ==============================================================================

TOUCHES = '''
import pathlib
from smartmdao import Pipeline

LOG = pathlib.Path(__file__).with_name("touched.log")

pipeline = Pipeline()

@pipeline.step(outputs=["y"], effects="once")
def send(x: float) -> float:
    with LOG.open("a") as handle:
        handle.write("fired\\n")
    return x

pipeline.run(x=1.0)
'''


def fired(directory):
    log = directory / "touched.log"
    return log.read_text().count("fired") if log.exists() else 0


def test_analysing_a_file_does_not_run_its_module_level_pipeline(tmp_path):
    """
    Measured before the fix: two static calls fired the side effect twice. The
    loader has to import the file, and importing ran its top-level run().
    """
    from smartmdao.mcp.handlers import analyze_pipeline, validate_pipeline

    path = tmp_path / "touches.py"
    path.write_text(TOUCHES)

    validate_pipeline(str(path))
    analyze_pipeline(str(path))

    assert fired(tmp_path) == 0


def test_run_pipeline_now_runs_it_once_not_twice(tmp_path):
    """The child used to execute it at import AND again explicitly."""
    from smartmdao.mcp.handlers import run_pipeline

    path = tmp_path / "touches.py"
    path.write_text(TOUCHES)

    assert run_pipeline(str(path))["ok"] is True
    assert fired(tmp_path) == 1


def test_using_the_result_of_a_suspended_run_is_explained(tmp_path):
    from smartmdao.mcp.loader import PipelineLoadError, load_pipeline

    path = tmp_path / "reads_result.py"
    path.write_text(
        "from smartmdao import Pipeline\n"
        "pipeline = Pipeline()\n"
        "@pipeline.step(outputs=['y'])\n"
        "def double(x: float) -> float:\n"
        "    return x * 2\n"
        "result = pipeline.run(x=1.0)\n"
        "print(result['y'])\n"
    )

    with pytest.raises(PipelineLoadError, match='if __name__ == "__main__"'):
        load_pipeline(str(path))


def test_the_placeholder_refuses_every_kind_of_use():
    from smartmdao.core import ExecutionSuspended, _NotExecuted

    placeholder = _NotExecuted()
    assert "not executed" in repr(placeholder)

    for use in (lambda: placeholder["y"], lambda: placeholder.keys, lambda: list(placeholder)):
        with pytest.raises(ExecutionSuspended):
            use()


def test_suspension_is_restored_and_thread_local():
    from smartmdao.core import _suspension, suspend_execution

    seen_elsewhere = []

    with suspend_execution():
        assert _suspension.active is True
        other = threading.Thread(
            target=lambda: seen_elsewhere.append(getattr(_suspension, "active", False))
        )
        other.start()
        other.join()

    assert _suspension.active is False
    assert seen_elsewhere == [False]


# ==============================================================================
# Multiplied runs: compare_runs
# ==============================================================================

DECLARES = '''
from smartmdao import Pipeline

def build_pipeline() -> Pipeline:
    pipeline = Pipeline()

    @pipeline.step(outputs=["y"], effects="every-sweep")
    def send(x: float) -> float:
        return x

    return pipeline
'''


def test_compare_runs_refuses_files_that_declare_effects(tmp_path):
    from smartmdao.mcp.handlers import compare_runs

    a = tmp_path / "a.py"
    a.write_text(DECLARES)

    result = compare_runs(str(a), str(a), inputs={"x": 1.0})

    assert result["ok"] is False
    assert result["refused"] == "side-effects"
    assert "twice, once per file" in result["error"]
    assert "runs" not in result


def test_compare_runs_accepts_them_when_told_to(tmp_path):
    from smartmdao.mcp.handlers import compare_runs

    a = tmp_path / "a.py"
    a.write_text(DECLARES)

    result = compare_runs(str(a), str(a), inputs={"x": 1.0}, allow_effects=True)

    assert result["ok"] is True
    assert result["match"] is True
