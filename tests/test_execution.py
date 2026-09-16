"""Running a pipeline in a child process, under a wall clock.

The argument for the subprocess is reliability, not safety — the agent driving
this connector already has a shell. What it buys is a hard kill for a solve
that will not stop, typed results instead of scraped stdout, and survival when
a discipline crashes rather than raises. Each of those is tested below, because
each is the reason the extra process exists.
"""
import json

import pytest

from smartmdao.mcp._runner import PREVIEW_ITEMS, run, summarise
from smartmdao.mcp.execution import (
    DEFAULT_BUDGET_SWEEPS,
    estimate_from_smoke,
    run_in_subprocess,
)
from smartmdao.mcp.handlers import run_pipeline

CONVERGING = '''
from smartmdao import Pipeline, HybridSolver

pipeline = Pipeline(solver=HybridSolver(max_iterations=100, tolerance=1e-9))

@pipeline.step(outputs=["y1"])
def discipline_1(z: float, y2: float) -> float:
    return 0.5 * y2 + z

@pipeline.step(outputs=["y2"])
def discipline_2(y1: float) -> float:
    return 0.5 * y1 + 1.0

pipeline.run(z=1.0, y2=1.0)
'''


@pytest.fixture
def converging(tmp_path):
    path = tmp_path / "converging.py"
    path.write_text(CONVERGING)
    return path


# --- summarise ----------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [(None, None), (True, True), (3, 3), ("x", "x"), (1.5, 1.5)],
)
def test_scalars_pass_through(value, expected):
    assert summarise(value) == expected


def test_non_finite_floats_survive_json():
    """A diverged residual is exactly when you want to see `inf`."""
    assert summarise(float("inf")) == "inf"
    assert summarise(float("-inf")) == "-inf"
    assert summarise(float("nan")) == "nan"
    json.dumps(summarise(float("inf")))          # must not raise


def test_long_lists_are_truncated():
    summarised = summarise(list(range(PREVIEW_ITEMS + 5)))
    assert len(summarised) == PREVIEW_ITEMS + 1
    assert summarised[-1] == "... 5 more omitted"


def test_sets_are_ordered_so_output_is_stable():
    assert summarise({"b", "a"}) == ["a", "b"]


def test_numpy_arrays_become_shape_stats_and_a_preview():
    numpy = pytest.importorskip("numpy")

    summarised = summarise(numpy.arange(100, dtype=float))
    assert summarised["type"] == "array"
    assert summarised["shape"] == [100]
    assert summarised["min"] == 0.0 and summarised["max"] == 99.0
    assert len(summarised["preview"]) == PREVIEW_ITEMS
    assert summarised["omitted"] == 90


def test_a_small_array_has_no_omitted_count():
    numpy = pytest.importorskip("numpy")
    assert "omitted" not in summarise(numpy.zeros(3))


def test_dataclasses_are_expanded():
    from smartmdao import ABANDONED, ConvergenceReport

    summarised = summarise(
        ConvergenceReport(status=ABANDONED, iterations=4, residuals=(1.0,), reason="x")
    )
    assert summarised["status"] == ABANDONED
    assert summarised["iterations"] == 4


def test_anything_else_becomes_a_bounded_repr():
    class Opaque:
        def __repr__(self):
            return "O" * 500

    assert len(summarise(Opaque())) == 200


def test_dicts_recurse_and_keys_become_strings():
    assert summarise({1: [1, 2]}) == {"1": [1, 2]}


# --- the child, called directly -----------------------------------------------

def test_an_unknown_rung_is_rejected(converging):
    result = run({"path": str(converging), "rung": "warp-speed"})
    assert result["ok"] is False
    assert "Unknown rung" in result["error"]


def test_a_load_failure_comes_back_as_data(tmp_path):
    result = run({"path": str(tmp_path / "absent.py")})
    assert result["ok"] is False
    assert "No such file" in result["error"]


def test_smoke_caps_the_solver_to_one_sweep(converging):
    result = run({"path": str(converging), "rung": "smoke", "inputs": {"z": 1.0, "y2": 1.0}})

    assert result["rung_detail"] == {
        "applied": True,
        "max_iterations": 1,
        "solver_default": 100,
    }
    assert result["converged"] is False
    assert result["convergence_reports"][0]["status"] == "max_iterations"


def test_budgeted_caps_to_the_requested_sweeps(converging):
    result = run(
        {
            "path": str(converging),
            "rung": "budgeted",
            "budget_sweeps": 3,
            "inputs": {"z": 1.0, "y2": 1.0},
        }
    )
    assert result["rung_detail"]["max_iterations"] == 3


def test_a_budget_above_the_solvers_own_ceiling_does_not_raise_it():
    """The engineer's budget caps; it never grants more than the file allows."""
    source = CONVERGING.replace("max_iterations=100", "max_iterations=5")
    import tempfile, pathlib

    path = pathlib.Path(tempfile.mkdtemp()) / "low.py"
    path.write_text(source)

    result = run({"path": str(path), "rung": "budgeted", "budget_sweeps": 999,
                  "inputs": {"z": 1.0, "y2": 1.0}})
    assert result["rung_detail"]["max_iterations"] == 5


def test_full_leaves_the_solver_alone_and_converges(converging):
    result = run({"path": str(converging), "rung": "full", "inputs": {"z": 1.0, "y2": 1.0}})

    assert result["rung_detail"]["applied"] is False
    assert result["converged"] is True
    assert result["state"]["y1"] == pytest.approx(2.0, abs=1e-6)


def test_an_acyclic_pipeline_says_every_rung_is_the_same(tmp_path):
    path = tmp_path / "acyclic.py"
    path.write_text(
        "from smartmdao import Pipeline\n"
        "pipeline = Pipeline()\n"
        "@pipeline.step(outputs=['b'])\n"
        "def double(a: float) -> float: return a * 2\n"
    )
    result = run({"path": str(path), "rung": "smoke", "inputs": {"a": 2.0}})

    assert result["rung_detail"]["applied"] is False
    assert "does not iterate" in result["rung_detail"]["reason"]
    assert result["state"]["b"] == 4.0


def test_per_step_timings_reveal_which_steps_iterated(converging):
    result = run({"path": str(converging), "rung": "full", "inputs": {"z": 1.0, "y2": 1.0}})

    assert result["per_step"]["discipline_1"]["calls"] > 1
    assert result["per_step"]["discipline_1"]["total_seconds"] >= 0


def test_a_raising_discipline_returns_a_traceback_not_an_exception(tmp_path):
    path = tmp_path / "raises.py"
    path.write_text(
        "from smartmdao import Pipeline\n"
        "pipeline = Pipeline()\n"
        "@pipeline.step(outputs=['b'])\n"
        "def boom(a: float) -> float: raise ValueError('bad physics')\n"
    )
    result = run({"path": str(path), "inputs": {"a": 1.0}})

    assert result["ok"] is False
    assert "bad physics" in result["error"]
    assert "ValueError" in result["traceback"]
    assert "elapsed_seconds" in result


# --- the parent: timeout, crash, and the contract -----------------------------

def test_a_real_run_through_the_subprocess(converging):
    result = run_in_subprocess(str(converging), inputs={"z": 1.0, "y2": 1.0}, rung="full")
    assert result["ok"] is True
    assert result["state"]["y1"] == pytest.approx(2.0, abs=1e-6)


def test_a_solve_that_will_not_stop_is_killed(tmp_path):
    path = tmp_path / "slow.py"
    path.write_text(
        "import time\n"
        "from smartmdao import Pipeline, IterativeSolver\n"
        "pipeline = Pipeline(solver=IterativeSolver(max_iterations=100000, target_var='x'))\n"
        "@pipeline.step(outputs=['x'])\n"
        "def never(x: float) -> float:\n"
        "    time.sleep(0.05)\n"
        "    return x + 1.0\n"
    )
    result = run_in_subprocess(str(path), inputs={"x": 0.0}, rung="full", timeout_seconds=1.0)

    assert result["ok"] is False
    assert result["timed_out"] is True
    assert "may not be converging" in result["error"]


def test_a_discipline_that_crashes_does_not_take_the_server_with_it(tmp_path):
    """The whole reason for the extra process."""
    path = tmp_path / "crash.py"
    path.write_text(
        "import os\n"
        "from smartmdao import Pipeline\n"
        "pipeline = Pipeline()\n"
        "@pipeline.step(outputs=['b'])\n"
        "def hard_exit(a: float) -> float: os._exit(139)\n"
    )
    result = run_in_subprocess(str(path), inputs={"a": 1.0})

    assert result["ok"] is False
    assert "exit code 139" in result["error"]
    assert "crash rather than an exception" in result["error"]


def test_output_that_is_not_json_is_reported(monkeypatch, converging):
    import subprocess

    class Garbage:
        returncode = 0
        stdout = "not json at all"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Garbage())
    result = run_in_subprocess(str(converging))

    assert result["ok"] is False
    assert "not valid JSON" in result["error"]
    assert result["stdout"] == "not json at all"


def test_discipline_logging_is_kept_rather_than_discarded(tmp_path):
    path = tmp_path / "noisy.py"
    path.write_text(
        "import sys\n"
        "from smartmdao import Pipeline\n"
        "pipeline = Pipeline()\n"
        "@pipeline.step(outputs=['b'])\n"
        "def chatty(a: float) -> float:\n"
        "    print('working', file=sys.stderr)\n"
        "    return a\n"
    )
    result = run_in_subprocess(str(path), inputs={"a": 1.0})

    assert result["ok"] is True
    assert "working" in result["stderr"]


def test_an_unknown_rung_is_rejected_before_spawning(converging):
    assert run_in_subprocess(str(converging), rung="nope")["ok"] is False


def test_a_non_positive_timeout_is_rejected(converging):
    result = run_in_subprocess(str(converging), timeout_seconds=0)
    assert result["ok"] is False
    assert "must be positive" in result["error"]


# --- the cost estimate --------------------------------------------------------

def test_a_smoke_run_quotes_what_a_full_run_might_cost():
    result = {
        "ok": True,
        "rung": "smoke",
        "elapsed_seconds": 0.5,
        "rung_detail": {"solver_default": 100},
    }
    estimate = estimate_from_smoke(result)

    assert estimate["one_sweep_seconds"] == 0.5
    assert estimate["budgeted_worst_case_seconds"] == 0.5 * DEFAULT_BUDGET_SWEEPS
    assert estimate["full_worst_case_seconds"] == 50.0
    assert "Quote this" in estimate["note"]


def test_a_sweep_too_fast_to_measure_says_so():
    estimate = estimate_from_smoke(
        {"ok": True, "rung": "smoke", "elapsed_seconds": 1e-9,
         "rung_detail": {"solver_default": 100}}
    )
    assert "not a concern" in estimate["note"]
    assert "full_worst_case_seconds" not in estimate


def test_an_acyclic_smoke_run_has_no_sweep_ceiling_to_project_from():
    estimate = estimate_from_smoke(
        {"ok": True, "rung": "smoke", "elapsed_seconds": 0.5, "rung_detail": {}}
    )
    assert "full_worst_case_seconds" not in estimate
    assert estimate["budgeted_worst_case_seconds"] == 0.5 * DEFAULT_BUDGET_SWEEPS


@pytest.mark.parametrize(
    "result",
    [
        {"ok": False, "rung": "smoke"},
        {"ok": True, "rung": "full", "elapsed_seconds": 1.0},
    ],
)
def test_no_estimate_from_a_failure_or_a_larger_rung(result):
    assert estimate_from_smoke(result) is None


# --- the handler --------------------------------------------------------------

def test_the_handler_defaults_to_the_cheapest_rung(converging):
    """An unbounded run should never be the thing that happens by accident."""
    assert run_pipeline(str(converging))["rung"] == "smoke"


def test_the_handler_recovers_input_values_from_the_file(converging):
    """`run_pipeline(path)` behaves the way running the script does."""
    result = run_pipeline(str(converging), rung="full")

    assert result["ok"] is True
    assert result["inputs_used"]["supplied"] == []
    assert set(result["inputs_used"]["found_in_source"]) == {"z", "y2"}
    assert result["converged"] is True


def test_caller_supplied_inputs_win_over_the_file(converging):
    result = run_pipeline(str(converging), inputs={"z": 10.0}, rung="full")

    assert result["inputs_used"]["supplied"] == ["z"]
    assert "z" not in result["inputs_used"]["found_in_source"]
    # Fixed point is y1 = (0.5 + z) / 0.75, so z=10 settles at 14, not the 20
    # a quick reading suggests - the file's own z=1.0 really was overridden.
    assert result["state"]["y1"] == pytest.approx(14.0, abs=1e-6)


def test_values_the_file_computes_are_reported_as_unresolved(tmp_path):
    path = tmp_path / "computed.py"
    path.write_text(
        "from smartmdao import Pipeline\n"
        "pipeline = Pipeline()\n"
        "@pipeline.step(outputs=['b'])\n"
        "def double(a: float) -> float: return a * 2\n"
        "import math\n"
        "pipeline.run(a=math.pi)\n"
    )
    result = run_pipeline(str(path))

    assert result["inputs_used"]["unresolved_in_source"] == ["a"]
    assert "computed rather than literal" in result["inputs_used"]["note"]


def test_the_handler_reports_load_failures(tmp_path):
    assert run_pipeline(str(tmp_path / "absent.py"))["ok"] is False
