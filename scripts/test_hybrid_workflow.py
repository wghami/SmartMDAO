"""
Hybrid Pipeline Demonstration
Structure: Linear -> Iterative (Cycle A) -> Linear -> Iterative (Cycle B) -> Linear
"""
import math
from smart_pipeline import Pipeline, HybridSolver
from pathlib import Path

# --- 1. Define Pipeline Functions ---

# Linear Start
def initialize(initial_value: float):
    print(f"1. [Linear] Initializing with {initial_value}")
    return initial_value * 2

# Cycle 1: A coupled system (x, y)
# x = (y + input) / 2
# y = sqrt(x + 5)
def cycle1_step_a(init_val, c1_y=1.0): 
    # c1_y defaults to 1.0 for first iteration
    res = (c1_y + init_val) / 2.0
    return res 

def cycle1_step_b(c1_x):
    return math.sqrt(c1_x + 5.0)

# Linear Middle
def transform_middle(c1_x, c1_y):
    val = c1_x + c1_y
    print(f"2. [Linear] Middle Transform: {c1_x:.4f} + {c1_y:.4f} = {val:.4f}")
    return val

# Cycle 2: Feedback loop (z)
# z_new = z_prev^0.5 + middle_val
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
pipe.add(initialize, outputs=["init_val"])

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

# Visualizatio diagram
print("\nGenerating interactive diagram...")
pipe.visualize(inputs=["x", "a"],
                output_path=str(Path("results") / "test_hybrid_flow.svg"),
                graph_type="bipartite")