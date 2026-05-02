import logging
import numpy as np
from scipy.optimize import minimize

from smartmdao.core import Pipeline
from smartmdao.solvers import DAGSolver
from smartmdao.logging_config import configure_logging
from smartmdao.optimization import PipelineEvaluator

# --- Setup Logging ---
logger = logging.getLogger(__name__)

# ==============================================================================
# PART 1: Initialize the Pipeline with the DAGSolver
# ==============================================================================
# HS71 is a feedforward problem (no cyclic dependencies). 
# We explicitly use the DAGSolver for maximum performance.
pipeline = Pipeline(solver=DAGSolver())

# ==============================================================================
# PART 2: Define the HS71 Computational Graph
# ==============================================================================
# Objective: min f(x) = x1 * x4 * (x1 + x2 + x3) + x3
# We break this into two steps to show pipeline data flow.

@pipeline.step(outputs=["sum_123"])
def compute_sum(x1: float, x2: float, x3: float) -> float:
    """Intermediate step: computes (x1 + x2 + x3)"""
    return x1 + x2 + x3

@pipeline.step(outputs=["objective"])
def compute_objective(x1: float, x3: float, x4: float, sum_123: float) -> float:
    """Final objective evaluation."""
    return x1 * x4 * sum_123 + x3

# Inequality Constraint: x1*x2*x3*x4 >= 25
@pipeline.step(outputs=["ineq_constraint"])
def compute_inequality(x1: float, x2: float, x3: float, x4: float) -> float:
    """Pipeline convention: g(x) <= 0. Thus: 25.0 - x1*x2*x3*x4 <= 0"""
    return 25.0 - (x1 * x2 * x3 * x4)

# Equality Constraint: x1^2 + x2^2 + x3^2 + x4^2 = 40
@pipeline.step(outputs=["eq_constraint"])
def compute_equality(x1: float, x2: float, x3: float, x4: float) -> float:
    """Pipeline convention: h(x) = 0."""
    return (x1**2 + x2**2 + x3**2 + x4**2) - 40.0


# ==============================================================================
# PART 3: MDO (Multidisciplinary Design Optimization) - SciPy
# ==============================================================================
def run_hs71_mdo():
    print("\n" + "="*65)
    print("--- Running HS71 Optimization Benchmark ---")
    print("="*65)
    
    # 1. Suppress pipeline INFO logs during the optimization loop
    logging.getLogger("smartmdao").setLevel(logging.WARNING)
    
    # 2. Setup the Evaluator bridge
    # We map the optimizer's array directly to our 4 design variables
    evaluator = PipelineEvaluator(
        pipeline=pipeline,
        design_vars=["x1", "x2", "x3", "x4"]
    )
    
    # 3. Setup Optimizer parameters
    # The standard initial guess for HS71
    initial_guess = np.array([1.0, 5.0, 5.0, 1.0]) 
    
    # Bounds: 1.0 <= xi <= 5.0 for all variables
    bounds = [(1.0, 5.0), (1.0, 5.0), (1.0, 5.0), (1.0, 5.0)]
    
    # Constraints for scipy.optimize
    cons = [
        # SciPy inequality expects g(x) >= 0, so we multiply our output by -1.0
        {'type': 'ineq', 'fun': evaluator.get_constraint("ineq_constraint", multiplier=-1.0)},
        # SciPy equality expects h(x) = 0. No multiplier needed.
        {'type': 'eq', 'fun': evaluator.get_constraint("eq_constraint")}
    ]
    
    # 4. Run Optimization
    print(f"Starting optimization from initial guess: {initial_guess}")
    result = minimize(
        evaluator.get_objective("objective"), 
        initial_guess, 
        method='SLSQP', 
        bounds=bounds, 
        constraints=cons,
        options={'disp': True, 'ftol': 1e-6}
    )
    
    # Restore logging level
    logging.getLogger("smartmdao").setLevel(logging.INFO)
    
    # 5. Extract Full State at Optimum
    optimal_state = evaluator.evaluate(result.x)
    
    # 6. Compare with Known HS71 Global Optimum
    expected = {
        "x1": 1.0000,
        "x2": 4.7430,
        "x3": 3.8211,
        "x4": 1.3794,
        "sum_123": 9.5641,
        "objective": 17.0140,
        "ineq_constraint": 0.0000, # Should be active (tight against 25)
        "eq_constraint": 0.0000    # Should be exactly 0
    }
    
    print("\n--- Final HS71 Results Comparison ---")
    print(f"Total Pipeline Runs: {evaluator.eval_count}")
    print("-" * 65)
    print(f"{'Variable':<15} | {'Expected':<12} | {'Obtained':<12} | {'Abs Error':<10}")
    print("-" * 65)
    
    # Display Design Variables
    for var in ["x1", "x2", "x3", "x4"]:
        obt = optimal_state[var]
        exp = expected[var]
        print(f"{var:<15} | {exp:>12.4f} | {obt:>12.4f} | {abs(obt - exp):>10.4e}")
    
    print("-" * 65)
    
    # Display Intermediates and Outputs
    for var in ["sum_123", "objective", "ineq_constraint", "eq_constraint"]:
        obt = optimal_state[var]
        exp = expected[var]
        print(f"{var:<15} | {exp:>12.4f} | {obt:>12.4f} | {abs(obt - exp):>10.4e}")
    
    print("-" * 65)
    
    if result.success:
        print("\nSUCCESS! The pipeline successfully solved the HS71 problem.")
    else:
        print("\nOPTIMIZATION FAILED.")

# ==============================================================================
if __name__ == "__main__":
    configure_logging(level=logging.INFO)
    run_hs71_mdo()