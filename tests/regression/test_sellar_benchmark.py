"""Known-answer regression tests for the Sellar problem.

Mirrors scripts/sellar_benchmark_mda.py, sellar_benchmark_mdo_scipy.py and
sellar_benchmark_mdo_openturns.py, but asserts on the results instead of
just printing them, so a silent change in solver/optimizer behavior fails CI.

The global optimum (Sellar, 1996) is:
    z1=1.9776, z2=0.0, x1=0.0, y1=3.16, y2=3.7553, objective=10.0090
"""
import math

import numpy as np
import pytest
from scipy.optimize import minimize

from smartmdao.core import Pipeline
from smartmdao.solvers import HybridSolver
from smartmdao.optimization import PipelineEvaluator

from ._assertions import assert_state_close

KNOWN_OPTIMUM = {
    "z1": 1.9776,
    "z2": 0.0000,
    "x1": 0.0000,
    "y1": 3.1600,
    "y2": 3.7553,
    "objective": 10.0090,
    "constraint_1": 0.0000,
    "constraint_2": -20.2447,
}


def build_sellar_pipeline() -> Pipeline:
    pipeline = Pipeline(solver=HybridSolver(max_iterations=100, tolerance=1e-6))

    @pipeline.step(outputs=["y1"])
    def discipline_1(z1: float, z2: float, x1: float, y2: float) -> float:
        return (z1 ** 2) + z2 + x1 - (0.2 * y2)

    @pipeline.step(outputs=["y2"])
    def discipline_2(z1: float, z2: float, y1: float) -> float:
        return math.sqrt(abs(y1)) + z1 + z2

    @pipeline.step(outputs=["objective"])
    def compute_objective(x1: float, z2: float, y1: float, y2: float) -> float:
        return (x1 ** 2) + z2 + (y1 ** 2) + math.exp(-y2)

    @pipeline.step(outputs=["constraint_1"])
    def compute_constraint_1(y1: float) -> float:
        return 3.16 - y1

    @pipeline.step(outputs=["constraint_2"])
    def compute_constraint_2(y2: float) -> float:
        return y2 - 24.0

    return pipeline


def test_sellar_mda_converges_at_known_optimum():
    """Running the MDA (no optimizer) at the published optimal design should
    reproduce the published state, proving the HybridSolver's cyclic
    convergence (y1 <-> y2) still behaves as expected."""
    pipeline = build_sellar_pipeline()

    results = pipeline.run(z1=1.9776, z2=0.0, x1=0.0, y2=1.0)

    assert_state_close(
        results,
        {"y1": KNOWN_OPTIMUM["y1"], "y2": KNOWN_OPTIMUM["y2"], "objective": KNOWN_OPTIMUM["objective"]},
        rel=1e-3,
        abs=1e-3,
    )
    assert results["constraint_1"] <= 1e-3
    assert results["constraint_2"] <= 1e-3


def test_sellar_mdo_scipy_converges_to_known_optimum():
    pipeline = build_sellar_pipeline()
    evaluator = PipelineEvaluator(
        pipeline=pipeline,
        design_vars=["z1", "z2", "x1"],
        constants={"y2": 1.0},
    )

    cons = [
        {"type": "ineq", "fun": evaluator.get_constraint("constraint_1", multiplier=-1.0)},
        {"type": "ineq", "fun": evaluator.get_constraint("constraint_2", multiplier=-1.0)},
    ]
    result = minimize(
        evaluator.get_objective("objective"),
        np.array([1.0, 1.0, 1.0]),
        method="SLSQP",
        bounds=[(-10.0, 10.0), (0.0, 10.0), (0.0, 10.0)],
        constraints=cons,
        options={"ftol": 1e-6},
    )

    assert result.success
    optimal_state = evaluator.evaluate(result.x)
    assert_state_close(optimal_state, KNOWN_OPTIMUM)


def test_sellar_mdo_openturns_converges_to_known_optimum():
    ot = pytest.importorskip("openturns")

    pipeline = build_sellar_pipeline()
    evaluator = PipelineEvaluator(
        pipeline=pipeline,
        design_vars=["z1", "z2", "x1"],
        constants={"y2": 1.0},
    )

    def ot_objective(x):
        return [evaluator.get_objective("objective")(x)]

    def ot_constraints(x):
        c1 = evaluator.get_constraint("constraint_1", multiplier=-1.0)(x)
        c2 = evaluator.get_constraint("constraint_2", multiplier=-1.0)(x)
        return [c1, c2]

    obj_fun = ot.PythonFunction(3, 1, ot_objective)
    cons_fun = ot.PythonFunction(3, 2, ot_constraints)

    problem = ot.OptimizationProblem(obj_fun)
    problem.setBounds(ot.Interval([-10.0, 0.0, 0.0], [10.0, 10.0, 10.0]))
    problem.setInequalityConstraint(cons_fun)

    algo = ot.Cobyla(problem)
    algo.setStartingPoint([1.0, 1.0, 1.0])
    algo.setMaximumIterationNumber(1000)
    algo.run()

    x_opt = np.array(algo.getResult().getOptimalPoint())
    optimal_state = evaluator.evaluate(x_opt)
    assert_state_close(optimal_state, KNOWN_OPTIMUM)
