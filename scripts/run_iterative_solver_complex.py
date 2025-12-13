import matplotlib.pyplot as plt
from pathlib import Path
from smart_pipeline import Pipeline, HybridSolver

# ==============================================================================
# 1. DEFINE A COUPLED SYSTEM (Cyclic Dependencies)
# ==============================================================================
# System:
# x = 0.5 * y + 2
# y = 0.5 * z - 1
# z = 0.5 * x + 4
#
# Analytical Solution: x ≈ 2.8571, y ≈ 1.7143, z ≈ 5.4286

def compute_x(y, history_x):
    val = 0.5 * y + 2
    history_x.append(val)
    return val

def compute_y(z, history_y):
    val = 0.5 * z - 1
    history_y.append(val)
    return val

def compute_z(x, history_z):
    val = 0.5 * x + 4
    history_z.append(val)
    return val

# ==============================================================================
# 2. CONFIGURE PIPELINE WITH HYBRID SOLVER
# ==============================================================================

# We use HybridSolver instead of IterativeSolver manually.
# This allows the system to auto-detect that these 3 functions form a cycle.
solver_config = HybridSolver(
    tolerance=1e-6,
    max_iterations=50
)

pipe = Pipeline(solver=solver_config)

# Add steps in ANY order. The HybridSolver resolves dependencies.
pipe.add(compute_y, outputs=['y'])
pipe.add(compute_x, outputs=['x'])
pipe.add(compute_z, outputs=['z'])

# ==============================================================================
# EXPLANATION: How is Execution Order Chosen?
# ==============================================================================
# 1. Dependency Analysis: 
#    The solver builds a graph where nodes are steps and edges are data dependencies.
#    Since x needs y, y needs z, and z needs x, it detects a Strongly Connected Component (Cycle).
#
# 2. Hybrid Decomposition:
#    - Linear parts (if any) are executed first/last based on Topological Sort.
#    - Cyclic parts are isolated into a "super-node" and executed iteratively.
#
# 3. Intra-Cycle Order:
#    Inside the detected cycle {compute_x, compute_y, compute_z}, the execution 
#    order defaults to alphanumeric sort of the function names to ensure determinism:
#    Order: compute_x -> compute_y -> compute_z.
# ==============================================================================

# ==============================================================================
# 3. RUN THE SIMULATION
# ==============================================================================

initial_state = {
    'x': 0, 'y': 0, 'z': 0,
    'history_x': [0], # Track history for plotting
    'history_y': [0],
    'history_z': [0]
}

print("--- Starting Hybrid Solver ---")
print("The solver will automatically detect the cycle [x, y, z] and iterate.")
results = pipe.run(**initial_state)

print(f"\nConverged Values -> X: {results['x']:.4f}, Y: {results['y']:.4f}, Z: {results['z']:.4f}")

# Verification
expected = {'x': 20./7., 'y': 12./7., 'z': 38./7.}
print("\n--- Verification Check ---")
for var, target in expected.items():
    actual = results[var]
    print(f"Variable {var.upper()}: Target={target:.4f}, Actual={actual:.4f} (Err={abs(actual - target):.4e})")

# Visualizatio diagram
print("Generating interactive diagram...")
pipe.visualize(inputs=["history_x", "history_y", "history_z"],  # If not provided, will try to infer and use a (?) in the graph"],
                output_path=str(Path("results") / "iterative_solver_complex.pdf"),
                graph_type="bipartite")

# ==============================================================================
# 4. PLOT TRAJECTORIES (Updated for Hybrid Solver Structure)
# ==============================================================================

hist_x = results['history_x']
hist_y = results['history_y']
hist_z = results['history_z']

# Retrieve residuals. 
# HybridSolver/IterativeSolver now stores a LIST of residual lists (one per cyclic block).
# Since we have 1 cycle, we take the last element (or the first, as there's only one).
raw_residuals = results.get('residual_history', [])
residuals = raw_residuals[0] if raw_residuals else []

iterations = range(len(hist_x))
res_iterations = range(1, len(residuals) + 1)

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

# Plot 1: Variable Convergence
ax1.plot(iterations, hist_x, 'o-', label=f"x (Target {expected['x']:.2f})", markersize=4, alpha=0.8)
ax1.plot(iterations, hist_y, 's-', label=f"y (Target {expected['y']:.2f})", markersize=4, alpha=0.8)
ax1.plot(iterations, hist_z, '^-', label=f"z (Target {expected['z']:.2f})", markersize=4, alpha=0.8)
ax1.set_title('Variable Convergence Trajectories')
ax1.set_xlabel('Iteration Step')
ax1.set_ylabel('Value')
ax1.grid(True, linestyle='--', alpha=0.5)
ax1.legend()

# Plot 2: Residual History
if residuals:
    ax2.plot(res_iterations, residuals, 'r-o', linewidth=2, label='Max Residual')
    ax2.set_yscale('log')
    ax2.set_title(f'Convergence Rate ({len(residuals)} iterations)')
    ax2.set_xlabel('Iteration Step')
    ax2.set_ylabel('Residual (Log Scale)')
    ax2.grid(True, linestyle='--', alpha=0.5, which="both")
    ax2.legend()
else:
    ax2.text(0.5, 0.5, "No residuals recorded\n(Converged immediately or no cycle)", ha='center')

plt.tight_layout()
plt.show()