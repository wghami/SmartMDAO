import logging
from dataclasses import dataclass, field
from typing import Callable, List, Literal

from .models import Step
from .solvers import Solver, DAGSolver
from .visualization import visualize_pipeline
from .validation import TypeChecker, StandardTypeChecker, validate_structure, validate_external_inputs

# Initialize module-level logger
logger = logging.getLogger(__name__)

@dataclass
class Pipeline:
    steps: list[Step] = field(default_factory=list)
    solver: Solver = field(default_factory=DAGSolver)
    # Static structural validation (producer/consumer type compatibility) always
    # runs before the first execution of a given pipeline shape - it's free.
    # Runtime per-call validation is opt-in since it adds overhead to every
    # step invocation, which matters inside IterativeSolver's convergence loop.
    runtime_type_checks: bool = False
    type_checker: TypeChecker = field(default_factory=StandardTypeChecker)
    _structure_validated: bool = field(default=False, init=False, repr=False, compare=False)

    def add(self, fn: Callable, outputs: list[str] = None):
        """
        Add a step to the pipeline.
        :param fn: The function to execute.
        :param outputs: Optional list of variable names this function produces.
        """
        step = Step(fn, outputs)
        self.steps.append(step)
        self._structure_validated = False
        logger.debug(f"Added step '{step.name}' to pipeline.")
        return self

    def step(self, fn: Callable = None, *, outputs: List[str] = None):
        """
        Decorator to register a step.
        """
        if fn is not None and callable(fn):
            self.add(fn, outputs=outputs)
            return fn

        def wrapper(func):
            self.add(func, outputs=outputs)
            return func
        
        return wrapper

    def run(self, **inputs):
        """
        Validates types, then delegates execution to the configured Solver.
        """
        logger.info(f"Starting pipeline execution with {len(self.steps)} steps and inputs: {list(inputs.keys())}")
        try:
            if not self._structure_validated:
                validate_structure(self.steps, self.type_checker)
                self._structure_validated = True

            validate_external_inputs(self.steps, inputs, self.type_checker)

            if self.runtime_type_checks:
                result = self.solver.solve(self.steps, inputs, type_checker=self.type_checker)
            else:
                result = self.solver.solve(self.steps, inputs)

            logger.info("Pipeline execution completed successfully.")
            return result
        except Exception as e:
            logger.error(f"Pipeline execution failed: {e}")
            raise

    def visualize(self, 
                  inputs: List[str] = None, 
                  output_path: str = None, 
                  orientation: Literal["TB", "LR"] = "TB",
                  graph_type: Literal["flow", "bipartite"] = "flow",
                  view: bool = True):
        """
        Generates an XDSM diagram of the pipeline.
        """
        input_set = set(inputs or [])
        logger.debug(f"Generating visualization ({graph_type}) for pipeline.")
        
        visualize_pipeline(
            steps=self.steps,
            inputs=input_set,
            output_path=output_path,
            orientation=orientation,
            graph_type=graph_type,
            view=view
        )