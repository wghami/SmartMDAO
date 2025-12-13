"""
Hybrid Pipeline Demonstration
Structure: Linear -> Iterative (Cycle A) -> Linear -> Iterative (Cycle B) -> Linear
"""
import math
from smart_pipeline import (
    Pipeline,
    HybridSolver,
    cached,
    HistoryBackend
)
from pathlib import Path
from dataclasses import dataclass
import matplotlib.pyplot as plt

# --- 0. Define pre processing ---

# Instantiate the backend to be used by the decorators
history_tracker = HistoryBackend()

# Output contract for the first step
@dataclass
class InitialOutput:
    """Output contract for the first step."""
    init_val: float

# --- 1. Define Pipeline Functions ---

# Linear Start
# FIX APPLIED: Added return type hint -> InitialOutput so the visualizer knows what to expect.
def initialize(initial_value: float) -> InitialOutput:
    print(f"1. [Linear] Initializing with {initial_value}")
    # Returning the dataclass instance allows the pipeline to auto-discover output names
    return InitialOutput(init_val = initial_value * 2)

# Cycle 1: A coupled system (x, y)
# x = (y + input) / 2
# y = sqrt(x + 5)
# We decorate these steps to track their values during iterations
@cached(history_tracker)
def cycle1_step_a(init_val, c1_y=1.0): 
    # c1_y defaults to 1.0 for first iteration
    res = (c1_y + init_val) / 2.0
    return res 

@cached(history_tracker)
def cycle1_step_b(c1_x):
    return math.sqrt(c1_x + 5.0)

# Linear Middle
def transform_middle(c1_x, c1_y):
    val = c1_x + c1_y
    print(f"2. [Linear] Middle Transform: {c1_x:.4f} + {c1_y:.4f} = {val:.4f}")
    return val

# Cycle 2: Feedback loop (z)
# z_new = z_prev^0.5 + middle_val
@cached(history_tracker)
def cycle2_loop(middle_val, z=1.0):
    return math.sqrt(z) * 0.5 + (middle_val * 0.1)

# Linear End
def finalize(z):
    print(f"3. [Linear] Finalizing result: {z:.4f}")
    return f"Final Result: {z:.4f}"

# --- 2. Build Pipeline ---

# Note: We add them in 'story' order, but the HybridSolver 
# calculates the mathematical dependencies automatically.
pipe = Pipeline(solver=HybridSolver(tolerance=1e-5))

# A. Linear
# FIX APPLIED: Removed outputs=["init_val"]. 
# The pipeline now infers "init_val" from the InitialOutput dataclass.
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

# --- 3. Execute ---

print("=== Starting Hybrid Pipeline Execution ===\n")

# We provide inputs. 
# Note: Cyclic variables (c1_y, z) don't need initial inputs if the functions handle defaults,
# or we can provide initial guesses in `run()` to seed the loop faster.
results = pipe.run(initial_value=10.0)

print("\n=== Execution Finished ===")
print(results["final_msg"])
print("\nConvergence History (Residuals per iterative block):")
for i, residuals in enumerate(results.get("residual_history", [])):
    print(f"Block {i+1}: {len(residuals)} iterations to converge. Last residual: {residuals[-1]:.6e}")

# --- 4. Visualize Convergence History ---

print("\nGenerating convergence plots...")

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

# Show the plot window instead of saving
print("Displaying plots...")
plt.show()

# --- 5. Generate Pipeline Diagram ---
print("\nGenerating interactive diagram...")
pipe.visualize(inputs=["initial_value"],  # If not provided, will try to infer and use a (?) in the graph
                output_path=str(Path("results") / "test_hybrid_flow.pdf"),
                orientation="TB",
                graph_type="bipartite",
                view=False)  # Set view=True to open automatically if supported