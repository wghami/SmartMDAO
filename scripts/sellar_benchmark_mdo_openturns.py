import math
import logging
import numpy as np
from scipy.optimize import minimize

from smart_pipeline.core import Pipeline
from smart_pipeline.solvers import HybridSolver
from smart_pipeline.logging_config import configure_logging
from smart_pipeline.optimization import PipelineEvaluator
import openturns as ot

# --- Setup Logging ---
logger = logging.getLogger(__name__)

# ==============================================================================
# PART 1: Initialize the Pipeline with the HybridSolver
# ==============================================================================
pipeline = Pipeline(
    solver=HybridSolver(max_iterations=100, tolerance=1e-6)
)

# ==============================================================================
# PART 2: Define the Sellar Disciplines
# ==============================================================================
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
    """Constraint formulation: 3.16 - y1 <= 0"""
    return 3.16 - y1

@pipeline.step(outputs=["constraint_2"])
def compute_constraint_2(y2: float) -> float:
    """Constraint formulation: y2 - 24.0 <= 0"""
    return y2 - 24.0

# ==============================================================================
# PART 3: MDO (Multidisciplinary Design Optimization) - OpenTURNS
# ==============================================================================
def run_sellar_mdo_openturns():
    print("\n" + "="*65)
    print("--- Running Sellar MDO using OpenTURNS ---")
    print("="*65)
    
    # 1. Temporarily suppress pipeline INFO logs to avoid console flooding
    logging.getLogger("smart_pipeline").setLevel(logging.WARNING)
    
    # 2. Setup the Evaluator bridge
    evaluator = PipelineEvaluator(
        pipeline=pipeline,
        design_vars=["z1", "z2", "x1"],
        constants={"y2": 1.0}
    )
    
    # 3. Define OpenTURNS Python functions
    # OpenTURNS requires mathematical functions to take a sequence and return a sequence
    def ot_objective(x):
        return [evaluator.get_objective("objective")(x)]
        
    def ot_constraints(x):
        # OpenTURNS inequality constraints convention is h(x) >= 0.
        # Sellar is defined as g(x) <= 0. So we multiply by -1.0 using the evaluator.
        c1 = evaluator.get_constraint("constraint_1", multiplier=-1.0)(x)
        c2 = evaluator.get_constraint("constraint_2", multiplier=-1.0)(x)
        return [c1, c2]

    # Create OpenTURNS Function objects (input_dim=3, output_dim=1 or 2)
    obj_fun = ot.PythonFunction(3, 1, ot_objective)
    cons_fun = ot.PythonFunction(3, 2, ot_constraints)
    
    # 4. Define Bounds
    bounds = ot.Interval([-10.0, 0.0, 0.0], [10.0, 10.0, 10.0])
    
    # 5. Formulate the Optimization Problem
    problem = ot.OptimizationProblem(obj_fun)
    problem.setBounds(bounds)
    problem.setInequalityConstraint(cons_fun)
    
    # 6. Define and Run the Solver
    # We use COBYLA, a standard derivative-free solver excellent for constrained problems
    algo = ot.Cobyla(problem)
    algo.setStartingPoint([1.0, 1.0, 1.0])
    algo.setMaximumIterationNumber(1000)
    
    print("Starting OpenTURNS COBYLA optimization...")
    algo.run()
    
    # Restore logging level
    logging.getLogger("smart_pipeline").setLevel(logging.INFO)
    
    # 7. Extract Results
    result = algo.getResult()
    x_opt = np.array(result.getOptimalPoint())
    
    # Extract Full State
    optimal_state = evaluator.evaluate(x_opt)
    
    # 8. Compare with Known Sellar Global Optimum
    expected = {
        "z1": 1.9776,
        "z2": 0.0000,
        "x1": 0.0000,
        "y1": 3.1600,
        "y2": 3.7553,
        "objective": 10.0090,
        "constraint_1": 0.0000,
        "constraint_2": -20.2447
    }
    
    print("\n--- Final OpenTURNS MDO Results Comparison ---")
    print(f"Total Pipeline Runs: {evaluator.eval_count}")
    print(f"OpenTURNS Solver Calls: {result.getCallsNumber()}")
    print("-" * 65)
    print(f"{'Variable':<15} | {'Expected':<12} | {'Obtained':<12} | {'Abs Error':<10}")
    print("-" * 65)
    
    # Display Inputs
    for var in ["z1", "z2", "x1"]:
        obt = optimal_state[var]
        exp = expected[var]
        print(f"{var:<15} | {exp:>12.4f} | {obt:>12.4f} | {abs(obt - exp):>10.4e}")
    
    print("-" * 65)
    
    # Display Intermediates and Outputs
    for var in ["y1", "y2", "objective", "constraint_1", "constraint_2"]:
        obt = optimal_state[var]
        exp = expected[var]
        print(f"{var:<15} | {exp:>12.4f} | {obt:>12.4f} | {abs(obt - exp):>10.4e}")
    
    print("-" * 65)

# ==============================================================================
if __name__ == "__main__":
    # Setup standard logging
    configure_logging(level=logging.INFO)
    
    # Run the full optimization loop with OpenTURNS
    run_sellar_mdo_openturns()