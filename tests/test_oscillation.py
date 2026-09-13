import pytest

from smartmdao.models import Step
from smartmdao.solvers import (
    ABANDONED,
    CONVERGED,
    MAX_ITERATIONS,
    ConvergenceReport,
    IterativeSolver,
    OscillationAwareConvergenceChecker,
    OscillationDetectedError,
    StandardConvergenceChecker,
)


def feed(checker, values):
    """Pushes a sequence through the checker the way IterativeSolver would."""
    previous = None
    for value in values:
        checker.distance(previous, value)
        previous = value


# --- Detection ---------------------------------------------------------------

def test_detects_two_cycle():
    checker = OscillationAwareConvergenceChecker()
    feed(checker, ["A", "B", "A", "B"])

    assert checker.detected_period == 2
    assert checker.detected_cycle == ["A", "B"]
    assert "oscillating with period 2" in checker.abandon_reason()


def test_detects_three_cycle():
    checker = OscillationAwareConvergenceChecker()
    feed(checker, ["A", "B", "C", "A", "B", "C"])

    assert checker.detected_period == 3
    assert checker.detected_cycle == ["A", "B", "C"]


def test_reports_smallest_period():
    # A,B,A,B is both a 2-cycle and (over 4 entries) a trivially longer pattern.
    # The smallest period is the useful diagnosis.
    checker = OscillationAwareConvergenceChecker()
    feed(checker, ["A", "B", "A", "B", "A", "B"])
    assert checker.detected_period == 2


def test_abandon_reason_is_none_until_a_cycle_is_seen():
    checker = OscillationAwareConvergenceChecker()
    feed(checker, ["A", "B", "C"])
    assert checker.abandon_reason() is None


def test_raising_is_opt_in():
    """Louder, but it destroys the run before anything can be recorded."""
    checker = OscillationAwareConvergenceChecker(raise_on_detection=True)
    with pytest.raises(OscillationDetectedError) as excinfo:
        feed(checker, ["A", "B", "A", "B"])

    error = excinfo.value
    assert error.period == 2
    assert error.cycle == ["A", "B"]
    # Two full repetitions are required, so a 2-cycle surfaces on the 4th value.
    assert error.iteration == 4
    assert "oscillating with period 2" in str(error)


def test_period_longer_than_max_period_is_not_detected():
    checker = OscillationAwareConvergenceChecker(max_period=2)
    # A genuine 3-cycle, but we only look for periods up to 2.
    feed(checker, ["A", "B", "C", "A", "B", "C"])
    assert checker.detected_period is None


# --- No false positives ------------------------------------------------------

def test_monotonic_progress_never_triggers():
    checker = OscillationAwareConvergenceChecker()
    feed(checker, [f"state-{i}" for i in range(20)])
    assert checker.detected_period is None


def test_history_is_bounded():
    checker = OscillationAwareConvergenceChecker(max_period=4)
    feed(checker, [f"state-{i}" for i in range(50)])
    assert len(checker.history) == 2 * checker.max_period


def test_converging_numeric_sequence_never_triggers():
    checker = OscillationAwareConvergenceChecker()
    value = 1.0
    previous = None
    for _ in range(30):
        checker.distance(previous, value)
        previous, value = value, value * 0.5
    assert checker.detected_period is None


# --- Delegation to the inner checker ----------------------------------------

def test_delegates_numeric_distance_to_inner():
    checker = OscillationAwareConvergenceChecker()
    assert checker.distance(1.0, 1.5) == pytest.approx(0.5)


def test_accepts_a_custom_inner_checker():
    class AlwaysMoving:
        def distance(self, previous, current):
            return 42.0

    checker = OscillationAwareConvergenceChecker(inner=AlwaysMoving())
    assert checker.distance("a", "b") == 42.0


def test_default_inner_is_the_standard_checker():
    assert isinstance(
        OscillationAwareConvergenceChecker().inner, StandardConvergenceChecker
    )


# --- Reset semantics ---------------------------------------------------------

def test_history_clears_on_convergence():
    checker = OscillationAwareConvergenceChecker()
    feed(checker, ["A", "B", "C"])
    assert checker.history

    checker.distance("C", "C")  # inner reports 0.0 -> at rest
    assert checker.history == []


def test_convergence_clears_a_prior_detection():
    checker = OscillationAwareConvergenceChecker(raise_on_detection=False)
    feed(checker, ["A", "B", "A", "B"])
    assert checker.detected_period == 2

    checker.distance("B", "B")
    assert checker.detected_period is None
    assert checker.detected_cycle is None


def test_reset_is_explicit_and_complete():
    checker = OscillationAwareConvergenceChecker(raise_on_detection=False)
    feed(checker, ["A", "B", "A", "B"])
    assert checker.detected_period is not None

    checker.reset()
    assert checker.history == []
    assert checker.detected_period is None
    assert checker.detected_cycle is None


def test_stale_history_survives_an_exhausted_run_until_reset():
    # Documents the leak the reset() docstring warns about: nothing in the
    # solvers resets the checker, so a run that ends without converging
    # leaves its history in place.
    checker = OscillationAwareConvergenceChecker(raise_on_detection=False)
    feed(checker, ["A", "B", "C"])
    assert len(checker.history) == 3


# --- Non-raising mode --------------------------------------------------------

def test_raise_on_detection_false_records_without_raising():
    checker = OscillationAwareConvergenceChecker(raise_on_detection=False)
    feed(checker, ["A", "B", "A", "B"])

    assert checker.detected_period == 2
    assert checker.detected_cycle == ["A", "B"]


def test_non_raising_mode_keeps_reporting_the_value_as_moving():
    checker = OscillationAwareConvergenceChecker(raise_on_detection=False)
    previous = None
    distances = []
    for value in ["A", "B", "A", "B"]:
        distances.append(checker.distance(previous, value))
        previous = value

    # Never 0.0 - an oscillating value is emphatically not converged.
    assert all(d == float("inf") for d in distances)


# --- Hostile values ----------------------------------------------------------

class Uncomparable:
    """A value whose `==` raises, the way a badly-behaved custom type might."""

    def __eq__(self, other):
        raise TypeError("nope")


def test_values_that_raise_on_comparison_do_not_break_detection():
    # Four *distinct* objects, so list comparison has to fall through to
    # __eq__ and blows up. That must be swallowed as "no cycle here" rather
    # than being allowed to kill the solve.
    checker = OscillationAwareConvergenceChecker()
    feed(checker, [Uncomparable() for _ in range(4)])
    assert checker.detected_period is None


def test_repeated_identical_objects_are_detected_despite_a_hostile_eq():
    # list.__eq__ compares elements by identity before falling back to __eq__,
    # so the same object recurring is recognised as a cycle even when the type
    # refuses to be compared. That is the correct answer: it is the same value.
    checker = OscillationAwareConvergenceChecker(raise_on_detection=False)
    a, b = Uncomparable(), Uncomparable()
    feed(checker, [a, b, a, b])
    assert checker.detected_period == 2


# --- Integration with IterativeSolver ---------------------------------------

def test_solver_aborts_early_instead_of_exhausting_iterations():
    """The whole point: stop paying for a fixed point that will never arrive."""
    calls = []

    def flip_flop(architecture):
        calls.append(architecture)
        return "B" if architecture == "A" else "A"

    steps = [Step(fn=flip_flop, manual_outputs=["architecture"])]
    solver = IterativeSolver(
        max_iterations=100,
        target_var="architecture",
        convergence_checker=OscillationAwareConvergenceChecker(),
    )

    result = solver.solve(steps, {"architecture": "A"})

    # Four sweeps to see two full repetitions, not the full hundred.
    assert len(calls) == 4

    report = result["convergence_reports"][-1]
    assert report.status == ABANDONED
    assert report.converged is False
    assert report.iterations == 4
    assert "period 2" in report.reason

    # And - the point of the whole exercise - the trace survives.
    assert len(result["residual_history"][-1]) == 4


def test_abandoning_still_raises_when_asked():
    steps = [Step(fn=lambda a: "B" if a == "A" else "A", manual_outputs=["a"])]
    solver = IterativeSolver(
        max_iterations=100,
        target_var="a",
        convergence_checker=OscillationAwareConvergenceChecker(
            raise_on_detection=True
        ),
    )
    with pytest.raises(OscillationDetectedError):
        solver.solve(steps, {"a": "A"})


def test_solver_still_converges_normally_with_the_checker_installed():
    steps = [Step(fn=lambda val: val * 0.5, manual_outputs=["val"])]
    solver = IterativeSolver(
        tolerance=0.1,
        target_var="val",
        convergence_checker=OscillationAwareConvergenceChecker(),
    )
    result = solver.solve(steps, {"val": 1.0})
    assert result["val"] < 0.1


# --- ConvergenceReport -------------------------------------------------------

def test_report_records_a_converged_run():
    steps = [Step(fn=lambda val: val * 0.5, manual_outputs=["val"])]
    solver = IterativeSolver(tolerance=0.1, target_var="val")
    result = solver.solve(steps, {"val": 1.0})

    report = result["convergence_reports"][-1]
    assert report.status == CONVERGED
    assert report.converged is True
    assert report.reason == ""
    assert report.steps == ("<lambda>",)
    assert report.final_residual < 0.1


def test_report_records_running_out_of_iterations():
    steps = [Step(fn=lambda x: x + 1, manual_outputs=["x"])]
    result = IterativeSolver(max_iterations=5).solve(steps, {"x": 0})

    report = result["convergence_reports"][-1]
    assert report.status == MAX_ITERATIONS
    assert report.converged is False
    assert report.iterations == 5
    assert len(report.residuals) == 5


def test_report_of_a_degenerate_run_has_no_residuals():
    """No steps means nothing to compare, so the loop never records one."""
    result = IterativeSolver(max_iterations=0).solve([], {})

    report = result["convergence_reports"][-1]
    assert report.iterations == 0
    assert report.residuals == ()
    assert report.final_residual == float("inf")


def test_report_str_is_readable():
    report = ConvergenceReport(
        status=ABANDONED,
        iterations=4,
        residuals=(float("inf"),),
        reason="it is cycling",
        steps=("a", "b"),
    )
    rendered = str(report)
    assert "abandoned after 4 iteration(s)" in rendered
    assert "it is cycling" in rendered
    assert "[a, b]" in rendered


def test_report_str_without_reason_or_steps():
    rendered = str(ConvergenceReport(status=CONVERGED, iterations=2, residuals=(0.0,)))
    assert rendered == "converged after 2 iteration(s), residual 0.000e+00"


def test_a_checker_without_abandon_reason_is_simply_never_asked():
    steps = [Step(fn=lambda x: x + 1, manual_outputs=["x"])]
    solver = IterativeSolver(
        max_iterations=3, convergence_checker=StandardConvergenceChecker()
    )
    result = solver.solve(steps, {"x": 0})
    assert result["convergence_reports"][-1].status == MAX_ITERATIONS


def test_a_custom_abandoning_checker_needs_no_inheritance():
    """AbandonmentAware is structural - implement the method and you are in."""

    class GiveUpImmediately:
        def distance(self, previous, current):
            return float("inf")

        def abandon_reason(self):
            return "I have seen enough"

    steps = [Step(fn=lambda x: x + 1, manual_outputs=["x"])]
    solver = IterativeSolver(max_iterations=50, convergence_checker=GiveUpImmediately())
    result = solver.solve(steps, {"x": 0})

    report = result["convergence_reports"][-1]
    assert report.status == ABANDONED
    assert report.reason == "I have seen enough"
    assert report.iterations == 1          # stopped on the very first sweep


def test_hybrid_solver_records_one_report_per_cyclic_block():
    from smartmdao import HybridSolver, Pipeline

    pipeline = Pipeline(solver=HybridSolver(max_iterations=60, tolerance=1e-6))

    @pipeline.step(outputs=["a1"])
    def a_first(a2: float) -> float:
        return a2 * 0.5

    @pipeline.step(outputs=["a2"])
    def a_second(a1: float) -> float:
        return a1 + 1.0

    @pipeline.step(outputs=["b1"])
    def b_first(b2: float) -> float:
        return b2 * 0.5

    @pipeline.step(outputs=["b2"])
    def b_second(b1: float) -> float:
        return b1 + 1.0

    result = pipeline.run(a2=1.0, b2=1.0)
    reports = result["convergence_reports"]

    assert len(reports) == 2
    assert all(report.converged for report in reports)
    # Each report names the block it describes.
    assert {frozenset(report.steps) for report in reports} == {
        frozenset({"a_first", "a_second"}),
        frozenset({"b_first", "b_second"}),
    }


# --- Integration with HybridSolver -------------------------------------------

def test_hybrid_solver_forwards_target_var_to_the_cycle():
    """The combination that was impossible before target_var was forwarded."""
    from smartmdao import HybridSolver, Pipeline

    calls = []
    pipeline = Pipeline(
        solver=HybridSolver(
            max_iterations=100,
            target_var="architecture",
            convergence_checker=OscillationAwareConvergenceChecker(),
        )
    )

    @pipeline.step(outputs=["architecture"])
    def propose(violations: frozenset, architecture: str) -> str:
        calls.append(architecture)
        return "B" if architecture == "A" else "A"

    @pipeline.step(outputs=["violations"])
    def check(architecture: str) -> frozenset:
        return frozenset({"bad"})

    result = pipeline.run(architecture="A", violations=frozenset())

    assert len(calls) == 4          # caught at 2p, not at max_iterations
    report = result["convergence_reports"][-1]
    assert report.status == ABANDONED
    assert "period 2" in report.reason


def test_hybrid_solver_still_converges_normally_with_a_target():
    from smartmdao import HybridSolver, Pipeline

    pipeline = Pipeline(solver=HybridSolver(tolerance=1e-6, target_var="y2"))

    @pipeline.step(outputs=["y1"])
    def d1(y2: float) -> float:
        return y2 * 0.5

    @pipeline.step(outputs=["y2"])
    def d2(y1: float) -> float:
        return y1 + 1.0

    result = pipeline.run(y2=1.0)
    assert result["y2"] == pytest.approx(2.0, abs=1e-4)


def test_hybrid_solver_only_targets_the_block_that_produces_the_variable():
    """Forwarding a target to the wrong block would be silently catastrophic.

    A block that does not produce `target_var` has no entry for it in its own
    snapshot, so the residual becomes distance(None, ...) - which is 0.0 when
    nothing else produces the name either. The block would report convergence
    on its first sweep without iterating.
    """
    from smartmdao import HybridSolver, Pipeline

    sweeps = []
    pipeline = Pipeline(
        solver=HybridSolver(max_iterations=20, target_var="not_produced_anywhere")
    )

    @pipeline.step(outputs=["y1"])
    def d1(y2: float) -> float:
        sweeps.append(1)
        return y2 * 0.5

    @pipeline.step(outputs=["y2"])
    def d2(y1: float) -> float:
        return y1 + 1.0

    result = pipeline.run(y2=1.0)

    # It really iterated rather than faking convergence at sweep 1.
    assert len(sweeps) > 1
    assert result["y2"] == pytest.approx(2.0, abs=1e-3)


def test_hybrid_solver_warns_when_the_target_is_never_applied(caplog):
    from smartmdao import HybridSolver, Pipeline

    pipeline = Pipeline(solver=HybridSolver(max_iterations=5, target_var="ghost"))

    # Named, not lambdas: a cyclic block executes in alphabetical order, and
    # two `<lambda>`s sort equal, leaving Tarjan's reversed order in place -
    # which would run the consumer before its producer.
    @pipeline.step(outputs=["y1"])
    def a_step(y2: float) -> float:
        return y2 * 0.5

    @pipeline.step(outputs=["y2"])
    def z_step(y1: float) -> float:
        return y1 + 1.0

    with caplog.at_level("WARNING", logger="smartmdao.solvers"):
        pipeline.run(y2=1.0)

    assert any("was never applied" in record.message for record in caplog.records)


def test_hybrid_solver_targets_only_the_relevant_block_of_several():
    """Two independent cycles; the target belongs to exactly one of them."""
    from smartmdao import HybridSolver, Pipeline

    pipeline = Pipeline(solver=HybridSolver(max_iterations=50, target_var="b2"))

    @pipeline.step(outputs=["a1"])
    def a_first(a2: float) -> float:
        return a2 * 0.5

    @pipeline.step(outputs=["a2"])
    def a_second(a1: float) -> float:
        return a1 + 1.0

    @pipeline.step(outputs=["b1"])
    def b_first(b2: float, a2: float) -> float:
        return (b2 * 0.5) + (a2 * 0.0)

    @pipeline.step(outputs=["b2"])
    def b_second(b1: float) -> float:
        return b1 + 1.0

    result = pipeline.run(a2=1.0, b2=1.0)

    # Both cycles converged to the same fixed point, x = 0.5x + 1 -> 2.
    assert result["a2"] == pytest.approx(2.0, abs=1e-3)
    assert result["b2"] == pytest.approx(2.0, abs=1e-3)


def test_solver_converges_on_a_non_numeric_fixed_point():
    def settle(plan):
        return frozenset(plan) | {"database"}

    steps = [Step(fn=settle, manual_outputs=["plan"])]
    solver = IterativeSolver(
        target_var="plan",
        convergence_checker=OscillationAwareConvergenceChecker(),
    )
    result = solver.solve(steps, {"plan": frozenset({"billing"})})
    assert result["plan"] == frozenset({"billing", "database"})
