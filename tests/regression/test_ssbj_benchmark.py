"""Golden-master regression test for the SSBJ topology benchmark.

Mirrors scripts/ssbj_analytical_benchmark_mdo.py. Unlike Sellar/HS71/Golinski,
this problem is a synthetic stand-in (invented for this repo to exercise a
3-way discipline cycle) with no published optimum to check against. So instead
of a known-answer test, this pins the currently-converged result as a baseline:
any drift means either the disciplines, the HybridSolver's cycle handling, or
the SLSQP call changed behavior. If you intentionally change any of those,
re-run scripts/ssbj_analytical_benchmark_mdo.py and update BASELINE below.
"""
import numpy as np
from scipy.optimize import minimize

from smartmdao.core import Pipeline
from smartmdao.solvers import HybridSolver
from smartmdao.optimization import PipelineEvaluator

from ._assertions import assert_state_close

DESIGN_VARS = [f"x{i}" for i in range(1, 12)]

BASELINE = {
    "x1": 1.0, "x2": 1.0, "x3": 1.0, "x4": 1.0, "x5": 5.0,
    "x6": 5.0, "x7": 1.0, "x8": 1.0, "x9": 1.0, "x10": 1.0, "x11": 1.0,
    "objective": -3.71904,
    "range_constraint": -153.5915,
    "lift": 60.60808,
    "drag": 4.62284,
    "twist": 3.04040,
    "struct_weight": 32.47423,
    "engine_weight": 13.93427,
}


def build_ssbj_pipeline() -> Pipeline:
    pipeline = Pipeline(solver=HybridSolver(max_iterations=150, tolerance=1e-5))

    @pipeline.step(outputs=["twist", "struct_weight"])
    def structures(x1: float, x2: float, x3: float, x4: float, lift: float, engine_weight: float):
        twist = 0.05 * lift + 0.01 * x1 * x2
        struct_weight = 20.0 * x3 + 5.0 * x4 + 0.1 * engine_weight + 2.0 * twist
        return twist, struct_weight

    @pipeline.step(outputs=["lift", "drag"])
    def aerodynamics(x5: float, x6: float, x7: float, twist: float):
        lift = 0.2 * twist + 10.0 * x5 + 2.0 * x6
        drag = 0.05 * lift + 1.5 * x7 + 0.01 * (twist ** 2)
        return lift, drag

    @pipeline.step(outputs=["engine_weight", "sfc"])
    def propulsion(x8: float, x9: float, x10: float, x11: float, drag: float):
        engine_weight = 1.5 * drag + 5.0 * x8 + 2.0 * x9
        sfc = 0.8 + 0.01 * x10 + 0.005 * x11 + 0.001 * drag
        return engine_weight, sfc

    @pipeline.step(outputs=["objective", "range_constraint"])
    def performance(struct_weight: float, engine_weight: float, lift: float, drag: float, sfc: float):
        total_weight = struct_weight + engine_weight + 50.0
        l_over_d = lift / (drag + 1e-6)
        aircraft_range = (l_over_d / sfc) * np.log(total_weight / (total_weight - 20.0))
        objective = -aircraft_range
        range_constraint = total_weight - 250.0
        return objective, range_constraint

    return pipeline


def test_ssbj_mdo_matches_pinned_baseline():
    pipeline = build_ssbj_pipeline()
    constants = {"twist": 0.5, "lift": 50.0, "engine_weight": 20.0}
    evaluator = PipelineEvaluator(pipeline=pipeline, design_vars=DESIGN_VARS, constants=constants)

    initial_guess = np.ones(11) * 2.0
    cons = [{"type": "ineq", "fun": evaluator.get_constraint("range_constraint", multiplier=-1.0)}]

    result = minimize(
        evaluator.get_objective("objective"),
        initial_guess,
        method="SLSQP",
        bounds=[(1.0, 5.0)] * 11,
        constraints=cons,
        options={"ftol": 1e-6},
    )

    assert result.success
    optimal_state = evaluator.evaluate(result.x)

    # Optimizer must have actually improved on the starting point, not just
    # returned it unchanged (catches a solver/evaluator wiring regression).
    initial_state = evaluator.evaluate(initial_guess)
    assert optimal_state["objective"] < initial_state["objective"]

    assert optimal_state["range_constraint"] <= 1e-3
    assert_state_close(optimal_state, BASELINE, rel=1e-3, abs=1e-3)
