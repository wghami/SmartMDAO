import pytest
from smart_pipeline.models import Step
from smart_pipeline.solvers import DAGSolver, IterativeSolver, HybridSolver

def test_dag_solver_success():
    steps = [
        Step(fn=lambda a: a * 2, manual_outputs=["b"]),
        Step(fn=lambda b: b + 1, manual_outputs=["c"])
    ]
    solver = DAGSolver()
    result = solver.solve(steps, {"a": 2})
    assert result["c"] == 5

def test_dag_solver_cycle():
    # b requires a, a requires b
    steps = [
        Step(fn=lambda b: b * 2, manual_outputs=["a"]),
        Step(fn=lambda a: a + 1, manual_outputs=["b"])
    ]
    solver = DAGSolver()
    with pytest.raises(ValueError, match="Cycle detected"):
        solver.solve(steps, {})

def test_iterative_solver_convergence():
    # A simple decay loop
    def decay(val):
        return val * 0.5
        
    steps = [Step(fn=decay, manual_outputs=["val"])]
    solver = IterativeSolver(tolerance=0.1, target_var="val")
    result = solver.solve(steps, {"val": 1.0})
    
    # Should converge quickly
    assert result["val"] < 0.1
    assert "residual_history" in result

def test_iterative_solver_max_iterations():
    # A loop that never converges
    steps = [Step(fn=lambda x: x + 1, manual_outputs=["x"])]
    solver = IterativeSolver(max_iterations=5)
    result = solver.solve(steps, {"x": 0})
    assert result["x"] == 5

def test_hybrid_solver():
    # x -> (a <-> b) -> y
    steps = [
        Step(fn=lambda x: x, manual_outputs=["a"]),     # Linear
        Step(fn=lambda a: a * 0.5, manual_outputs=["b"]), # Cycle part 1
        Step(fn=lambda b: b + 0.1, manual_outputs=["a"]), # Cycle part 2
        Step(fn=lambda b: b * 2, manual_outputs=["y"]),   # Linear
    ]
    solver = HybridSolver(tolerance=0.2)
    result = solver.solve(steps, {"x": 1.0})
    assert "y" in result

def test_iterative_solver_max_iterations():
    # A loop that never converges
    steps = [Step(fn=lambda x: x + 1, manual_outputs=["x"])]
    solver = IterativeSolver(max_iterations=5)
    result = solver.solve(steps, {"x": 0})
    assert result["x"] == 5

def test_hybrid_solver():
    # x -> (a <-> b) -> y
    # We use named functions instead of lambdas here because the HybridSolver
    # sorts cycle blocks alphabetically by step name to ensure deterministic execution.
    def step1_linear(x): return x
    def step2_cycle_part1(a): return a * 0.5
    def step3_cycle_part2(b): return b + 0.1
    def step4_linear(b): return b * 2

    steps = [
        Step(fn=step1_linear, manual_outputs=["a"]),     # Linear
        Step(fn=step2_cycle_part1, manual_outputs=["b"]), # Cycle part 1
        Step(fn=step3_cycle_part2, manual_outputs=["a"]), # Cycle part 2
        Step(fn=step4_linear, manual_outputs=["y"]),   # Linear
    ]
    solver = HybridSolver(tolerance=0.2)
    result = solver.solve(steps, {"x": 1.0})
    assert "y" in result