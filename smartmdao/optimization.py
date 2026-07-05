import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Protocol, Tuple, Type, Union

from .core import Pipeline

logger = logging.getLogger(__name__)

class PipelineEvaluator:
    """
    A generic, stateful bridge to interface the Pipeline with external optimizers 
    (SciPy, OpenTURNS, PyOptSparse, etc.).
    
    It caches the last evaluation to prevent redundant pipeline runs when optimizers
    request objectives and constraints independently for the same state.
    """
    def __init__(self, 
                 pipeline: Pipeline, 
                 design_vars: List[str], 
                 constants: Dict[str, Any] = None):
        """
        :param pipeline: The instantiated smartmdao.
        :param design_vars: Ordered list of variable names corresponding to the optimizer's input array `x`.
        :param constants: Optional dictionary of variables that remain fixed during optimization.
        """
        self.pipeline = pipeline
        self.design_vars = design_vars
        self.constants = constants or {}
        
        self.last_x = None
        self.last_results = None
        self.eval_count = 0

    def evaluate(self, x) -> Dict[str, Any]:
        """Runs the pipeline if the design variables have changed."""
        # Convert to tuple for hashable comparison
        x_tuple = tuple(x)
        
        if self.last_x != x_tuple:
            self.eval_count += 1
            
            # 1. Map the numeric array 'x' back to named variables
            inputs = dict(zip(self.design_vars, x))
            
            # 2. Inject constants
            inputs.update(self.constants)
            
            # 3. Execute
            self.last_results = self.pipeline.run(**inputs)
            self.last_x = x_tuple
            
        return self.last_results

    def get_objective(self, output_name: str) -> Callable:
        """
        Factory method that returns a callable objective function for the optimizer.
        """
        def _objective(x):
            return self.evaluate(x)[output_name]
        return _objective

    def get_constraint(self, output_name: str, multiplier: float = 1.0) -> Callable:
        """
        Factory method that returns a callable constraint function.
        :param multiplier: Useful for flipping constraint signs.
                           (e.g., SciPy expects f(x) >= 0. If pipeline outputs f(x) <= 0, use multiplier=-1.0)
        """
        def _constraint(x):
            return multiplier * self.evaluate(x)[output_name]
        return _constraint


# ==============================================================================
# Backend-agnostic optimization: define the problem once, swap the solver.
# ==============================================================================

@dataclass
class ConstraintSpec:
    """
    One inequality or equality constraint on a named pipeline output.
    Both built-in backends follow the same convention: 'ineq' means
    h(x) >= 0 and 'eq' means h(x) == 0 - use `multiplier` to flip the sign
    of a discipline that was naturally written the other way around.
    """
    name: str
    kind: Literal["ineq", "eq"] = "ineq"
    multiplier: float = 1.0


@dataclass
class OptimizationProblem:
    """
    A backend-agnostic description of an optimization problem, built on top
    of an existing PipelineEvaluator. Any OptimizerBackend can consume this
    without knowing anything about SmartMDAO's Pipeline internals.
    """
    evaluator: PipelineEvaluator
    initial_guess: List[float]
    bounds: Optional[List[Tuple[float, float]]] = None
    objective: str = "objective"
    constraints: List[ConstraintSpec] = field(default_factory=list)


@dataclass
class OptimizationResult:
    """
    Normalized optimization outcome, common across every backend.
    `raw` keeps the underlying backend-specific result object (e.g. a
    scipy.optimize.OptimizeResult) for anyone who needs backend-specific detail.
    """
    x: List[float]
    objective_value: float
    success: bool
    message: str
    state: Dict[str, Any]
    raw: Any = None


class OptimizerBackend(Protocol):
    """Interface every optimizer backend implements."""
    def solve(self, problem: OptimizationProblem, **options: Any) -> OptimizationResult:
        ...


_BACKENDS: Dict[str, Type[OptimizerBackend]] = {}


def register_backend(name: str):
    """
    Registers an OptimizerBackend under a short string key, so it becomes
    selectable via optimize(problem, backend=name) - the same "name it once,
    use it everywhere" pattern as @pipeline.step. Custom backends (PyOptSparse,
    an in-house solver, ...) can register themselves the exact same way.
    """
    def decorator(cls: Type[OptimizerBackend]) -> Type[OptimizerBackend]:
        _BACKENDS[name] = cls
        return cls
    return decorator


def optimize(
    problem: OptimizationProblem,
    backend: Union[str, OptimizerBackend] = "scipy",
    **options: Any,
) -> OptimizationResult:
    """
    Runs `problem` through the given backend and returns a normalized result.
    `backend` can be a registered name ('scipy', 'openturns', ...) or any
    object implementing OptimizerBackend directly (bring your own optimizer).
    """
    if isinstance(backend, str):
        try:
            backend_cls = _BACKENDS[backend]
        except KeyError:
            raise ValueError(
                f"Unknown optimizer backend '{backend}'. Registered backends: {sorted(_BACKENDS)}."
            ) from None
        backend = backend_cls()

    logger.info(f"Running optimization with backend '{type(backend).__name__}'.")
    return backend.solve(problem, **options)


@register_backend("scipy")
class ScipyBackend:
    """
    Bridges to scipy.optimize.minimize. Defaults to SLSQP, since it natively
    supports bounds plus inequality/equality constraints - the most common case.
    Extra keyword arguments are forwarded as-is to `minimize()` (e.g. `tol=1e-6`
    or `options={"disp": True}`).
    """
    def solve(self, problem: OptimizationProblem, method: str = "SLSQP", **options: Any) -> OptimizationResult:
        from scipy.optimize import minimize

        constraints = [
            {
                "type": c.kind,
                "fun": problem.evaluator.get_constraint(c.name, multiplier=c.multiplier),
            }
            for c in problem.constraints
        ]

        result = minimize(
            problem.evaluator.get_objective(problem.objective),
            problem.initial_guess,
            method=method,
            bounds=problem.bounds,
            constraints=constraints,
            **options,
        )

        x_opt = [float(v) for v in result.x]
        return OptimizationResult(
            x=x_opt,
            objective_value=float(result.fun),
            success=bool(result.success),
            message=str(result.message),
            state=problem.evaluator.evaluate(x_opt),
            raw=result,
        )


@register_backend("openturns")
class OpenTURNSBackend:
    """
    Bridges to OpenTURNS' OptimizationAlgorithm classes. Defaults to Cobyla,
    a derivative-free algorithm that handles inequality/equality constraints
    without requiring gradients. `method` is looked up as a class name on the
    `openturns` module (e.g. "Cobyla", "SLSQP", "AbdoRackwitz", ...), so any
    algorithm OpenTURNS ships is selectable without adding a branch here.
    """
    def solve(
        self,
        problem: OptimizationProblem,
        method: str = "Cobyla",
        max_iterations: int = 1000,
        **options: Any,
    ) -> OptimizationResult:
        import openturns as ot

        dim = len(problem.initial_guess)
        objective_fn = ot.PythonFunction(
            dim, 1, lambda x: [problem.evaluator.get_objective(problem.objective)(x)]
        )
        ot_problem = ot.OptimizationProblem(objective_fn)

        if problem.bounds:
            lower = [b[0] for b in problem.bounds]
            upper = [b[1] for b in problem.bounds]
            ot_problem.setBounds(ot.Interval(lower, upper))

        ineq = [c for c in problem.constraints if c.kind == "ineq"]
        eq = [c for c in problem.constraints if c.kind == "eq"]

        if ineq:
            ot_problem.setInequalityConstraint(
                ot.PythonFunction(
                    dim, len(ineq),
                    lambda x, _cs=ineq: [problem.evaluator.get_constraint(c.name, c.multiplier)(x) for c in _cs],
                )
            )
        if eq:
            ot_problem.setEqualityConstraint(
                ot.PythonFunction(
                    dim, len(eq),
                    lambda x, _cs=eq: [problem.evaluator.get_constraint(c.name, c.multiplier)(x) for c in _cs],
                )
            )

        algo = getattr(ot, method)(ot_problem)
        algo.setStartingPoint(problem.initial_guess)
        algo.setMaximumIterationNumber(max_iterations)
        for key, value in options.items():
            getattr(algo, f"set{key}")(value)
        algo.run()

        ot_result = algo.getResult()
        x_opt = list(ot_result.getOptimalPoint())

        return OptimizationResult(
            x=x_opt,
            objective_value=float(ot_result.getOptimalValue()[0]),
            success=True,  # OpenTURNS raises on failure rather than reporting a boolean.
            message=f"Completed after {ot_result.getIterationNumber()} iteration(s).",
            state=problem.evaluator.evaluate(x_opt),
            raw=ot_result,
        )