from dataclasses import dataclass, field
from typing import Callable, List, Optional, Literal

from .models import Step
from .solvers import Solver, DAGSolver
from .visualization import build_mermaid_graph, render_to_browser, render_to_file

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
                  orientation: str = "TD",
                  graph_type: Literal["flow", "bipartite"] = "flow"):
        """
        Generates a Mermaid diagram of the pipeline.
        
        :param inputs: List of input keys available at runtime.
        :param output_path: Path to save file. Supports .pdf, .svg, .png. 
                            Use .svg for single-page large diagrams.
        :param orientation: 'TD' (Top-Down) or 'LR' (Left-Right).
        :param graph_type: 'flow' (default) or 'bipartite' (explicit variable nodes).
        """
        inputs = set(inputs or [])
        graph_def = build_mermaid_graph(self.steps, inputs, orientation, graph_type)

        if output_path:
            render_to_file(graph_def, output_path)
            print(f"Pipeline diagram saved to: {output_path}")
        else:
            render_to_browser(graph_def)