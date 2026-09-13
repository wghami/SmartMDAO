import pytest

from smartmdao import DAGSolver, HybridSolver, IterativeSolver, Pipeline
from smartmdao.analysis import (
    ERROR,
    INFO,
    WARNING,
    Finding,
    analyze,
    explain,
    validate,
)
from smartmdao.graph import build_execution_plan
from smartmdao.models import Step


def codes(findings):
    return [finding.code for finding in findings]


# --- Fixtures ----------------------------------------------------------------

def linear_pipeline(solver=None):
    pipeline = Pipeline(solver=solver or DAGSolver())

    @pipeline.step(outputs=["b"])
    def double(a: float) -> float:
        return a * 2

    @pipeline.step(outputs=["c"])
    def increment(b: float) -> float:
        return b + 1

    return pipeline


def sellar_pipeline(solver=None):
    pipeline = Pipeline(solver=solver or HybridSolver())

    @pipeline.step(outputs=["y1"])
    def discipline_1(z1: float, x1: float, y2: float) -> float:
        return z1 + x1 - y2

    @pipeline.step(outputs=["y2"])
    def discipline_2(z1: float, y1: float) -> float:
        return z1 + y1

    @pipeline.step(outputs=["objective"])
    def compute_objective(y1: float, y2: float) -> float:
        return y1 + y2

    return pipeline


# --- analyze -----------------------------------------------------------------

def test_analyze_linear_pipeline():
    analysis = analyze(linear_pipeline(), inputs=["a"])

    assert analysis.steps == ("double", "increment")
    assert analysis.execution_order == ("double", "increment")
    assert analysis.cycles == ()
    assert analysis.has_cycles is False
    assert analysis.external_inputs == ("a",)
    assert analysis.terminal_outputs == ("c",)
    assert analysis.initial_guesses_required == ()
    assert analysis.recommended_solver == "DAGSolver"
    assert "No feedback loops" in analysis.reason


def test_analyze_detects_the_cycle_and_the_step_after_it():
    analysis = analyze(sellar_pipeline(), inputs=["z1", "x1", "y2"])

    assert analysis.recommended_solver == "HybridSolver"
    assert "feedback loop" in analysis.reason
    assert len(analysis.cycles) == 1

    cycle = analysis.cycles[0]
    assert set(cycle.steps) == {"discipline_1", "discipline_2"}
    assert cycle.feedback_variables == ("y1", "y2")

    # The objective is downstream of the loop, so it runs once, last.
    assert analysis.execution_order[-1] == "compute_objective"
    assert analysis.terminal_outputs == ("objective",)


def test_analyze_identifies_the_variable_needing_a_seed():
    analysis = analyze(sellar_pipeline(), inputs=["z1", "x1"])

    assert len(analysis.initial_guesses_required) == 1
    guess = analysis.initial_guesses_required[0]
    assert guess.variable == "y2"
    assert guess.consumed_by == "discipline_1"


def test_declaring_the_seed_clears_the_requirement():
    analysis = analyze(sellar_pipeline(), inputs=["z1", "x1", "y2"])
    assert analysis.initial_guesses_required == ()


def test_which_variable_needs_seeding_follows_alphabetical_step_order():
    """The trap from docs/known-issues.md, pinned.

    A cyclic block runs in alphabetical order, so the step that sorts first is
    the one whose input must be seeded - rename it and the answer moves.
    """
    def make(first_name, second_name):
        pipeline = Pipeline(solver=HybridSolver())
        first = Step(fn=lambda beta: beta + 1, manual_outputs=["alpha"])
        first.fn.__name__ = first_name
        second = Step(fn=lambda alpha: alpha + 1, manual_outputs=["beta"])
        second.fn.__name__ = second_name
        pipeline.steps.extend([first, second])
        return pipeline

    # "a_step" produces alpha from beta; it sorts first, so beta needs a seed.
    analysis = analyze(make("a_step", "z_step"))
    assert [g.variable for g in analysis.initial_guesses_required] == ["beta"]

    # Swap the names and the other side of the loop needs seeding instead.
    analysis = analyze(make("z_step", "a_step"))
    assert [g.variable for g in analysis.initial_guesses_required] == ["alpha"]


def test_iterative_solver_uses_registration_order_not_the_graph():
    """IterativeSolver ignores the dependency graph, so seeding differs."""
    pipeline = Pipeline(solver=IterativeSolver(target_var="reading"))

    @pipeline.step(outputs=["reading"])
    def sensor(control: float) -> float:
        return control * 2

    @pipeline.step(outputs=["control"])
    def controller(reading: float) -> float:
        return reading - 1

    # Registration order runs `sensor` first, so `control` is what needs a seed.
    analysis = analyze(pipeline)
    assert analysis.execution_order == ("sensor", "controller")
    assert [g.variable for g in analysis.initial_guesses_required] == ["control"]


def test_iterative_solver_honours_an_explicit_execution_order():
    pipeline = Pipeline(
        solver=IterativeSolver(execution_order=["increment", "double"])
    )

    @pipeline.step(outputs=["b"])
    def double(a: float) -> float:
        return a * 2

    @pipeline.step(outputs=["c"])
    def increment(b: float) -> float:
        return b + 1

    analysis = analyze(pipeline, inputs=["a"])
    assert analysis.execution_order == ("increment", "double")


def test_analyze_handles_an_empty_pipeline():
    analysis = analyze(Pipeline())
    assert analysis.steps == ()
    assert analysis.execution_order == ()
    assert analysis.recommended_solver == "DAGSolver"


def test_optional_parameters_are_not_required_inputs():
    pipeline = Pipeline()

    @pipeline.step(outputs=["b"])
    def with_default(a: float = 1.0) -> float:
        return a

    assert validate(pipeline) == ()


# --- validate ----------------------------------------------------------------

def test_clean_pipeline_has_no_findings():
    assert validate(sellar_pipeline(), inputs=["z1", "x1", "y2"]) == ()


def test_detects_duplicate_output_names():
    pipeline = Pipeline()
    pipeline.add(lambda x: x + 1, outputs=["y"])
    pipeline.add(lambda x: x + 100, outputs=["y"])

    findings = validate(pipeline, inputs=["x"])
    assert "duplicate-output" in codes(findings)
    assert "computed and discarded" in findings[0].message


def test_detects_type_mismatch_on_an_edge():
    pipeline = Pipeline()

    @pipeline.step(outputs=["value"])
    def produce(seed: int) -> str:
        return str(seed)

    @pipeline.step(outputs=["result"])
    def consume(value: int) -> int:
        return value

    findings = validate(pipeline, inputs=["seed"])
    assert "type-mismatch" in codes(findings)


def test_collects_every_type_mismatch_rather_than_raising_on_the_first():
    pipeline = Pipeline()

    @pipeline.step(outputs=["a"])
    def produce_a(seed: int) -> str:
        return str(seed)

    @pipeline.step(outputs=["b"])
    def produce_b(seed: int) -> str:
        return str(seed)

    @pipeline.step(outputs=["out"])
    def consume(a: int, b: int) -> int:
        return a + b

    findings = validate(pipeline, inputs=["seed"])
    assert codes(findings).count("type-mismatch") == 2


def test_detects_missing_inputs_and_groups_them_by_variable():
    findings = validate(sellar_pipeline())
    missing = [f for f in findings if f.code == "missing-input"]

    # z1 is consumed by two steps but is one thing to fix.
    variables = {f.variable for f in missing}
    assert variables == {"z1", "x1"}

    z1_finding = next(f for f in missing if f.variable == "z1")
    assert "discipline_1" in z1_finding.message
    assert "discipline_2" in z1_finding.message
    assert z1_finding.step is None            # shared, so not attributed to one


def test_single_consumer_missing_input_is_attributed_to_its_step():
    findings = validate(linear_pipeline())
    missing = next(f for f in findings if f.code == "missing-input")
    assert missing.variable == "a"
    assert missing.step == "double"


def test_dag_solver_on_a_cyclic_pipeline_is_an_error():
    findings = validate(sellar_pipeline(solver=DAGSolver()), inputs=["z1", "x1", "y2"])
    assert "solver-mismatch" in codes(findings)
    assert findings[0].severity == ERROR


def test_iterative_solver_on_an_acyclic_misordered_pipeline_warns():
    pipeline = Pipeline(solver=IterativeSolver())

    @pipeline.step(outputs=["c"])                     # consumer registered first
    def increment(b: float) -> float:
        return b + 1

    @pipeline.step(outputs=["b"])
    def double(a: float) -> float:
        return a * 2

    findings = validate(pipeline, inputs=["a", "b"])
    warning = next(f for f in findings if f.code == "avoidable-iteration")
    assert warning.severity == WARNING
    assert "report convergence before anything was evaluated" in warning.message


def test_correctly_ordered_acyclic_iterative_pipeline_does_not_warn():
    pipeline = Pipeline(solver=IterativeSolver())
    pipeline.add(lambda a: a * 2, outputs=["b"])
    pipeline.add(lambda b: b + 1, outputs=["c"])

    assert "avoidable-iteration" not in codes(validate(pipeline, inputs=["a"]))


def test_iterative_solver_on_a_cycle_without_target_var_is_informational():
    pipeline = sellar_pipeline(solver=IterativeSolver())
    findings = validate(pipeline, inputs=["z1", "x1", "y2"])

    info = next(f for f in findings if f.code == "no-target-var")
    assert info.severity == INFO
    assert "target_var" in info.message


def test_target_var_suppresses_the_informational_finding():
    pipeline = sellar_pipeline(solver=IterativeSolver(target_var="y1"))
    assert "no-target-var" not in codes(validate(pipeline, inputs=["z1", "x1", "y2"]))


def test_findings_are_sorted_worst_first():
    pipeline = sellar_pipeline(solver=IterativeSolver())
    findings = validate(pipeline)        # missing inputs (error) + info

    severities = [f.severity for f in findings]
    assert severities == sorted(severities, key=[ERROR, WARNING, INFO].index)


def test_a_custom_type_checker_is_honoured():
    class PermissiveChecker:
        def check_value(self, value, expected):
            return True

        def check_types(self, produced, expected):
            return True

    pipeline = Pipeline()

    @pipeline.step(outputs=["value"])
    def produce(seed: int) -> str:
        return str(seed)

    @pipeline.step(outputs=["result"])
    def consume(value: int) -> int:
        return value

    strict = validate(pipeline, inputs=["seed"])
    permissive = validate(pipeline, inputs=["seed"], type_checker=PermissiveChecker())

    assert "type-mismatch" in codes(strict)
    assert "type-mismatch" not in codes(permissive)


def test_producer_without_a_return_annotation_is_not_flagged():
    """A consumer may declare a type even when the producer declares none."""
    pipeline = Pipeline()

    @pipeline.step(outputs=["value"])
    def produce(seed: int):                    # no return annotation
        return str(seed)

    @pipeline.step(outputs=["result"])
    def consume(value: int) -> int:
        return value

    assert "type-mismatch" not in codes(validate(pipeline, inputs=["seed"]))


def test_untyped_steps_are_not_flagged():
    """Missing type information degrades to 'unchecked', never to an error."""
    pipeline = Pipeline()
    pipeline.add(lambda a: a * 2, outputs=["b"])
    pipeline.add(lambda b: b + 1, outputs=["c"])

    assert "type-mismatch" not in codes(validate(pipeline, inputs=["a"]))


# --- Finding formatting ------------------------------------------------------

def test_finding_str_includes_the_step_when_known():
    finding = Finding(code="x", severity=ERROR, message="boom", step="my_step")
    assert str(finding) == "ERROR: boom [my_step]"


def test_finding_str_falls_back_to_the_variable():
    finding = Finding(code="x", severity=WARNING, message="boom", variable="y2")
    assert str(finding) == "WARNING: boom [y2]"


def test_finding_str_without_a_location():
    assert str(Finding(code="x", severity=INFO, message="boom")) == "INFO: boom"


# --- explain -----------------------------------------------------------------

def test_explain_describes_a_cyclic_pipeline():
    text = explain(sellar_pipeline(), inputs=["z1", "x1", "y2"])

    assert "Recommended solver: HybridSolver" in text
    assert "Feedback loops (1):" in text
    assert "coupling on: y1, y2" in text
    assert "Execution order:" in text
    assert "External inputs: x1, z1" in text
    assert "Final outputs:   objective" in text


def test_explain_reports_seeds_and_findings():
    text = explain(sellar_pipeline(), inputs=["z1", "x1"])
    assert "Needs initial values for: y2 (for discipline_1)" in text
    assert "Findings (" in text


def test_explain_describes_an_acyclic_pipeline():
    text = explain(linear_pipeline(), inputs=["a"])
    assert "Feedback loops: none." in text
    assert "Recommended solver: DAGSolver" in text


def test_explain_handles_a_pipeline_with_no_external_inputs():
    pipeline = Pipeline(solver=HybridSolver())
    pipeline.add(lambda beta: beta + 1, outputs=["alpha"])
    pipeline.add(lambda alpha: alpha + 1, outputs=["beta"])

    text = explain(pipeline, inputs=["alpha"])
    assert "External inputs: none" in text


def test_explain_omits_final_outputs_when_everything_is_consumed():
    pipeline = Pipeline(solver=HybridSolver())
    pipeline.add(lambda beta: beta + 1, outputs=["alpha"])
    pipeline.add(lambda alpha: alpha + 1, outputs=["beta"])

    assert "Final outputs:" not in explain(pipeline, inputs=["alpha"])


def test_explain_reports_a_cycle_with_no_detectable_coupling():
    """A self-looping step whose output name differs from its input name."""
    pipeline = Pipeline(solver=HybridSolver())
    step = Step(fn=lambda value: value, manual_outputs=["value"])
    pipeline.steps.append(step)

    plan = build_execution_plan(pipeline.steps, set())
    assert plan[0].is_cyclic          # guard: this really is a self-loop

    text = explain(pipeline, inputs=["value"])
    assert "coupling on: value" in text
