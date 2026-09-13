import logging
import math

from smartmdao import (
    DAGSolver,
    HybridSolver,
    IterativeSolver,
    Pipeline,
    analyze,
    configure_logging,
    explain,
    validate,
)

logger = logging.getLogger(__name__)

# ==============================================================================
# WHY THIS DEMO EXISTS
# ==============================================================================
# Every structural fact about a pipeline - what runs, in what order, which
# loops exist, which types disagree - is derivable from function signatures and
# annotations. None of it requires running a discipline.
#
# `analyze`, `validate` and `explain` expose exactly that. The point is to find
# out that your pipeline is wrong BEFORE spending an optimization run on it,
# and to answer the questions that are genuinely hard to see by eye:
#
#   - did these functions accidentally close a feedback loop?
#   - which variable needs an initial guess, and why that one?
#   - does the configured solver actually fit this pipeline?
#
# Nothing below executes a single discipline. Notice that several of the
# disciplines have bodies that would crash if they ever ran.
# ==============================================================================


def build_sellar() -> Pipeline:
    """The classic coupled MDO problem: y1 and y2 feed each other."""
    pipeline = Pipeline(solver=HybridSolver())

    @pipeline.step(outputs=["y1"])
    def discipline_1(z1: float, z2: float, x1: float, y2: float) -> float:
        return (z1 ** 2) + z2 + x1 - (0.2 * y2)

    @pipeline.step(outputs=["y2"])
    def discipline_2(z1: float, z2: float, y1: float) -> float:
        return math.sqrt(abs(y1)) + z1 + z2

    @pipeline.step(outputs=["objective"])
    def compute_objective(x1: float, z2: float, y1: float, y2: float) -> float:
        return (x1 ** 2) + z2 + (y1 ** 2) + math.exp(-y2)

    return pipeline


def build_broken() -> Pipeline:
    """
    A pipeline with four separate problems, none of which raise on definition.

    This is roughly what carelessly generated code looks like: it imports fine,
    it looks plausible, and it fails at run time - or worse, produces a number.
    """
    pipeline = Pipeline(solver=DAGSolver())

    # (1) type mismatch: this declares str...
    @pipeline.step(outputs=["mass"])
    def estimate_mass(span: float) -> str:
        return "heavy"

    # ...but its consumer declares float.
    @pipeline.step(outputs=["loads"])
    def compute_loads(mass: float) -> float:
        return mass * 9.81

    # (2) a genuine feedback loop, under a solver that raises on cycles
    @pipeline.step(outputs=["deflection"])
    def compute_deflection(loads: float, stiffness: float) -> float:
        return loads / stiffness

    @pipeline.step(outputs=["stiffness"])
    def compute_stiffness(deflection: float) -> float:
        return deflection * 2.0

    # (3) duplicate output name - the earlier result is computed and discarded
    @pipeline.step(outputs=["cost"])
    def estimate_cost(mass: float) -> float:
        return mass * 100.0

    @pipeline.step(outputs=["cost"])
    def refine_cost(mass: float) -> float:
        return mass * 120.0

    # (4) 'allowable' is required but nothing produces it
    @pipeline.step(outputs=["margin"])
    def check_margin(loads: float, allowable: float) -> float:
        return allowable - loads

    return pipeline


def show_findings(pipeline, inputs) -> None:
    findings = validate(pipeline, inputs=inputs)
    if not findings:
        print("    (no findings - pipeline is structurally sound)")
        return
    for finding in findings:
        print(f"    [{finding.severity:>7}] {finding.code}")
        print(f"              {finding.message}")


def run_pipeline_analysis_demo():
    configure_logging(level=logging.WARNING)

    # ==========================================================================
    # CASE 1: what will actually happen when this runs
    # ==========================================================================
    print("=== CASE 1: analyze() - reading a pipeline without running it ===")
    sellar = build_sellar()
    analysis = analyze(sellar, inputs=["z1", "z2", "x1"])

    print(f"  steps           : {', '.join(analysis.steps)}")
    print(f"  execution order : {' -> '.join(analysis.execution_order)}")
    print(f"  solver          : {analysis.recommended_solver}")
    print(f"  external inputs : {', '.join(analysis.external_inputs)}")
    print(f"  final outputs   : {', '.join(analysis.terminal_outputs)}")
    print(f"  feedback loops  : {len(analysis.cycles)}")
    for cycle in analysis.cycles:
        print(f"      {' <-> '.join(cycle.steps)}")
        print(f"      coupling on: {', '.join(cycle.feedback_variables)}")
    for guess in analysis.initial_guesses_required:
        print(f"  NEEDS A GUESS   : {guess.variable} (read by {guess.consumed_by})")
    print()
    print("  -> That last line is the useful one. Nothing in the source says")
    print("     'y2 needs an initial value'; it falls out of the loop structure.")
    print("     It is exactly the y2=1.0 the README passes to run().")

    # ==========================================================================
    # CASE 2: everything wrong with a pipeline, in one pass
    # ==========================================================================
    print("\n=== CASE 2: validate() - four authored mistakes, none raise on import ===")
    show_findings(build_broken(), inputs=["span"])
    print()
    print("  -> Four deliberate mistakes produce SEVEN findings, which is the")
    print("     honest picture: one wrong return annotation on 'estimate_mass'")
    print("     breaks the edge to every one of its three consumers, and the")
    print("     feedback loop needs a seed on top of needing a different solver.")
    print("     validate() collects them all rather than stopping at the first,")
    print("     because fixing generated code one error per run is slow.")

    # ==========================================================================
    # CASE 3: the answer depends on the solver
    # ==========================================================================
    print("\n=== CASE 3: the same steps need DIFFERENT seeds per solver ===")

    def build_control_loop(solver):
        pipeline = Pipeline(solver=solver)

        @pipeline.step(outputs=["reading"])
        def sensor(control: float) -> float:
            return control * 2.0

        @pipeline.step(outputs=["control"])
        def controller(reading: float) -> float:
            return reading - 1.0

        return pipeline

    for label, solver in (
        ("HybridSolver", HybridSolver()),
        ("IterativeSolver", IterativeSolver(target_var="reading")),
    ):
        result = analyze(build_control_loop(solver))
        order = " -> ".join(result.execution_order)
        seeds = ", ".join(g.variable for g in result.initial_guesses_required)
        print(f"  {label:<16} runs {order:<24} needs: {seeds}")

    print()
    print("  -> Identical steps, identical registration order, different answer.")
    print("     HybridSolver orders a cyclic block ALPHABETICALLY, so 'controller'")
    print("     goes first and wants 'reading'. IterativeSolver ignores the graph")
    print("     entirely and sweeps in REGISTRATION order, so 'sensor' goes first")
    print("     and wants 'control'. Seed the wrong one and you get a KeyError")
    print("     from deep inside the solve.")

    # ==========================================================================
    # CASE 4: the whole picture, in prose
    # ==========================================================================
    print("\n=== CASE 4: explain() - for review, docs, or an agent to read ===")
    for line in explain(sellar, inputs=["z1", "z2", "x1", "y2"]).splitlines():
        print(f"  {line}")


if __name__ == "__main__":
    run_pipeline_analysis_demo()
