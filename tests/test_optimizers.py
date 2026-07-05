import pytest

from smartmdao import Pipeline, PipelineEvaluator
from smartmdao.optimization import (
    ConstraintSpec,
    OptimizationProblem,
    OptimizationResult,
    optimize,
    register_backend,
    _BACKENDS,
)


def _quadratic_pipeline():
    """objective(x) = (x - 3)^2, unconstrained minimum at x=3."""
    pipeline = Pipeline()

    @pipeline.step(outputs=["objective"])
    def objective(x: float) -> float:
        return (x - 3.0) ** 2

    return pipeline


def _constrained_pipeline():
    """objective(x) = x^2 subject to x >= 2, active-constraint minimum at x=2."""
    pipeline = Pipeline()

    @pipeline.step(outputs=["objective"])
    def objective(x: float) -> float:
        return x ** 2

    @pipeline.step(outputs=["constraint"])
    def constraint(x: float) -> float:
        return 2.0 - x  # naturally expressed as x <= 2; flip sign to get our 'ineq' >= 0 convention

    return pipeline


# --- Registry ---

def test_registered_backends_include_builtins():
    assert "scipy" in _BACKENDS
    assert "openturns" in _BACKENDS

def test_optimize_unknown_backend_raises():
    evaluator = PipelineEvaluator(_quadratic_pipeline(), design_vars=["x"])
    problem = OptimizationProblem(evaluator=evaluator, initial_guess=[0.0])
    with pytest.raises(ValueError, match="Unknown optimizer backend 'nope'"):
        optimize(problem, backend="nope")

def test_optimize_accepts_custom_backend_instance():
    class StubBackend:
        def solve(self, problem, **options):
            return OptimizationResult(
                x=[0.0] * len(problem.initial_guess),
                objective_value=0.0,
                success=True,
                message="stub",
                state=problem.evaluator.evaluate([0.0] * len(problem.initial_guess)),
            )

    evaluator = PipelineEvaluator(_quadratic_pipeline(), design_vars=["x"])
    problem = OptimizationProblem(evaluator=evaluator, initial_guess=[5.0])
    result = optimize(problem, backend=StubBackend())
    assert result.x == [0.0]
    assert result.message == "stub"

def test_register_backend_decorator_adds_entry():
    @register_backend("dummy_for_test")
    class Dummy:
        def solve(self, problem, **options):
            return OptimizationResult(
                x=problem.initial_guess, objective_value=0.0, success=True, message="", state={}
            )

    try:
        assert _BACKENDS["dummy_for_test"] is Dummy
    finally:
        del _BACKENDS["dummy_for_test"]  # keep the registry clean for other tests


# --- ScipyBackend ---

def test_scipy_backend_unconstrained():
    evaluator = PipelineEvaluator(_quadratic_pipeline(), design_vars=["x"])
    problem = OptimizationProblem(
        evaluator=evaluator, initial_guess=[0.0], bounds=[(-10.0, 10.0)], objective="objective"
    )
    result = optimize(problem, backend="scipy")
    assert result.success
    assert result.x[0] == pytest.approx(3.0, abs=1e-3)
    assert result.state["objective"] == pytest.approx(0.0, abs=1e-3)
    assert result.raw is not None

def test_scipy_backend_constrained():
    evaluator = PipelineEvaluator(_constrained_pipeline(), design_vars=["x"])
    problem = OptimizationProblem(
        evaluator=evaluator,
        initial_guess=[5.0],
        bounds=[(0.0, 10.0)],
        constraints=[ConstraintSpec(name="constraint", kind="ineq", multiplier=-1.0)],
    )
    result = optimize(problem, backend="scipy")
    assert result.success
    assert result.x[0] == pytest.approx(2.0, abs=1e-3)

def test_scipy_backend_forwards_extra_options():
    evaluator = PipelineEvaluator(_quadratic_pipeline(), design_vars=["x"])
    problem = OptimizationProblem(evaluator=evaluator, initial_guess=[0.0], bounds=[(-10.0, 10.0)])
    result = optimize(problem, backend="scipy", tol=1e-8)
    assert result.x[0] == pytest.approx(3.0, abs=1e-3)


# --- OpenTURNSBackend ---

def test_openturns_backend_unconstrained():
    evaluator = PipelineEvaluator(_quadratic_pipeline(), design_vars=["x"])
    problem = OptimizationProblem(
        evaluator=evaluator, initial_guess=[0.0], bounds=[(-10.0, 10.0)], objective="objective"
    )
    result = optimize(problem, backend="openturns", max_iterations=200)
    assert result.success
    assert result.x[0] == pytest.approx(3.0, abs=1e-2)
    assert result.raw is not None

def test_openturns_backend_constrained():
    evaluator = PipelineEvaluator(_constrained_pipeline(), design_vars=["x"])
    problem = OptimizationProblem(
        evaluator=evaluator,
        initial_guess=[5.0],
        bounds=[(0.0, 10.0)],
        constraints=[ConstraintSpec(name="constraint", kind="ineq", multiplier=-1.0)],
    )
    result = optimize(problem, backend="openturns", max_iterations=500)
    assert result.x[0] == pytest.approx(2.0, abs=1e-2)

def test_openturns_backend_equality_constraint():
    pipeline = Pipeline()

    @pipeline.step(outputs=["objective"])
    def objective(x: float) -> float:
        return x ** 2

    @pipeline.step(outputs=["equality"])
    def equality(x: float) -> float:
        return x - 4.0

    evaluator = PipelineEvaluator(pipeline, design_vars=["x"])
    problem = OptimizationProblem(
        evaluator=evaluator,
        initial_guess=[0.0],
        bounds=[(-10.0, 10.0)],
        constraints=[ConstraintSpec(name="equality", kind="eq")],
    )
    result = optimize(problem, backend="openturns", max_iterations=500)
    assert result.x[0] == pytest.approx(4.0, abs=1e-2)

def test_openturns_backend_forwards_extra_options_via_setters():
    evaluator = PipelineEvaluator(_quadratic_pipeline(), design_vars=["x"])
    problem = OptimizationProblem(evaluator=evaluator, initial_guess=[0.0], bounds=[(-10.0, 10.0)])
    # MaximumAbsoluteError maps to algo.setMaximumAbsoluteError(...) via the generic pass-through.
    result = optimize(problem, backend="openturns", MaximumAbsoluteError=1e-8)
    assert result.x[0] == pytest.approx(3.0, abs=1e-2)

def test_openturns_backend_unknown_option_raises_attribute_error():
    evaluator = PipelineEvaluator(_quadratic_pipeline(), design_vars=["x"])
    problem = OptimizationProblem(evaluator=evaluator, initial_guess=[0.0], bounds=[(-10.0, 10.0)])
    with pytest.raises(AttributeError):
        optimize(problem, backend="openturns", NotARealOption=1)
