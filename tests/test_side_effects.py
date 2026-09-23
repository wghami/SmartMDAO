"""
Steps that touch something outside the pipeline.

Phase 5.1: the declaration and the static finding. The run-time half - refusing,
and the `"once"` latch - is 5.2, and the finding says so rather than implying
protection that does not exist yet.

What is tested here is mostly the *shape* of the rule rather than the mechanics:
that it is solver-aware, that a declaration of intent is not mistaken for
enforcement, and that a typo in the declaration is refused rather than reported
- because a typo here would silently declare a destructive step pure.
"""
import pytest

from smartmdao import (
    DAGSolver,
    HybridSolver,
    IterativeSolver,
    Pipeline,
    Step,
    explain,
    validate,
)


def codes(pipeline, inputs=()):
    return [finding.code for finding in validate(pipeline, inputs=inputs)]


def loop(solver, effects):
    """A two-step cycle where the first step declares `effects`."""
    pipeline = Pipeline(solver=solver)

    @pipeline.step(outputs=["notified"], effects=effects)
    def notify(mass: float) -> float:
        return mass

    @pipeline.step(outputs=["mass"])
    def size(notified: float) -> float:
        return notified * 0.5 + 10.0

    return pipeline


# ==============================================================================
# The declaration
# ==============================================================================

def test_a_step_is_pure_unless_it_says_otherwise():
    step = Step(fn=lambda x: x)
    assert step.effects is None
    assert step.has_effects is False


@pytest.mark.parametrize("value", [True, "once", "every-sweep"])
def test_declared_effects_are_carried_on_the_step(value):
    pipeline = Pipeline()

    @pipeline.step(outputs=["y"], effects=value)
    def touches(x: float) -> float:
        return x

    assert pipeline.steps[0].effects == value
    assert pipeline.steps[0].has_effects is True


@pytest.mark.parametrize("value", [False, None])
def test_explicitly_pure_is_allowed_and_means_pure(value):
    pipeline = Pipeline()
    pipeline.add(lambda x: x, outputs=["y"], effects=value)

    assert pipeline.steps[0].has_effects is False


def test_a_typo_in_the_declaration_is_refused_not_reported():
    """
    Unlike every other judgement call in this project, this one is raised. A
    misspelled value would silently declare a destructive step pure, and there
    is no defensible reading of `effects="sometimes"` to hand back.
    """
    with pytest.raises(ValueError, match="effects must be one of"):
        Step(fn=lambda x: x, effects="sometimes")

    with pytest.raises(ValueError, match="silently declare a destructive step"):
        Pipeline().add(lambda x: x, outputs=["y"], effects="occasionally")


def test_the_decorator_and_add_agree():
    by_decorator = Pipeline()

    @by_decorator.step(outputs=["y"], effects="once")
    def writer(x: float) -> float:
        return x

    by_add = Pipeline()
    by_add.add(writer, outputs=["y"], effects="once")

    assert by_decorator.steps[0].effects == by_add.steps[0].effects == "once"


# ==============================================================================
# The finding
# ==============================================================================

def test_an_undeclared_step_in_a_loop_is_not_reported():
    """Nothing is inferred: a pure-looking step stays silent."""
    assert "side-effect-in-cycle" not in codes(loop(HybridSolver(), None), ["notified"])


def test_effects_true_in_a_loop_asks_which_behaviour_is_meant():
    findings = [
        f for f in validate(loop(HybridSolver(), True), inputs=["notified"])
        if f.code == "side-effect-in-cycle"
    ]

    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert findings[0].step == "notify"
    assert "Say which you mean" in findings[0].message
    assert "run() will refuse it" in findings[0].message


def test_every_sweep_is_an_answer_and_silences_it():
    """The engineer keeps the choice; they are only required to make it."""
    assert "side-effect-in-cycle" not in codes(
        loop(HybridSolver(), "every-sweep"), ["notified"]
    )


def test_once_in_a_loop_is_reported_because_latching_changes_the_answer():
    """
    In 1.22.0 `"once"` was reported because nothing honoured it yet. 1.23.0
    ships the latch - and measuring it showed the latch has a real cost: a step
    inside a cyclic block feeds the loop back by definition, so freezing its
    output makes the loop converge somewhere other than its fixed point while
    reporting success. So it is still reported, for a different reason.
    """
    found = [
        f for f in validate(loop(HybridSolver(), "once"), inputs=["notified"])
        if f.code == "side-effect-latched"
    ]

    assert len(found) == 1
    assert found[0].severity == "warning"
    assert "not against its own fixed point" in found[0].message
    assert "split them" in found[0].message
    assert "side-effect-in-cycle" not in codes(loop(HybridSolver(), "once"), ["notified"])


def test_a_side_effecting_step_outside_any_loop_is_not_reported():
    pipeline = Pipeline()

    @pipeline.step(outputs=["summary"])
    def summarise(data: float) -> str:
        return str(data)

    @pipeline.step(outputs=["path"], effects=True)
    def write_report(summary: str) -> str:
        return "out.pdf"

    assert "side-effect-in-cycle" not in codes(pipeline, ["data"])


# ==============================================================================
# Solver awareness, which is the part that is easy to get wrong
# ==============================================================================

def test_iterative_solver_repeats_every_step_even_without_a_cycle():
    """
    The question is *would this run more than once*, not *is it in a cycle*.
    `IterativeSolver` sweeps everything it was given, so an acyclic pipeline
    still repeats - and asking the graph alone would report nothing here.
    """
    pipeline = Pipeline(solver=IterativeSolver())

    @pipeline.step(outputs=["y"], effects=True)
    def send(x: float) -> float:
        return x

    findings = [
        f for f in validate(pipeline, inputs=["x"]) if f.code == "side-effect-in-cycle"
    ]

    assert len(findings) == 1
    assert "swept repeatedly by IterativeSolver" in findings[0].message


def test_a_dag_runs_every_step_exactly_once_so_nothing_is_reported():
    pipeline = Pipeline(solver=DAGSolver())

    @pipeline.step(outputs=["y"], effects=True)
    def send(x: float) -> float:
        return x

    assert codes(pipeline, ["x"]) == []


def test_the_message_names_the_solver_doing_the_repeating():
    findings = [
        f for f in validate(loop(HybridSolver(), True), inputs=["notified"])
        if f.code == "side-effect-in-cycle"
    ]
    assert "HybridSolver" in findings[0].message


# ==============================================================================
# explain()
# ==============================================================================

def test_explain_states_what_the_pipeline_touches():
    """
    "What does this pipeline touch outside itself" is exactly what a reviewer
    wants stated, and it costs nothing to say.
    """
    pipeline = Pipeline()

    @pipeline.step(outputs=["summary"])
    def summarise(data: float) -> str:
        return str(data)

    @pipeline.step(outputs=["path"], effects="once")
    def write_report(summary: str) -> str:
        return "out.pdf"

    text = explain(pipeline, inputs=["data"])
    assert "Touches the world (1):" in text
    assert "write_report: once" in text


def test_explain_spells_out_an_unspecified_declaration():
    pipeline = Pipeline()

    @pipeline.step(outputs=["y"], effects=True)
    def send(x: float) -> float:
        return x

    assert "send: unspecified in a loop" in explain(pipeline, inputs=["x"])


def test_explain_says_nothing_when_a_pipeline_touches_nothing():
    pipeline = Pipeline()

    @pipeline.step(outputs=["y"])
    def compute(x: float) -> float:
        return x

    assert "Touches the world" not in explain(pipeline, inputs=["x"])
