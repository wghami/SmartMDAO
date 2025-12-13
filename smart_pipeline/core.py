import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Literal, Set

from .models import Step
from .solvers import Solver, DAGSolver
from .visualization import visualize_pipeline

# Initialize module-level logger
logger = logging.getLogger(__name__)

@dataclass
class Pipeline:
    steps: list[Step] = field(default_factory=list)
    solver: Solver = field(default_factory=DAGSolver)

    def add(self, fn: Callable, outputs: list[str] = None):
        """
        Add a step to the pipeline.
        :param fn: The function to execute.
        :param outputs: Optional list of variable names this function produces. 
        """
        step = Step(fn, outputs)
        self.steps.append(step)
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
        Delegates the execution to the configured Solver.
        """
        logger.info(f"Starting pipeline execution with {len(self.steps)} steps and inputs: {list(inputs.keys())}")
        try:
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
        Generates a Graphviz diagram of the pipeline.
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