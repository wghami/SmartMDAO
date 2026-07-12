"""Known-answer regression test for the HS71 benchmark (Hock & Schittkowski, 1981).

Mirrors scripts/hs71_benchmark_mdo.py. The global optimum is well known:
    x* = (1.0, 4.7430, 3.8211, 1.3794), f* = 17.0140
"""
import numpy as np
from scipy.optimize import minimize

from smartmdao.core import Pipeline
from smartmdao.solvers import DAGSolver
from smartmdao.optimization import PipelineEvaluator

from ._assertions import assert_state_close

KNOWN_OPTIMUM = {
    "x1": 1.0000,
    "x2": 4.7430,
    "x3": 3.8211,
    "x4": 1.3794,
    "sum_123": 9.5641,
    "objective": 17.0140,
    "ineq_constraint": 0.0000,
    "eq_constraint": 0.0000,
}


def build_hs71_pipeline() -> Pipeline:
    pipeline = Pipeline(solver=DAGSolver())

    @pipeline.step(outputs=["sum_123"])
    def compute_sum(x1: float, x2: float, x3: float) -> float:
        return x1 + x2 + x3

    @pipeline.step(outputs=["objective"])
    def compute_objective(x1: float, x3: float, x4: float, sum_123: float) -> float:
        return x1 * x4 * sum_123 + x3

    @pipeline.step(outputs=["ineq_constraint"])
    def compute_inequality(x1: float, x2: float, x3: float, x4: float) -> float:
        return 25.0 - (x1 * x2 * x3 * x4)

    @pipeline.step(outputs=["eq_constraint"])
    def compute_equality(x1: float, x2: float, x3: float, x4: float) -> float:
        return (x1 ** 2 + x2 ** 2 + x3 ** 2 + x4 ** 2) - 40.0

    return pipeline


def test_hs71_mdo_scipy_converges_to_known_optimum():
    pipeline = build_hs71_pipeline()
    evaluator = PipelineEvaluator(pipeline=pipeline, design_vars=["x1", "x2", "x3", "x4"])

    cons = [
        {"type": "ineq", "fun": evaluator.get_constraint("ineq_constraint", multiplier=-1.0)},
        {"type": "eq", "fun": evaluator.get_constraint("eq_constraint")},
    ]
    result = minimize(
        evaluator.get_objective("objective"),
        np.array([1.0, 5.0, 5.0, 1.0]),
        method="SLSQP",
        bounds=[(1.0, 5.0)] * 4,
        constraints=cons,
        options={"ftol": 1e-6},
    )

    assert result.success
    optimal_state = evaluator.evaluate(result.x)
    assert_state_close(optimal_state, KNOWN_OPTIMUM)
