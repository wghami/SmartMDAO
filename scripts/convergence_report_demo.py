import logging

from smartmdao import (
    ABANDONED,
    CONVERGED,
    MAX_ITERATIONS,
    HybridSolver,
    IterativeSolver,
    OscillationAwareConvergenceChecker,
    OscillationDetectedError,
    Pipeline,
    configure_logging,
)

logger = logging.getLogger(__name__)

# ==============================================================================
# WHY THIS DEMO EXISTS
# ==============================================================================
# A solve used to tell you almost nothing about itself. You got
# `residual_history` - a list of numbers - and had to work out the outcome by
# re-applying the tolerance by hand. "Converged" and "ran out of iterations"
# were indistinguishable without doing that arithmetic yourself.
#
# Worse, there was no way for a convergence checker to say "stop, this is never
# going to converge". `distance()` returns a magnitude, and a magnitude cannot
# express "give up" - so the only exits were below-tolerance or out-of-
# iterations. A checker that KNEW the system was hopeless had to raise from
# inside `distance()`, which killed the run and took the residual history with
# it. You learned that it failed, and nothing about how.
#
# Two additions fix that:
#
#   ConvergenceReport   - what actually happened, for every iterative block
#   AbandonmentAware    - an optional protocol letting a checker stop a solve
#                         cleanly instead of raising
#
# AbandonmentAware is structural typing: implement `abandon_reason()` and you
# are in. No inheritance, and existing checkers are completely unaffected.
# ==============================================================================


def show(result) -> None:
    for report in result["convergence_reports"]:
        print(f"    {report}")


def run_convergence_report_demo():
    configure_logging(level=logging.CRITICAL)

    # ==========================================================================
    # CASE 1: the three outcomes are now distinguishable
    # ==========================================================================
    print("=== CASE 1: every solve says what happened to it ===")

    print("  a) converged:")
    settling = Pipeline(solver=IterativeSolver(tolerance=1e-6, target_var="y2"))

    @settling.step(outputs=["y1"])
    def damp(y2: float) -> float:
        return y2 * 0.5

    @settling.step(outputs=["y2"])
    def offset(y1: float) -> float:
        return y1 + 1.0

    show(settling.run(y2=1.0))

    print("  b) ran out of iterations:")
    diverging = Pipeline(solver=IterativeSolver(max_iterations=6, target_var="x"))

    @diverging.step(outputs=["x"])
    def never_settles(x: float) -> float:
        return x + 1.0

    show(diverging.run(x=0.0))

    print("  c) abandoned as hopeless:")
    flipping = Pipeline(
        solver=IterativeSolver(
            max_iterations=100,
            target_var="choice",
            convergence_checker=OscillationAwareConvergenceChecker(),
        )
    )

    @flipping.step(outputs=["choice"])
    def flip_flop(choice: str) -> str:
        return "B" if choice == "A" else "A"

    abandoned = flipping.run(choice="A")
    show(abandoned)

    print()
    print("  -> Before, all three returned the same shape and you had to infer")
    print("     the difference. `report.status` is one of CONVERGED,")
    print("     MAX_ITERATIONS or ABANDONED, and `report.converged` is a bool.")
    print(f"     e.g. {CONVERGED!r}, {MAX_ITERATIONS!r}, {ABANDONED!r}")

    # ==========================================================================
    # CASE 2: the information that used to be destroyed
    # ==========================================================================
    print("\n=== CASE 2: an abandoned run keeps its own record ===")
    report = abandoned["convergence_reports"][-1]
    print(f"  status    : {report.status}")
    print(f"  iterations: {report.iterations} (of a permitted 100)")
    print(f"  reason    : {report.reason}")
    print(f"  residuals : {len(report.residuals)} recorded, kept in full")
    print()
    print("  The same pipeline with raise_on_detection=True - the old behaviour,")
    print("  now opt-in:")

    raising = Pipeline(
        solver=IterativeSolver(
            max_iterations=100,
            target_var="choice",
            convergence_checker=OscillationAwareConvergenceChecker(
                raise_on_detection=True
            ),
        )
    )
    raising.add(flip_flop, outputs=["choice"])
    try:
        raising.run(choice="A")
    except OscillationDetectedError as error:
        print(f"    raised: {type(error).__name__}, period {error.period}")
        print("    ...and the residual history is gone, because the run never")
        print("    reached the line that records it. That is the trade: louder,")
        print("    but it destroys the evidence. Hence the default flipped.")

    # ==========================================================================
    # CASE 3: writing your own abandoning checker
    # ==========================================================================
    print("\n=== CASE 3: AbandonmentAware is structural - no inheritance ===")

    class StallDetector:
        """Gives up when the residual stops improving meaningfully."""

        def __init__(self, patience: int = 3):
            self.patience = patience
            self.history: list[float] = []

        def distance(self, previous, current) -> float:
            gap = abs(current - previous) if previous is not None else float("inf")
            self.history.append(gap)
            return gap

        def abandon_reason(self):
            if len(self.history) <= self.patience:
                return None
            recent = self.history[-self.patience:]
            if max(recent) - min(recent) < 1e-12:
                return f"residual flat for {self.patience} sweeps at {recent[-1]:.3e}"
            return None

    stalling = Pipeline(
        solver=IterativeSolver(
            max_iterations=200,
            tolerance=1e-12,
            target_var="value",
            convergence_checker=StallDetector(),
        )
    )
    # Adds a constant each sweep: the residual is identical forever, so it is
    # "making progress" by any single-step measure and never converges.
    @stalling.step(outputs=["value"])
    def creep(value: float) -> float:
        return value + 1.0

    show(stalling.run(value=0.0))
    print()
    print("  -> 30 lines, no base class, no framework buy-in. The solver asks")
    print("     any checker that can answer, and leaves the rest alone.")

    # ==========================================================================
    # CASE 4: one report per block
    # ==========================================================================
    print("\n=== CASE 4: HybridSolver reports each cyclic block separately ===")
    layered = Pipeline(solver=HybridSolver(max_iterations=40, tolerance=1e-6))

    @layered.step(outputs=["a1"])
    def a_first(a2: float) -> float:
        return a2 * 0.5

    @layered.step(outputs=["a2"])
    def a_second(a1: float) -> float:
        return a1 + 1.0

    @layered.step(outputs=["b1"])
    def b_first(b2: float) -> float:
        return b2 * 0.25

    @layered.step(outputs=["b2"])
    def b_second(b1: float) -> float:
        return b1 + 2.0

    show(layered.run(a2=1.0, b2=1.0))
    print()
    print("  -> Two independent loops, two reports, each naming its own steps.")
    print("     A pipeline where one block converges and another gives up is")
    print("     now legible instead of being a single opaque verdict.")


if __name__ == "__main__":
    run_convergence_report_demo()
