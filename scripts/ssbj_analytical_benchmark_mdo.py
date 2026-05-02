import logging
import numpy as np
from scipy.optimize import minimize

from smartmdao.core import Pipeline
from smartmdao.solvers import HybridSolver
from smartmdao.logging_config import configure_logging
from smartmdao.optimization import PipelineEvaluator

# --- Setup Logging ---
logger = logging.getLogger(__name__)

# ==============================================================================
# PART 1: Initialize the Pipeline
# ==============================================================================
# SSBJ has a massive 3-way cycle (Aero <-> Struct, Aero -> Prop -> Struct).
# We MUST use the HybridSolver so Tarjan's algorithm can automatically 
# isolate the aircraft feedback loops and converge them.
pipeline = Pipeline(
    solver=HybridSolver(max_iterations=150, tolerance=1e-5)
)

# ==============================================================================
# PART 2: Define the Disciplines (SSBJ Topology)
# ==============================================================================
# In this benchmark, x1 to x11 represent the 11 SSBJ design variables 
# (e.g., thickness-to-chord, altitude, Mach, Aspect Ratio, Wing Area, etc.)

@pipeline.step(outputs=["twist", "struct_weight"])
def structures(x1: float, x2: float, x3: float, x4: float, 
               lift: float, engine_weight: float):
    """
    STRUCTURES DISCIPLINE:
    Requires: 'lift' (from Aero) and 'engine_weight' (from Propulsion)
    Produces: 'twist' (wing deformation) and 'struct_weight'
    """
    # Synthetic contraction mapping for stability
    twist = 0.05 * lift + 0.01 * x1 * x2
    struct_weight = 20.0 * x3 + 5.0 * x4 + 0.1 * engine_weight + 2.0 * twist
    return twist, struct_weight

@pipeline.step(outputs=["lift", "drag"])
def aerodynamics(x5: float, x6: float, x7: float, twist: float):
    """
    AERODYNAMICS DISCIPLINE:
    Requires: 'twist' (from Structures)
    Produces: 'lift' and 'drag'
    """
    lift = 0.2 * twist + 10.0 * x5 + 2.0 * x6
    drag = 0.05 * lift + 1.5 * x7 + 0.01 * (twist ** 2)
    return lift, drag

@pipeline.step(outputs=["engine_weight", "sfc"])
def propulsion(x8: float, x9: float, x10: float, x11: float, drag: float):
    """
    PROPULSION DISCIPLINE:
    Requires: 'drag' (from Aerodynamics)
    Produces: 'engine_weight' and 'sfc' (Specific Fuel Consumption)
    """
    engine_weight = 1.5 * drag + 5.0 * x8 + 2.0 * x9
    sfc = 0.8 + 0.01 * x10 + 0.005 * x11 + 0.001 * drag
    return engine_weight, sfc

@pipeline.step(outputs=["objective", "range_constraint"])
def performance(struct_weight: float, engine_weight: float, 
                lift: float, drag: float, sfc: float) -> float:
    """
    PERFORMANCE (SYSTEM LEVEL):
    Calculates the aircraft range (Objective to maximize, so we minimize -Range).
    """
    total_weight = struct_weight + engine_weight + 50.0 # 50.0 is payload/fuel
    l_over_d = lift / (drag + 1e-6)
    
    # Breguet Range Equation simplified equivalent
    aircraft_range = (l_over_d / sfc) * np.log(total_weight / (total_weight - 20.0))
    
    # Objective: Minimize negative range
    objective = -aircraft_range
    
    # Constraint: Total weight must not exceed a limit (e.g., 250.0)
    # Pipeline convention: g(x) <= 0
    range_constraint = total_weight - 250.0
    
    return objective, range_constraint


# ==============================================================================
# PART 3: Optimization Execution
# ==============================================================================
def run_ssbj_mdo():
    print("\n" + "="*70)
    print("--- Running SSBJ Topology Benchmark (11 Variables, 3 Disciplines) ---")
    print("="*70)
    
    # Suppress pipeline INFO logs during the optimization loop
    logging.getLogger("smartmdao").setLevel(logging.WARNING)
    
    # We have 11 design variables
    design_vars = [f"x{i}" for i in range(1, 12)]
    
    # We must provide initial guesses for the cyclic solver to kick off the loops
    constants = {
        "twist": 0.5,          # <- ADD THIS: aerodynamics is evaluated first alphabetically and needs this!
        "lift": 50.0, 
        "engine_weight": 20.0
    }
    
    evaluator = PipelineEvaluator(
        pipeline=pipeline, 
        design_vars=design_vars,
        constants=constants
    )
    
    # Initial guess for the 11 variables
    initial_guess = np.ones(11) * 2.0
    
    # Bounds for the 11 variables (1.0 <= xi <= 5.0)
    bounds = [(1.0, 5.0) for _ in range(11)]
    
    # Constraints for scipy.optimize
    cons = [
        {'type': 'ineq', 'fun': evaluator.get_constraint("range_constraint", multiplier=-1.0)}
    ]
    
    print("Starting optimization for 11 design variables...")
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
    
    # Extract Full State
    optimal_state = evaluator.evaluate(result.x)
    
    print("\n--- Final SSBJ Results ---")
    print(f"Total Pipeline Runs: {evaluator.eval_count}")
    print("-" * 40)
    
    print("Optimized 11 Design Variables:")
    for var in design_vars:
        print(f"  {var:<4} = {optimal_state[var]:>8.4f}")
        
    print("-" * 40)
    print("Converged Aircraft State:")
    print(f"  Lift          = {optimal_state['lift']:>8.4f}")
    print(f"  Drag          = {optimal_state['drag']:>8.4f}")
    print(f"  Twist         = {optimal_state['twist']:>8.4f}")
    print(f"  Struct Weight = {optimal_state['struct_weight']:>8.4f}")
    print(f"  Engine Weight = {optimal_state['engine_weight']:>8.4f}")
    print(f"  L/D Ratio     = {optimal_state['lift']/optimal_state['drag']:>8.4f}")
    
    print("-" * 40)
    # The objective was negative range, so we flip it back for display
    print(f"Maximized Range = {-optimal_state['objective']:>8.4f}")
    print(f"Weight Constraint limit (<=0) = {optimal_state['range_constraint']:>8.4f}")
    
    # Optional: Prove the topology extraction works!
    print("\nGenerating graphviz output (ssbj_topology.pdf) to prove cycle detection...")
    pipeline.visualize(
        inputs=design_vars, 
        output_path="results/ssbj_topology", 
        orientation="LR", 
        graph_type="bipartite",
        view=False
    )

# ==============================================================================
if __name__ == "__main__":
    configure_logging(level=logging.INFO)
    run_ssbj_mdo()