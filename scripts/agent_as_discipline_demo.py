import logging
from dataclasses import dataclass, replace

from smartmdao import (
    Pipeline,
    IterativeSolver,
    OscillationAwareConvergenceChecker,
    OscillationDetectedError,
    configure_logging,
)

logger = logging.getLogger(__name__)

# ==============================================================================
# WHY THIS DEMO EXISTS
# ==============================================================================
# Gradient-based MDO sizes continuous variables beautifully and cannot touch
# *discrete architecture*: how many motors, which battery chemistry, which
# topology. In practice that half is decided by a human in a meeting.
#
# Because SmartMDAO converges non-numeric coupling variables on structural
# equality, a language model can occupy that role as an ordinary discipline:
# it proposes an architecture, numeric disciplines size it and hand back
# constraint violations, and the loop runs until the proposal stops changing.
#
# The numerics keep it honest - the model cannot hallucinate a mass budget,
# because `evaluate_architecture` computes it. And termination is decided by
# the solver, not by the model announcing that it is finished.
#
# `ask_model` below is a deterministic stub standing in for that call, so the
# convergence mechanics can be demonstrated and tested without a network
# round-trip. See docs/design/002-agent-as-discipline.md.
# ==============================================================================


@dataclass(frozen=True)
class Architecture:
    """
    The coupling variable. Deliberately LOW-ENTROPY: a handful of discrete
    decisions, nothing free-form.

    Structural equality is binary - one reworded word reads as "still moving" -
    so the loop couples on the *decision*, never on the prose explaining it.
    """
    n_motors: int
    battery: str            # "li-ion" | "li-s"
    infeasible: bool = False


@dataclass(frozen=True)
class Requirements:
    min_range_km: float
    max_mass_kg: float


# A discipline must always return a value. "No feasible answer" is a value,
# not the absence of one: returning None would make StepExecutor store nothing,
# leaving the previous architecture in memory unchanged - which the convergence
# checker would read as CONVERGED. An explicit sentinel converges honestly.
INFEASIBLE = Architecture(n_motors=0, battery="none", infeasible=True)

SPECIFIC_ENERGY_WH_PER_KG = {"li-ion": 250.0, "li-s": 450.0}
BATTERY_MASS_KG = 300.0
MOTOR_MASS_KG = 40.0
STRUCTURE_MASS_KG = 500.0
DRAG_FACTOR = 0.30


# ==============================================================================
# The numeric discipline - plain physics, no model involved
# ==============================================================================
def evaluate_architecture(
    architecture: Architecture, requirements: Requirements
) -> tuple[float, float, frozenset]:
    """Sizes an architecture and reports which requirements it violates."""
    if architecture.infeasible:
        return 0.0, 0.0, frozenset({"no_feasible_architecture"})

    mass_kg = (
        STRUCTURE_MASS_KG
        + BATTERY_MASS_KG
        + MOTOR_MASS_KG * architecture.n_motors
    )
    energy_wh = BATTERY_MASS_KG * SPECIFIC_ENERGY_WH_PER_KG[architecture.battery]
    range_km = energy_wh / (mass_kg * DRAG_FACTOR)

    violations = set()
    if range_km < requirements.min_range_km:
        violations.add("range")
    if mass_kg > requirements.max_mass_kg:
        violations.add("mass")

    return mass_kg, range_km, frozenset(violations)


# ==============================================================================
# The stubbed model discipline
# ==============================================================================
def ask_model(
    architecture: Architecture, violations: frozenset, requirements: Requirements
) -> Architecture:
    """
    Stands in for an LLM call. Given the current architecture and what it
    violated, propose a revision - or concede that nothing works.

    Deterministic on purpose: this demo is about the convergence machinery,
    not about model quality. Swap the body for an MCP sampling call and the
    surrounding pipeline is unchanged.
    """
    if architecture.infeasible or not violations:
        return architecture                       # nothing left to decide

    if "range" in violations:
        if architecture.battery == "li-ion":
            return replace(architecture, battery="li-s")
        return INFEASIBLE                          # already on the best chemistry

    if "mass" in violations and architecture.n_motors > 2:
        return replace(architecture, n_motors=architecture.n_motors - 2)

    return INFEASIBLE


def naive_ask_model(
    architecture: Architecture, violations: frozenset, requirements: Requirements
) -> Architecture:
    """
    A *worse* model, included on purpose. It believes more motors buy more
    range, which is false here - motors add mass, and mass costs range. So it
    adds motors to fix range, trips the mass limit, removes them to fix mass,
    and trips range again. Forever.

    This is the realistic failure mode of putting a model in a feedback loop,
    and it is exactly what OscillationAwareConvergenceChecker exists to catch.
    """
    if not violations:
        return architecture
    if "mass" in violations and architecture.n_motors > 2:
        return replace(architecture, n_motors=architecture.n_motors - 2)
    if "range" in violations:
        return replace(architecture, n_motors=architecture.n_motors + 2)
    return architecture


def build_pipeline(model_fn, max_iterations: int = 100) -> Pipeline:
    """
    Wires the model discipline and the numeric discipline into a feedback loop.

    NOTE: this drives `IterativeSolver` directly rather than `HybridSolver`,
    because only `IterativeSolver` accepts `target_var`. Without it, residuals
    are a max() across every produced variable and `distance()` is called once
    per variable in arbitrary set order - which a stateful checker cannot make
    sense of. HybridSolver does not forward `target_var` to the sub-solvers it
    creates for cyclic blocks.

    Step ORDER matters. `IterativeSolver` runs steps in registration order, so
    the numeric discipline is registered FIRST: the model has to see real
    violations on the very first sweep. Register it the other way round and
    sweep 1 hands the model an empty violation set, it returns the architecture
    unchanged, and the solver reads that as a converged fixed point before
    anything has actually been evaluated.
    """
    pipeline = Pipeline(
        solver=IterativeSolver(
            max_iterations=max_iterations,
            target_var="architecture",
            convergence_checker=OscillationAwareConvergenceChecker(),
        )
    )
    pipeline.add(evaluate_architecture, outputs=["mass_kg", "range_km", "violations"])
    pipeline.add(model_fn, outputs=["architecture"])
    return pipeline


def describe(architecture: Architecture) -> str:
    if architecture.infeasible:
        return "INFEASIBLE (no architecture satisfies these requirements)"
    return f"{architecture.n_motors} motors, {architecture.battery}"


def run_agent_as_discipline_demo():
    configure_logging(level=logging.WARNING)

    # ==========================================================================
    # CASE 1: converges on a feasible architecture
    # ==========================================================================
    print("=== CASE 1: the loop finds a feasible architecture ===")
    requirements = Requirements(min_range_km=400.0, max_mass_kg=1200.0)
    pipeline = build_pipeline(ask_model)

    result = pipeline.run(
        architecture=Architecture(n_motors=2, battery="li-ion"),
        requirements=requirements,
    )

    print(f"  start      : 2 motors, li-ion")
    print(f"  converged  : {describe(result['architecture'])}")
    print(f"  mass       : {result['mass_kg']:.0f} kg (limit {requirements.max_mass_kg:.0f})")
    print(f"  range      : {result['range_km']:.0f} km (need {requirements.min_range_km:.0f})")
    print(f"  violations : {set(result['violations']) or '{}'}")
    print(f"  iterations : {len(result['residual_history'][-1])}")

    # ==========================================================================
    # CASE 2: converges on "there is no answer"
    # ==========================================================================
    print("\n=== CASE 2: infeasible requirements converge on a sentinel ===")
    requirements = Requirements(min_range_km=900.0, max_mass_kg=1200.0)
    pipeline = build_pipeline(ask_model)

    result = pipeline.run(
        architecture=Architecture(n_motors=2, battery="li-ion"),
        requirements=requirements,
    )

    print(f"  converged  : {describe(result['architecture'])}")
    print(f"  iterations : {len(result['residual_history'][-1])}")
    print("  -> A converged 'no' is a real MDA result, not a failure. The loop")
    print("     terminated normally because the answer stopped changing.")

    # ==========================================================================
    # CASE 3: a naive model oscillates - and is caught
    # ==========================================================================
    print("\n=== CASE 3: oscillation is detected instead of burning iterations ===")
    print("  (the ERROR line below is expected - Pipeline.run logs the abort)")
    requirements = Requirements(min_range_km=600.0, max_mass_kg=950.0)
    pipeline = build_pipeline(naive_ask_model, max_iterations=100)

    try:
        pipeline.run(
            architecture=Architecture(n_motors=2, battery="li-s"),
            requirements=requirements,
        )
        print("  unexpected: no oscillation detected")
    except OscillationDetectedError as error:
        print(f"  detected   : period {error.period}, at sweep {error.iteration} of 100")
        for architecture in error.cycle:
            print(f"    - {describe(architecture)}")
        print("  -> Without detection this burns all 100 sweeps. Each sweep is a")
        print("     model call, so this is the difference between 4 calls and 100.")


if __name__ == "__main__":
    run_agent_as_discipline_demo()
