import logging

from smartmdao import (
    HybridSolver,
    IterativeSolver,
    OscillationAwareConvergenceChecker,
    OscillationDetectedError,
    Pipeline,
    configure_logging,
    validate,
)

logger = logging.getLogger(__name__)

# ==============================================================================
# WHY THIS DEMO EXISTS
# ==============================================================================
# `target_var` narrows convergence to a single coupling variable instead of
# taking a max() across every produced variable. Two reasons to want it:
#
#   1. One noisy variable otherwise holds the whole system back.
#   2. A STATEFUL ConvergenceChecker needs distance() called exactly once per
#      iteration. Without a target it is called once per produced variable, in
#      arbitrary set order, so a checker keeping history sees interleaved
#      values from different variables and its history means nothing.
#
# Until 1.8.0 only IterativeSolver accepted it. HybridSolver built its
# sub-solvers without forwarding it, so you had to choose: automatic cycle
# detection, OR oscillation detection. Not both. This closes that.
#
# The interesting part is the guard. Forwarding a target to a block that does
# not produce it would be silently catastrophic, and CASE 3 shows why.
# ==============================================================================


def build_naive_agent_loop(solver) -> Pipeline:
    """
    A model that flip-flops between two architectures forever - the realistic
    failure mode of putting a language model in a feedback loop.
    """
    pipeline = Pipeline(solver=solver)

    @pipeline.step(outputs=["architecture"])
    def propose(violations: frozenset, architecture: str) -> str:
        return "B" if architecture == "A" else "A"

    @pipeline.step(outputs=["violations"])
    def evaluate(architecture: str) -> frozenset:
        return frozenset({"still_wrong"})

    return pipeline


def run_hybrid_target_var_demo():
    configure_logging(level=logging.CRITICAL)   # the aborts below are expected

    # ==========================================================================
    # CASE 1: what you had to give up before
    # ==========================================================================
    print("=== CASE 1: HybridSolver WITHOUT a target - the checker is blind ===")
    pipeline = build_naive_agent_loop(
        HybridSolver(
            max_iterations=30,
            convergence_checker=OscillationAwareConvergenceChecker(
                raise_on_detection=False
            ),
        )
    )
    result = pipeline.run(architecture="A", violations=frozenset())
    sweeps = len(result["residual_history"][-1])
    print(f"  sweeps used : {sweeps} of 30")
    print("  detection   : never fires")
    print()
    print("  -> Without a target, distance() is called once per produced")
    print("     variable ('architecture' AND 'violations') in arbitrary order.")
    print("     The checker's history is a meaningless interleaving, so the")
    print("     2-cycle is invisible and the loop runs to max_iterations.")

    # ==========================================================================
    # CASE 2: the same pipeline, with the target forwarded
    # ==========================================================================
    print("\n=== CASE 2: HybridSolver WITH target_var - detection works ===")
    pipeline = build_naive_agent_loop(
        HybridSolver(
            max_iterations=30,
            target_var="architecture",
            convergence_checker=OscillationAwareConvergenceChecker(),
        )
    )
    try:
        pipeline.run(architecture="A", violations=frozenset())
        print("  unexpected: no detection")
    except OscillationDetectedError as error:
        print(f"  detected    : period {error.period}, cycling {error.cycle}")
        print(f"  sweeps used : {error.iteration} of 30")
        print()
        print("  -> One call per sweep, so the history is coherent and the")
        print("     2-cycle is caught after two full repetitions. Automatic")
        print("     SCC detection AND oscillation detection, together.")

    # ==========================================================================
    # CASE 3: why the target is scoped to one block
    # ==========================================================================
    print("\n=== CASE 3: the guard - a target is only given to its own block ===")
    print("  If a target were handed to a block that does not produce it, that")
    print("  block's snapshot would have no entry for the name. The residual")
    print("  becomes distance(None, None) == 0.0 - and 0.0 means CONVERGED.")
    print("  The block would report success on sweep 1 without iterating once.")
    print()

    pipeline = Pipeline(solver=HybridSolver(max_iterations=40, target_var="elsewhere"))

    @pipeline.step(outputs=["y1"])
    def a_discipline(y2: float) -> float:
        return y2 * 0.5

    @pipeline.step(outputs=["y2"])
    def z_discipline(y1: float) -> float:
        return y1 + 1.0

    result = pipeline.run(y2=1.0)
    sweeps = len(result["residual_history"][-1])
    print(f"  target_var  : 'elsewhere' (produced by nothing)")
    print(f"  sweeps used : {sweeps}  <- really iterated, not a fake convergence")
    print(f"  converged   : y2={result['y2']:.4f} (exact answer is 2.0)")
    print()
    print("  -> The block produces y1 and y2, not 'elsewhere', so it keeps the")
    print("     default max() and converges honestly. A warning is logged so")
    print("     the unmet intent is not silent.")

    # ==========================================================================
    # CASE 4: validate() catches the mistake before you run anything
    # ==========================================================================
    print("\n=== CASE 4: the same mistakes, caught statically ===")

    print("  a) custom checker, no target:")
    suspect = build_naive_agent_loop(
        HybridSolver(convergence_checker=OscillationAwareConvergenceChecker())
    )
    for finding in validate(suspect, inputs=["architecture", "violations"]):
        print(f"     [{finding.severity}] {finding.code}")

    print("  b) target that no cycle produces:")
    mistargeted = Pipeline(solver=HybridSolver(target_var="elsewhere"))
    mistargeted.add(a_discipline, outputs=["y1"])
    mistargeted.add(z_discipline, outputs=["y2"])
    for finding in validate(mistargeted, inputs=["y2"]):
        print(f"     [{finding.severity}] {finding.code}")

    print("  c) IterativeSolver has a wider scope - it sweeps every step, so")
    print("     any produced variable is targetable, not just cyclic ones:")
    iterative = Pipeline(solver=IterativeSolver(target_var="ghost"))
    iterative.add(a_discipline, outputs=["y1"])
    iterative.add(z_discipline, outputs=["y2"])
    for finding in validate(iterative, inputs=["y2"]):
        print(f"     [{finding.severity}] {finding.code}")

    print()
    print("  -> Note what is NOT reported: HybridSolver with the standard")
    print("     checker and no target. That is the idiomatic default, it works,")
    print("     and flagging it would fire on almost every correct pipeline.")


if __name__ == "__main__":
    run_hybrid_target_var_demo()
