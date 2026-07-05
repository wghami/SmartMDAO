"""
A tour of SmartMDAO's backend-agnostic optimization layer.

Define an OptimizationProblem once - a design-agnostic description built on
top of a PipelineEvaluator (design vars, bounds, objective, constraints) -
and hand it to `optimize()`. Which optimizer actually runs is just a string:

    optimize(problem, backend="scipy")
    optimize(problem, backend="openturns")

Backends are looked up in a small registry populated via `@register_backend`,
the same "name it once, use it everywhere" pattern as `@pipeline.step`. You
are never locked into the two built-ins: register your own backend the same
way, or pass an object implementing `OptimizerBackend` directly.
"""
import logging
import math
import random

from smartmdao import (
    ConstraintSpec,
    HybridSolver,
    OptimizationProblem,
    OptimizationResult,
    Pipeline,
    PipelineEvaluator,
    configure_logging,
    optimize,
    register_backend,
)

logger = logging.getLogger(__name__)


# ==============================================================================
# PART 1: Define the Sellar MDA once - completely optimizer-agnostic
# ==============================================================================
def build_sellar_problem() -> OptimizationProblem:
    pipeline = Pipeline(solver=HybridSolver(max_iterations=100, tolerance=1e-6))

    @pipeline.step(outputs=["y1"])
    def discipline_1(z1: float, z2: float, x1: float, y2: float) -> float:
        return (z1 ** 2) + z2 + x1 - (0.2 * y2)

    @pipeline.step(outputs=["y2"])
    def discipline_2(z1: float, z2: float, y1: float) -> float:
        return math.sqrt(abs(y1)) + z1 + z2

    @pipeline.step(outputs=["objective"])
    def compute_objective(x1: float, z2: float, y1: float, y2: float) -> float:
        return (x1 ** 2) + z2 + (y1 ** 2) + math.exp(-y2)

    @pipeline.step(outputs=["constraint_1"])
    def compute_constraint_1(y1: float) -> float:
        return 3.16 - y1

    @pipeline.step(outputs=["constraint_2"])
    def compute_constraint_2(y2: float) -> float:
        return y2 - 24.0

    evaluator = PipelineEvaluator(
        pipeline=pipeline,
        design_vars=["z1", "z2", "x1"],
        constants={"y2": 1.0},
    )

    return OptimizationProblem(
        evaluator=evaluator,
        initial_guess=[1.0, 1.0, 1.0],
        bounds=[(-10.0, 10.0), (0.0, 10.0), (0.0, 10.0)],
        objective="objective",
        # Both backends expect h(x) >= 0; Sellar's constraints are naturally
        # written as g(x) <= 0, so we flip the sign with multiplier=-1.0.
        constraints=[
            ConstraintSpec(name="constraint_1", multiplier=-1.0),
            ConstraintSpec(name="constraint_2", multiplier=-1.0),
        ],
    )


# ==============================================================================
# PART 2: Run the *same* problem through two different backends
# ==============================================================================
def demo_swap_backend_by_name():
    logger.info("=== PART 1: Same OptimizationProblem, swap the backend with one string ===")

    for backend_name in ("scipy", "openturns"):
        # A fresh evaluator per run: PipelineEvaluator is stateful (it caches
        # the last evaluation), so each backend gets a clean one to run.
        problem = build_sellar_problem()
        result = optimize(problem, backend=backend_name)
        rounded_x = [round(v, 4) for v in result.x]
        logger.info(
            f"[{backend_name:>9}] x={rounded_x} objective={result.objective_value:.4f} success={result.success}"
        )


# ==============================================================================
# PART 3: Backend-specific tuning still flows through, via **options
# ==============================================================================
def demo_backend_specific_options():
    logger.info("=== PART 2: Backend-specific options pass straight through ===")

    problem = build_sellar_problem()
    result = optimize(problem, backend="scipy", tol=1e-8, options={"maxiter": 200})
    logger.info(f"scipy (tol=1e-8, maxiter=200): objective={result.objective_value:.4f}")

    problem = build_sellar_problem()
    result = optimize(problem, backend="openturns", max_iterations=2000)
    logger.info(f"openturns (max_iterations=2000): objective={result.objective_value:.4f}")


# ==============================================================================
# PART 4: Bring your own optimizer - register it once, use it like a built-in
# ==============================================================================
def demo_custom_backend():
    logger.info("=== PART 3: Registering a custom backend ===")

    @register_backend("random_search")
    class RandomSearchBackend:
        """A deliberately naive backend: samples random feasible points and keeps the best."""
        def solve(self, problem: OptimizationProblem, n_samples: int = 500, seed: int = 0) -> OptimizationResult:
            rng = random.Random(seed)
            objective_fn = problem.evaluator.get_objective(problem.objective)
            constraint_fns = [problem.evaluator.get_constraint(c.name, c.multiplier) for c in problem.constraints]

            best_x, best_value = None, float("inf")
            for _ in range(n_samples):
                x = [rng.uniform(lo, hi) for lo, hi in problem.bounds]
                if all(fn(x) >= 0 for fn in constraint_fns):
                    value = objective_fn(x)
                    if value < best_value:
                        best_x, best_value = x, value

            x_opt = best_x or problem.initial_guess
            return OptimizationResult(
                x=x_opt,
                objective_value=best_value,
                success=best_x is not None,
                message=f"Best of {n_samples} random samples.",
                state=problem.evaluator.evaluate(x_opt),
            )

    problem = build_sellar_problem()
    result = optimize(problem, backend="random_search", n_samples=500)
    logger.info(f"random_search: objective={result.objective_value:.4f} ({result.message})")


# ==============================================================================
# PART 5: Typos are caught immediately, with the list of valid backends
# ==============================================================================
def demo_unknown_backend_error():
    logger.info("=== PART 4: An unknown backend name fails loudly, not silently ===")
    problem = build_sellar_problem()
    try:
        optimize(problem, backend="scippy")
    except ValueError as e:
        logger.info(f"Caught expected error: {e}")


# ==============================================================================
# MAIN EXECUTION
# ==============================================================================
def run_optimizer_backends_demo():
    configure_logging(level=logging.INFO)
    # Each backend calls the pipeline many times per optimization; mute its
    # internal per-run logs so only this script's own commentary shows up.
    # (Set back to logging.DEBUG here if you want to see every pipeline run.)
    logging.getLogger("smartmdao").setLevel(logging.WARNING)

    demo_swap_backend_by_name()
    demo_backend_specific_options()
    demo_custom_backend()
    demo_unknown_backend_error()


if __name__ == "__main__":
    run_optimizer_backends_demo()
