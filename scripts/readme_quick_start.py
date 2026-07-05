"""
The exact Sellar workflow shown in the README's collapsed Quick Start
section - caching, PipelineEvaluator, SciPy optimization with constraint
sign flipping, visualization, and running the same problem through a
second backend - kept here so it's continuously exercised by run_all.py
and never silently drifts from the documentation.
"""
import math
import logging
from pathlib import Path
from scipy.optimize import minimize
from smartmdao import (
    Pipeline,
    HybridSolver,
    PipelineEvaluator,
    OptimizationProblem,
    ConstraintSpec,
    optimize,
    cached,
    MemoryBackend,
    configure_logging
)

# --- Setup Logging and Cache ---
mem_cache = MemoryBackend()  # HDF5 and Pickle also available

# ==============================================================================
# PART 1: Initialize the Pipeline with the HybridSolver
# ==============================================================================
# The HybridSolver automatically detects and converges cyclic dependencies
pipeline = Pipeline(
    solver=HybridSolver(max_iterations=100, tolerance=1e-6)
)

# ==============================================================================
# PART 2: Define the Sellar Disciplines (MDA)
# ==============================================================================
@pipeline.step(outputs=["y1"])
@cached(mem_cache)  # Instantly cache this discipline to speed up evaluations
def discipline_1(z1: float, z2: float, x1: float, y2: float) -> float:
    return (z1 ** 2) + z2 + x1 - (0.2 * y2)

@pipeline.step(outputs=["y2"])
@cached(mem_cache)
def discipline_2(z1: float, z2: float, y1: float) -> float:
    return math.sqrt(abs(y1)) + z1 + z2

@pipeline.step(outputs=["objective"])
@cached(mem_cache)
def compute_objective(x1: float, z2: float, y1: float, y2: float) -> float:
    return (x1 ** 2) + z2 + (y1 ** 2) + math.exp(-y2)

@pipeline.step(outputs=["constraint_1"])
@cached(mem_cache)
def compute_constraint_1(y1: float) -> float:
    """Constraint formulation: 3.16 - y1 <= 0"""
    return 3.16 - y1

@pipeline.step(outputs=["constraint_2"])
@cached(mem_cache)
def compute_constraint_2(y2: float) -> float:
    """Constraint formulation: y2 - 24.0 <= 0"""
    return y2 - 24.0


def run_readme_quick_start():
    configure_logging(level=logging.WARNING)

    # ==============================================================================
    # PART 3: Setup the Evaluator Bridge
    # ==============================================================================
    # Map the optimizer's numeric array back to our named design variables
    evaluator = PipelineEvaluator(
        pipeline=pipeline,
        design_vars=["z1", "z2", "x1"],
        constants={"y2": 1.0}  # Initial guess to kick off the cycle
    )

    # Setup Optimizer parameters
    initial_guess = [1.0, 1.0, 1.0]
    bounds = [(-10.0, 10.0), (0.0, 10.0), (0.0, 10.0)]

    # Constraints for scipy.optimize
    # SciPy expects f(x) >= 0. Since our pipeline outputs f(x) <= 0, we use multiplier=-1.0
    # The evaluator returns a callable function so the optimizer can access it
    cons = [
        {'type': 'ineq', 'fun': evaluator.get_constraint("constraint_1", multiplier=-1.0)},
        {'type': 'ineq', 'fun': evaluator.get_constraint("constraint_2", multiplier=-1.0)}
    ]

    # ==============================================================================
    # PART 4: Run Optimization (MDO)
    # ==============================================================================
    print(f"Starting scipy optimization from initial guess: {initial_guess}")
    result = minimize(
        evaluator.get_objective("objective"),
        initial_guess,
        method='SLSQP',
        bounds=bounds,
        constraints=cons,
        options={'disp': True, 'ftol': 1e-6}
    )

    # ==============================================================================
    # PART 5: Extract Full State at Optimum
    # ==============================================================================
    # By passing the optimal 'x' back to the evaluator, we retrieve the full dictionary
    # of intermediate variables, constraints, and objective values.
    # Results automatically recovered from cache (no additional run).
    # Change log to DEBUG to see cache hit.
    optimal_state = evaluator.evaluate(result.x)

    print("\nOptimization Success! Final State:")
    for key, value in optimal_state.items():
        if isinstance(value, float):
            print(f"  {key}: {value:.4f}")
        else:
            print(f"  {key}: {value}")

    # Optional - visualizing the workflow
    pipeline.visualize(inputs=["z1", "z2", "x1"],  # <-- if not provided, pipeline tries to infer it
        output_path=str(Path("results") / f"{Path(__file__).stem}.svg"),  # choose your format svg, pdf, png
        orientation="LR",
        graph_type="bipartite",
        view=False)

    # ==============================================================================
    # PART 6 (Bonus): Run the *same* problem through two different backends
    # ==============================================================================
    # Wrap the same evaluator, bounds, and constraints into a backend-agnostic
    # OptimizationProblem, then swap the solver with a single string.
    problem = OptimizationProblem(
        evaluator=evaluator,
        initial_guess=initial_guess,
        bounds=bounds,
        objective="objective",
        constraints=[
            ConstraintSpec(name="constraint_1", multiplier=-1.0),
            ConstraintSpec(name="constraint_2", multiplier=-1.0),
        ],
    )

    for backend_name in ("scipy", "openturns"):
        backend_result = optimize(problem, backend=backend_name)
        print(f"[{backend_name:>9}] objective={backend_result.objective_value:.4f}")


if __name__ == "__main__":
    run_readme_quick_start()
