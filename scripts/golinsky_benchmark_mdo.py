import math
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
# PART 1: Initialize the Pipeline
# ==============================================================================
# The Golinski problem is a feedforward engineering design problem.
pipeline = Pipeline(solver=DAGSolver())

# ==============================================================================
# PART 2: Define the Disciplines (Modular Decomposition)
# ==============================================================================

# -- 1. Gear Weight & Mechanics --
@pipeline.step(outputs=["gear_weight"])
def compute_gear_weight(x1: float, x2: float, x3: float) -> float:
    """Computes the weight contribution of the gears."""
    return 0.7854 * x1 * (x2**2) * (3.3333 * (x3**2) + 14.9334 * x3 - 43.0934)

@pipeline.step(outputs=["g1", "g2", "g7", "g8", "g9"])
def gear_constraints(x1: float, x2: float, x3: float):
    """
    Evaluates bending stress, contact stress, and dimensional limits for the gears.
    Pipeline convention: g(x) <= 0
    """
    g1 = 27.0 / (x1 * (x2**2) * x3) - 1.0
    g2 = 397.5 / (x1 * (x2**2) * (x3**2)) - 1.0
    g7 = (x2 * x3) / 40.0 - 1.0
    g8 = (5.0 * x2) / x1 - 1.0
    g9 = x1 / (12.0 * x2) - 1.0
    return g1, g2, g7, g8, g9

# -- 2. Shaft Weights & Mechanics --
@pipeline.step(outputs=["shaft_weight"])
def compute_shaft_weight(x1: float, x4: float, x5: float, x6: float, x7: float) -> float:
    """Computes the weight contribution of the two shafts."""
    w1 = -1.508 * x1 * (x6**2 + x7**2)
    w2 = 7.4777 * (x6**3 + x7**3)
    w3 = 0.7854 * (x4 * (x6**2) + x5 * (x7**2))
    return w1 + w2 + w3

@pipeline.step(outputs=["g3", "g5", "g10"])
def shaft1_constraints(x2: float, x3: float, x4: float, x6: float):
    """Evaluates deflection and stress limits for Shaft 1."""
    g3 = 1.93 * (x4**3) / (x2 * x3 * (x6**4)) - 1.0
    val = (745.0 * x4 / (x2 * x3))**2 + 16.9e6
    g5 = math.sqrt(val) / (110.0 * (x6**3)) - 1.0
    g10 = (1.5 * x6 + 1.9) / x4 - 1.0
    return g3, g5, g10

@pipeline.step(outputs=["g4", "g6", "g11"])
def shaft2_constraints(x2: float, x3: float, x5: float, x7: float):
    """Evaluates deflection and stress limits for Shaft 2."""
    g4 = 1.93 * (x5**3) / (x2 * x3 * (x7**4)) - 1.0
    val = (745.0 * x5 / (x2 * x3))**2 + 157.5e6
    g6 = math.sqrt(val) / (85.0 * (x7**3)) - 1.0
    g11 = (1.1 * x7 + 1.9) / x5 - 1.0
    return g4, g6, g11

# -- 3. System Objective --
@pipeline.step(outputs=["objective"])
def compute_total_weight(gear_weight: float, shaft_weight: float) -> float:
    """Total weight of the speed reducer."""
    return gear_weight + shaft_weight


# ==============================================================================
# PART 3: Optimization Execution
# ==============================================================================
def run_golinski_mdo():
    print("\n" + "="*70)
    print("--- Running Golinski Speed Reducer Benchmark ---")
    print("="*70)
    
    # Suppress pipeline INFO logs during the optimization loop
    logging.getLogger("smartmdao").setLevel(logging.WARNING)
    
    # Map the optimizer's array to the 7 design variables
    design_vars = ["x1", "x2", "x3", "x4", "x5", "x6", "x7"]
    evaluator = PipelineEvaluator(pipeline=pipeline, design_vars=design_vars)
    
    # Initial guess (mid-point of bounds)
    initial_guess = np.array([3.1, 0.75, 22.5, 7.8, 8.05, 3.4, 5.25]) 
    
    # Standard bounds for the Golinski problem
    # Note: x3 is theoretically an integer (number of teeth), but is 
    # treated continuously in standard numerical benchmarks.
    bounds = [
        (2.6, 3.6),   # x1: gear face width
        (0.7, 0.8),   # x2: teeth module
        (17.0, 28.0), # x3: number of teeth
        (7.3, 8.3),   # x4: distance between bearings 1
        (7.8, 8.3),   # x5: distance between bearings 2
        (2.9, 3.9),   # x6: diameter of shaft 1
        (5.0, 5.5)    # x7: diameter of shaft 2
    ]
    
    # Generate 11 constraints dynamically using the evaluator factory
    cons = []
    for i in range(1, 12):
        cons.append({
            'type': 'ineq', 
            # SciPy expects g(x) >= 0, so we multiply our g(x) <= 0 by -1.0
            'fun': evaluator.get_constraint(f"g{i}", multiplier=-1.0)
        })
    
    # Run Optimization
    print("Starting optimization from initial guess...")
    result = minimize(
        evaluator.get_objective("objective"), 
        initial_guess, 
        method='SLSQP', 
        bounds=bounds, 
        constraints=cons,
        options={'disp': True, 'ftol': 1e-6, 'maxiter': 200}
    )
    
    # Restore logging level
    logging.getLogger("smartmdao").setLevel(logging.INFO)
    
    # Extract Full State
    optimal_state = evaluator.evaluate(result.x)
    
    # Known Global Optimum (Continuous)
    expected = {
        "x1": 3.500000,
        "x2": 0.700000,
        "x3": 17.000000,
        "x4": 7.300000,
        "x5": 7.800000,
        "x6": 3.350214,
        "x7": 5.286683,
        "objective": 2994.471066
    }
    
    print("\n--- Final Golinski Results Comparison ---")
    print(f"Total Pipeline Runs: {evaluator.eval_count}")
    print("-" * 65)
    print(f"{'Variable':<15} | {'Expected':<12} | {'Obtained':<12} | {'Abs Error':<10}")
    print("-" * 65)
    
    # Display Design Variables
    for var in design_vars:
        obt = optimal_state[var]
        exp = expected[var]
        print(f"{var:<15} | {exp:>12.4f} | {obt:>12.4f} | {abs(obt - exp):>10.4e}")
    
    print("-" * 65)
    print(f"{'objective':<15} | {expected['objective']:>12.4f} | {optimal_state['objective']:>12.4f} | {abs(optimal_state['objective'] - expected['objective']):>10.4e}")
    print("-" * 65)
    
    # Check Active Constraints (Constraints close to 0 are the ones restricting the design)
    print("\n--- Constraints Analysis ---")
    for i in range(1, 12):
        val = optimal_state[f'g{i}']
        status = "ACTIVE (Binding)" if abs(val) < 1e-4 else "Inactive"
        print(f"g{i:<2} = {val:>10.4e} [{status}]")
    
    if result.success:
        print("\nSUCCESS! The pipeline successfully solved the Golinski engineering problem.")
    else:
        print("\nOPTIMIZATION FAILED.")

# ==============================================================================
if __name__ == "__main__":
    configure_logging(level=logging.INFO)
    run_golinski_mdo()