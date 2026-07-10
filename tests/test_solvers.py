import pytest
from dataclasses import dataclass
from smartmdao.models import Step
from smartmdao.solvers import DAGSolver, IterativeSolver, HybridSolver, StandardConvergenceChecker

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

def test_iterative_solver_no_steps_never_converges_without_crashing():
    # Degenerate case: no steps means no produced variables, so there's
    # nothing to compare between iterations. This should behave like
    # "never converges" (residual stays inf) rather than raising on an
    # empty max() call.
    solver = IterativeSolver(max_iterations=3)
    result = solver.solve([], {})
    assert result["residual_history"][-1] == [float("inf")] * 3

def test_standard_convergence_checker_numeric():
    checker = StandardConvergenceChecker()
    assert checker.distance(1.0, 1.5) == pytest.approx(0.5)
    assert checker.distance(3, 3) == 0.0

def test_standard_convergence_checker_equatable_non_numeric():
    checker = StandardConvergenceChecker()
    assert checker.distance({"a": 1}, {"a": 1}) == 0.0
    assert checker.distance({"a": 1}, {"a": 2}) == float("inf")
    assert checker.distance(frozenset({"x", "y"}), frozenset({"y", "x"})) == 0.0
    assert checker.distance("done", "still-going") == float("inf")

def test_standard_convergence_checker_uncomparable_falls_back_to_inf():
    class Explodes:
        def __eq__(self, other):
            raise TypeError("not comparable")

    checker = StandardConvergenceChecker()
    assert checker.distance(Explodes(), Explodes()) == float("inf")

def test_iterative_solver_converges_on_non_numeric_fixed_point():
    # A dependency-closure loop over frozensets: no floats anywhere.
    # Previously, `_calculate_residual` only ever considered int/float
    # outputs, so a purely non-numeric cycle like this could never be
    # detected as converged and would always burn through max_iterations.
    depends_on = {"billing": frozenset({"database"})}

    def resolve(requested: frozenset, enabled: frozenset) -> frozenset:
        expanded = set(enabled) | set(requested)
        for feature in list(expanded):
            expanded |= set(depends_on.get(feature, frozenset()))
        return frozenset(expanded)

    steps = [Step(fn=resolve, manual_outputs=["enabled"])]
    solver = IterativeSolver(max_iterations=10)
    result = solver.solve(steps, {"requested": frozenset({"billing"}), "enabled": frozenset()})

    assert result["enabled"] == frozenset({"billing", "database"})
    # Converged before exhausting max_iterations.
    assert len(result["residual_history"][-1]) < 10

def test_hybrid_solver_converges_on_non_numeric_cycle():
    @dataclass(frozen=True)
    class Plan:
        tasks: frozenset

    roster = ["design", "build"]
    blocked_by = {"build": frozenset({"design"})}

    def propose(reviewed_plan: Plan) -> Plan:
        staged = set(reviewed_plan.tasks)
        for task in roster:
            if task not in staged and blocked_by.get(task, frozenset()) <= staged:
                return Plan(tasks=frozenset(staged | {task}))
        return Plan(tasks=frozenset(staged))

    def review(plan: Plan) -> Plan:
        return plan

    steps = [
        Step(fn=propose, manual_outputs=["plan"]),
        Step(fn=review, manual_outputs=["reviewed_plan"]),
    ]
    solver = HybridSolver(max_iterations=10)
    result = solver.solve(steps, {"reviewed_plan": Plan(tasks=frozenset())})

    assert result["plan"].tasks == frozenset({"design", "build"})

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