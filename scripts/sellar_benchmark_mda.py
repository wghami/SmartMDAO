import math
import logging
from smart_pipeline.core import Pipeline
from smart_pipeline.solvers import HybridSolver
from smart_pipeline.logging_config import configure_logging

# --- Setup Logging ---
# Initialize module-level logger
logger = logging.getLogger(__name__)

# ==============================================================================
# PART 1: Initialize the Pipeline with the HybridSolver
# ==============================================================================
# The HybridSolver is crucial here because of the cyclic dependency y1 <-> y2
pipeline = Pipeline(
    solver=HybridSolver(max_iterations=100, tolerance=1e-6)
)

# ==============================================================================
# PART 2: Define the Sellar Disciplines using decorator
# ==============================================================================
@pipeline.step(outputs=["y1"])
def discipline_1(z1: float, z2: float, x1: float, y2: float) -> float:
    """Computes state variable y1."""
    return (z1 ** 2) + z2 + x1 - (0.2 * y2)

@pipeline.step(outputs=["y2"])
def discipline_2(z1: float, z2: float, y1: float) -> float:
    """Computes state variable y2."""
    # Using abs() to prevent math domain errors during iterative convergence
    return math.sqrt(abs(y1)) + z1 + z2

@pipeline.step(outputs=["objective"])
def compute_objective(x1: float, z2: float, y1: float, y2: float) -> float:
    """The global objective function we eventually want to minimize."""
    return (x1 ** 2) + z2 + (y1 ** 2) + math.exp(-y2)

@pipeline.step(outputs=["constraint_1"])
def compute_constraint_1(y1: float) -> float:
    """Constraint: 3.16 - y1 <= 0"""
    return 3.16 - y1

@pipeline.step(outputs=["constraint_2"])
def compute_constraint_2(y2: float) -> float:
    """Constraint: y2 - 24.0 <= 0"""
    return y2 - 24.0

# ==============================================================================
# PART 3: Execution
# ==============================================================================
def run_sellar_benchmark():
    # Initialize the centralized logging configuration
    # PRO TIP: Change to logging.DEBUG to see the cache hits/misses in action!
    configure_logging(level=logging.INFO)

    logger.info("--- Running Sellar Multidisciplinary Analysis (MDA) ---")
    
    # We provide the global (z) and local (x) design variables.
    # We also provide an initial 'guess' for y2 to kick-start the cycle.
    inputs = {
        "z1": 1.9776,
        "z2": 0.0,
        "x1": 0.0,
        "y2": 1.0  # Initial guess for the cyclic solver
    }
    
    # Execute the pipeline!
    # Expected behavior: HybridSolver will detect the d1<->d2 cycle, 
    # iterate them to convergence, and then compute the objective and constraints.
    results = pipeline.run(**inputs)
    
    logger.info("\n--- Final Results ---")
    logger.info(f"y1         = {results['y1']:.4f} (Expected: ~3.16)")
    logger.info(f"y2         = {results['y2']:.4f} (Expected: ~3.75)")
    logger.info(f"Objective  = {results['objective']:.4f} (Expected: ~10.00)")
    logger.info(f"Constraint 1 = {results['constraint_1']:.4f} (Expected: <= 0)")
    logger.info(f"Constraint 2 = {results['constraint_2']:.4f} (Expected: <= 0)")
    
    # Optional: Generate your Graphviz diagram!
    pipeline.visualize(inputs=list(inputs.keys()),
                       output_path="sellar_mda",
                       orientation="LR",
                       graph_type="bipartite")

# 4. Run the Benchmark
if __name__ == "__main__":
    run_sellar_benchmark()