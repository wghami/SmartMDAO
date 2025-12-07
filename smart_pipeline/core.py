from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .models import Step
from .solvers import Solver, DAGSolver
from .visualization import build_mermaid_graph, render_to_browser, render_to_pdf

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

    def visualize(self, inputs: List[str] = None, output_pdf: str = None):
        """
        Generates a Mermaid diagram of the pipeline.
        """
        inputs = set(inputs or [])
        graph_def = build_mermaid_graph(self.steps, inputs)

        if output_pdf:
            render_to_pdf(graph_def, output_pdf)
            print(f"Pipeline diagram saved to: {output_pdf}")
        else:
            render_to_browser(graph_def)