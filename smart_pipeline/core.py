from dataclasses import dataclass, field
from typing import Callable, List, Optional, Literal, Set

from .models import Step
from .solvers import Solver, DAGSolver
from .visualization import visualize_pipeline

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
        self.steps.append(Step(fn, outputs))
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
        return self.solver.solve(self.steps, inputs)

    def visualize(self, 
                  inputs: List[str] = None, 
                  output_path: str = None, 
                  orientation: Literal["TB", "LR"] = "TB",
                  graph_type: Literal["flow", "bipartite"] = "flow",
                  view: bool = True):
        """
        Generates a Graphviz diagram of the pipeline.
        
        :param inputs: List of input keys available at runtime.
        :param output_path: Path to save file (e.g., 'pipeline.pdf', 'graph.png').
                            If None, opens in the default viewer.
        :param orientation: 'TB' (Top-Bottom) or 'LR' (Left-Right).
        :param graph_type: 'flow' (logic view) or 'bipartite' (data view).
        :param view: Whether to try opening the generated file automatically.
        """
        input_set = set(inputs or [])
        
        visualize_pipeline(
            steps=self.steps,
            inputs=input_set,
            output_path=output_path,
            orientation=orientation,
            graph_type=graph_type,
            view=view
        )