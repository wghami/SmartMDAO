from .core import Pipeline
from .models import Step
from .solvers import Solver, DAGSolver, IterativeSolver, HybridSolver
from .cache import cached, MemoryBackend, HistoryBackend, HDF5Backend, PickleDiskBackend
from .logging_config import configure_logging
from .optimization import PipelineEvaluator

# Expose the configuration helper so users can easily do: 
# import pipeline; pipeline.configure_logging()

__all__ = [
    "Pipeline", 
    "Step", 
    "Solver", 
    "DAGSolver", 
    "IterativeSolver", 
    "HybridSolver",
    "cached", 
    "MemoryBackend", 
    "HistoryBackend", 
    "HDF5Backend", 
    "PickleDiskBackend",
    "configure_logging",
    "PipelineEvaluator"
]