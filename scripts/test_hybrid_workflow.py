"""
Hybrid Pipeline Demonstration
Structure: Linear -> Iterative (Cycle A) -> Linear -> Iterative (Cycle B) -> Linear
"""
import math
import logging
from pathlib import Path
from dataclasses import dataclass
import matplotlib.pyplot as plt

from smart_pipeline import (
    Pipeline,
    HybridSolver,
    cached,
    HistoryBackend,
    configure_logging
)

# Initialize module-level logger
logger = logging.getLogger(__name__)

# ==========================================
# 1. Global Setup & Step Definitions
# ==========================================

# Instantiate the backend globally so decorators can register it immediately
history_tracker = HistoryBackend()

# Output contract for the first step
@dataclass
class InitialOutput:
    """Output contract for the first step."""
    init_val: float

# --- Step Definitions ---

# Linear Start
def initialize(initial_value: float) -> InitialOutput:
    logger.info(f"1. [Linear] Initializing with {initial_value}")
    return InitialOutput(init_val = initial_value * 2)

# Cycle 1: A coupled system (x, y)
@cached(history_tracker)
def cycle1_step_a(init_val, c1_y=1.0): 
    res = (c1_y + init_val) / 2.0
    return res 

@cached(history_tracker)
def cycle1_step_b(c1_x):
    return math.sqrt(c1_x + 5.0)

# Linear Middle
def transform_middle(c1_x, c1_y):
    val = c1_x + c1_y
    logger.info(f"2. [Linear] Middle Transform: {c1_x:.4f} + {c1_y:.4f} = {val:.4f}")
    return val

# Cycle 2: Feedback loop (z)
@cached(history_tracker)
def cycle2_loop(middle_val, z=1.0):
    return math.sqrt(z) * 0.5 + (middle_val * 0.1)

# Linear End
def finalize(z):
    logger.info(f"3. [Linear] Finalizing result: {z:.4f}")
    return f"Final Result: {z:.4f}"


# ==========================================
# 2. Main Execution Logic
# ==========================================
def run_hybrid_workflow_demo():
    # 0. Configure Logging
    configure_logging(level=logging.INFO)
    
    # 1. Build Pipeline
    # We use HybridSolver to handle the mix of linear and cyclic steps
    pipe = Pipeline(solver=HybridSolver(tolerance=1e-5))

    # A. Linear
    pipe.add(initialize)

    # B. Iterative Block 1 (Coupled inputs)
    pipe.add(cycle1_step_a, outputs=["c1_x"])
    pipe.add(cycle1_step_b, outputs=["c1_y"])

    # C. Linear
    pipe.add(transform_middle, outputs=["middle_val"])

    # D. Iterative Block 2
    pipe.add(cycle2_loop, outputs=["z"])

    # E. Linear
    pipe.add(finalize, outputs=["final_msg"])

    # 2. Execute
    logger.info("=== Starting Hybrid Pipeline Execution ===")

    # Run the pipeline
    results = pipe.run(initial_value=10.0)

    logger.info("=== Execution Finished ===")
    logger.info(results["final_msg"])

    # Print convergence history using logger
    logger.info("Convergence History:")
    for i, residuals in enumerate(results.get("residual_history", [])):
        logger.info(f"  Block {i+1}: {len(residuals)} iterations to converge. Last residual: {residuals[-1]:.6e}")

    # 3. Visualize Convergence History
    logger.info("Generating convergence plots...")

    # Retrieve history from our custom backend
    history_x = history_tracker.history.get('cycle1_step_a', [])
    history_y = history_tracker.history.get('cycle1_step_b', [])
    history_z = history_tracker.history.get('cycle2_loop', [])

    # Plotting
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # Plot Cycle 1
    if history_x and history_y:
        ax1.plot(history_x, 'o-', label='c1_x (Step A)')
        ax1.plot(history_y, 's-', label='c1_y (Step B)')
        ax1.set_title(f'Cycle 1 Convergence ({len(history_x)} iters)')
        ax1.set_xlabel('Iteration')
        ax1.set_ylabel('Value')
        ax1.legend()
        ax1.grid(True, linestyle='--', alpha=0.7)

    # Plot Cycle 2
    if history_z:
        ax2.plot(history_z, '^-', color='green', label='z (Loop)')
        ax2.set_title(f'Cycle 2 Convergence ({len(history_z)} iters)')
        ax2.set_xlabel('Iteration')
        ax2.set_ylabel('Value')
        ax2.legend()
        ax2.grid(True, linestyle='--', alpha=0.7)

    plt.tight_layout()

    logger.info("Displaying plots...")
    plt.show()

    # 4. Generate Pipeline Diagram
    logger.info("Generating interactive diagram...")
    pipe.visualize(inputs=["initial_value"],
                    output_path=str(Path("results") / "test_hybrid_flow.pdf"),
                    orientation="TB",
                    graph_type="bipartite",
                    view=False)

if __name__ == "__main__":
    run_hybrid_workflow_demo()