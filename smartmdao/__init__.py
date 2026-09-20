from .core import Pipeline
from .models import Step
from .solvers import (
    Solver,
    DAGSolver,
    IterativeSolver,
    HybridSolver,
    ConvergenceChecker,
    StandardConvergenceChecker,
    OscillationAwareConvergenceChecker,
    OscillationDetectedError,
    AbandonmentAware,
    ConvergenceReport,
    CONVERGED,
    MAX_ITERATIONS,
    ABANDONED,
)
from .cache import cached, MemoryBackend, HistoryBackend, HDF5Backend, PickleDiskBackend
from .logging_config import configure_logging
from .optimization import (
    PipelineEvaluator,
    ConstraintSpec,
    OptimizationProblem,
    OptimizationResult,
    OptimizerBackend,
    register_backend,
    optimize,
)
from .discretisation import Bands, Discretisation, DiscretisationError
from .rules import (
    INFEASIBLE,
    AmbiguousProgramError,
    Conflict,
    RuleBudgetExceeded,
    RuleCost,
    RuleDiscipline,
    RuleProgramError,
)
from .validation import TypeChecker, StandardTypeChecker, TypeMismatchError
from .analysis import (
    analyze,
    validate,
    explain,
    Finding,
    CycleAnalysis,
    InitialGuess,
    PipelineAnalysis,
)

# Expose the configuration helper so users can easily do:
# import pipeline; pipeline.configure_logging()

__all__ = [
    "Pipeline",
    "Step",
    "Solver",
    "DAGSolver",
    "IterativeSolver",
    "HybridSolver",
    "ConvergenceChecker",
    "StandardConvergenceChecker",
    "OscillationAwareConvergenceChecker",
    "OscillationDetectedError",
    "AbandonmentAware",
    "ConvergenceReport",
    "CONVERGED",
    "MAX_ITERATIONS",
    "ABANDONED",
    "cached",
    "MemoryBackend",
    "HistoryBackend",
    "HDF5Backend",
    "PickleDiskBackend",
    "configure_logging",
    "PipelineEvaluator",
    "ConstraintSpec",
    "OptimizationProblem",
    "OptimizationResult",
    "OptimizerBackend",
    "register_backend",
    "optimize",
    "Bands",
    "Discretisation",
    "DiscretisationError",
    "RuleDiscipline",
    "RuleProgramError",
    "AmbiguousProgramError",
    "RuleBudgetExceeded",
    "RuleCost",
    "Conflict",
    "INFEASIBLE",
    "TypeChecker",
    "StandardTypeChecker",
    "TypeMismatchError",
    "analyze",
    "validate",
    "explain",
    "Finding",
    "CycleAnalysis",
    "InitialGuess",
    "PipelineAnalysis",
]