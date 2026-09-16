"""Comparing two pipelines, which is what makes a translation trustworthy.

The failure this catches is silent semantic drift: a converted pipeline that
returns a different number while looking cleaner, so it gets trusted. The
headline test builds exactly that — two translations of the same model whose
convergence criteria differ — and shows both reporting success while one of
their answers is 95% out.
"""
import pytest

from smartmdao.mcp.comparison import (
    DEFAULT_ATOL,
    DEFAULT_RTOL,
    MAX_DIFFERENCES,
    compare_runs,
    diff_states,
)

#: y1 settles slowly; y2 barely moves. A criterion watching y2 alone stops
#: almost immediately, while one watching everything keeps going.
DRIFTING = '''
from smartmdao import Pipeline, HybridSolver

pipeline = Pipeline(solver=HybridSolver(max_iterations=300, tolerance=1e-2{target}))

@pipeline.step(outputs=["y1"])
def discipline_1(y1: float, y2: float) -> float:
    return 0.95 * y1 + 0.05 * y2

@pipeline.step(outputs=["y2"])
def discipline_2(y1: float) -> float:
    return 10.0 + 0.0001 * y1

pipeline.run(y1=0.0, y2=10.0)
'''


@pytest.fixture
def faithful(tmp_path):
    path = tmp_path / "faithful.py"
    path.write_text(DRIFTING.format(target=', target_var="y2"'))
    return path


@pytest.fixture
def idiomatic(tmp_path):
    path = tmp_path / "idiomatic.py"
    path.write_text(DRIFTING.format(target=""))
    return path


# --- diff_states --------------------------------------------------------------

def test_identical_states_match():
    result = diff_states({"a": 1.0, "b": "x"}, {"a": 1.0, "b": "x"})
    assert result["match"] is True
    assert result["identical"] == 2
    assert result["differences"] == []


def test_numbers_within_tolerance_are_the_same():
    assert diff_states({"a": 1.0}, {"a": 1.0 + 1e-12})["match"] is True


def test_numbers_outside_tolerance_report_both_gaps():
    difference = diff_states({"a": 1.0}, {"a": 2.0})["differences"][0]

    assert difference["variable"] == "a"
    assert difference["absolute"] == 1.0
    assert difference["relative"] == 0.5


def test_a_tiny_absolute_gap_near_zero_is_absorbed():
    """Relative error is meaningless at zero; atol is what saves it."""
    assert diff_states({"a": 0.0}, {"a": 1e-15})["match"] is True


def test_relative_is_none_when_both_sides_are_zero_ish():
    result = diff_states({"a": 0.0}, {"a": 0.0}, atol=0.0)
    assert result["match"] is True


def test_booleans_compare_exactly_not_numerically():
    """True == 1 numerically; that is not a match anyone wants."""
    difference = diff_states({"flag": True}, {"flag": 1.5})["differences"][0]
    assert difference["a"] is True


def test_matching_booleans_are_a_match():
    assert diff_states({"flag": True, "off": False}, {"flag": True, "off": False})["match"] is True


def test_non_numeric_values_compare_exactly():
    result = diff_states({"plan": frozenset({"a"})}, {"plan": frozenset({"b"})})
    assert result["match"] is False
    assert "absolute" not in result["differences"][0]


def test_variables_present_on_only_one_side_are_listed():
    result = diff_states({"a": 1, "shared": 0}, {"b": 2, "shared": 0})

    assert result["only_in_a"] == ["a"]
    assert result["only_in_b"] == ["b"]
    assert result["match"] is False
    assert result["identical"] == 1


def test_the_biggest_difference_is_reported_first():
    """One variable 40% out and five 1e-7 out is one problem, not six."""
    left = {"small": 1.0, "huge": 1.0, "medium": 1.0}
    right = {"small": 1.000001, "huge": 2.0, "medium": 1.1}

    order = [d["variable"] for d in diff_states(left, right)["differences"]]
    assert order[0] == "huge"
    assert order[1] == "medium"


def test_a_flood_of_differences_is_truncated():
    left = {f"v{i}": 0.0 for i in range(MAX_DIFFERENCES + 7)}
    right = {f"v{i}": float(i + 1) for i in range(MAX_DIFFERENCES + 7)}

    result = diff_states(left, right)
    assert len(result["differences"]) == MAX_DIFFERENCES
    assert result["differences_omitted"] == 7


def test_tolerances_are_reported_so_a_match_can_be_judged():
    result = diff_states({"a": 1.0}, {"a": 1.0})
    assert result["tolerance"] == {"rtol": DEFAULT_RTOL, "atol": DEFAULT_ATOL}


def test_a_looser_tolerance_can_absorb_a_difference():
    assert diff_states({"a": 1.0}, {"a": 1.01}, rtol=0.1)["match"] is True


# --- compare_runs -------------------------------------------------------------

def test_a_pipeline_compared_with_itself_matches(faithful):
    result = compare_runs(str(faithful), str(faithful))

    assert result["ok"] is True
    assert result["match"] is True
    assert result["convergence_differs"] is False


def test_two_convergence_criteria_that_both_report_success_can_disagree(
    faithful, idiomatic
):
    """The whole reason this tool exists.

    Faithful watches y2 alone, as the hand-written loop did. Idiomatic uses the
    default max() across everything. Both converge, both report success, and one
    answer is wildly out - which nothing but running both would reveal.
    """
    result = compare_runs(str(faithful), str(idiomatic))

    assert result["ok"] is True
    assert result["match"] is False
    assert result["runs"]["a"]["converged"] is True
    assert result["runs"]["b"]["converged"] is True      # both claim success

    worst = result["differences"][0]
    assert worst["variable"] == "y1"
    assert worst["relative"] > 0.5                       # about 95% out

    # And the reason is visible: one stopped almost immediately.
    assert result["runs"]["a"]["iterations"][0] < result["runs"]["b"]["iterations"][0]


def test_the_same_inputs_go_to_both_sides(faithful, idiomatic):
    """A comparison on different inputs would mean nothing."""
    result = compare_runs(str(faithful), str(idiomatic))

    assert result["inputs_used"]["recovered_from_a"] == ["y1", "y2"]
    assert "same inputs were used for both" in result["inputs_used"]["note"]


def test_caller_inputs_override_both_sides(faithful, idiomatic):
    result = compare_runs(str(faithful), str(idiomatic), inputs={"y1": 0.0, "y2": 20.0})

    assert result["inputs_used"]["supplied"] == ["y1", "y2"]
    assert result["inputs_used"]["recovered_from_a"] == []
    # Both settle near the new y2, whatever their criteria.
    assert result["runs"]["b"]["converged"] is True


def test_a_different_destination_counts_as_a_difference(tmp_path, faithful):
    """Close numbers are not a match when one side never converged."""
    exhausted = tmp_path / "exhausted.py"
    exhausted.write_text(DRIFTING.format(target="").replace("max_iterations=300", "max_iterations=2"))

    result = compare_runs(str(faithful), str(exhausted))

    assert result["convergence_differs"] is True
    assert result["match"] is False
    assert result["runs"]["b"]["statuses"] == ["max_iterations"]


def test_a_side_that_will_not_load_is_reported_rather_than_compared(faithful, tmp_path):
    result = compare_runs(str(faithful), str(tmp_path / "absent.py"))

    assert result["ok"] is False
    assert "b did not run" in result["error"]
    assert "needs both sides" in result["error"]
    assert result["runs"]["a"]["ok"] is True


def test_a_missing_first_file_fails_before_running_anything(tmp_path, faithful):
    result = compare_runs(str(tmp_path / "absent.py"), str(faithful))
    assert result["ok"] is False
    assert "No such file" in result["error"]


def test_both_sides_failing_is_reported_once(tmp_path):
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    for path in (a, b):
        path.write_text(
            "from smartmdao import Pipeline\n"
            "pipeline = Pipeline()\n"
            "@pipeline.step(outputs=['y'])\n"
            "def boom(x: float) -> float: raise ValueError('no')\n"
        )
    result = compare_runs(str(a), str(b), inputs={"x": 1.0})

    assert result["ok"] is False
    assert "a and b did not run" in result["error"]


def test_an_unknown_rung_is_rejected(faithful):
    assert compare_runs(str(faithful), str(faithful), rung="nope")["ok"] is False


def test_comparing_at_a_cheaper_rung_is_allowed(faithful, idiomatic):
    """Smoke both sides if you only want to know they execute."""
    result = compare_runs(str(faithful), str(idiomatic), rung="smoke")
    assert result["ok"] is True


def test_the_handler_delegates(faithful):
    from smartmdao.mcp.handlers import compare_runs as handler

    assert handler(str(faithful), str(faithful))["match"] is True
