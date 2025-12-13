import inspect
import tempfile
import os
import webbrowser
from typing import List, Set, Dict, Literal, Optional, Any
from collections import defaultdict

from .models import Step

# Try importing graphviz; handle absence gracefully
try:
    import graphviz
except ImportError:
    graphviz = None


class PipelineVisualizer:
    """
    A modern, modular visualizer for the Pipeline using Graphviz.
    Supports 'flow' (logic-centric) and 'bipartite' (data-centric) views.
    """

    def __init__(
        self, 
        steps: List[Step], 
        input_keys: Set[str],
        orientation: Literal['TB', 'LR'] = 'TB'
    ):
        if graphviz is None:
            raise ImportError(
                "The 'graphviz' library is required for visualization. "
                "Please install it using: pip install graphviz"
            )
        
        self.steps = sorted(steps, key=lambda s: s.name)
        self.input_keys = input_keys
        self.orientation = orientation
        
        # Initialize the directed graph
        self.dot = graphviz.Digraph(comment='Pipeline Graph')
        self._setup_graph_attributes()

    def _setup_graph_attributes(self):
        """Configures global graph styling for a modern look."""
        self.dot.attr(rankdir=self.orientation)
        self.dot.attr('node', fontname='Helvetica', fontsize='10', margin='0.2')
        self.dot.attr('edge', fontname='Helvetica', fontsize='9', color='#555555')
        
        # 'curved' conflicts with edge labels in 'dot' layout. 
        # 'ortho' (orthogonal) is clean for technical diagrams.
        self.dot.attr(splines='ortho') 

    def build(self, graph_type: Literal["flow", "bipartite"] = "flow") -> "PipelineVisualizer":
        """
        Builds the nodes and edges based on the selected strategy.
        """
        if graph_type == "bipartite":
            self._build_bipartite()
        else:
            self._build_flow()
        return self

    def render(self, output_path: Optional[str] = None, view: bool = True):
        """
        Renders the graph.
        :param output_path: File path to save (e.g., 'pipeline.pdf', 'graph.png').
                            If None, renders to a temp file and attempts to open it.
        :param view: Whether to try opening the rendered file automatically.
        """
        try:
            if output_path:
                filename, ext = os.path.splitext(output_path)
                # Graphviz 'format' is the extension without dot
                fmt = ext.lstrip('.').lower() if ext else 'pdf'
                
                # render() saves the file. It only opens it if view=True.
                output_file = self.dot.render(filename, format=fmt, cleanup=True, view=view)
                if not view:
                    print(f"Pipeline diagram saved to: {output_file}")
            else:
                # view() saves to temp and opens.
                self.dot.view(cleanup=True)
                
        except Exception as e:
            # Gracefully handle missing viewers (xdg-open, etc.)
            msg = f"Graph rendered successfully, but could not be opened automatically: {e}"
            if output_path:
                msg += f"\nFile saved at: {output_path}"
            print(msg)

    # --- Flow Strategy (Step-to-Step) ---

    def _build_flow(self):
        """
        Constructs a graph focusing on Steps as nodes and dependencies as edges.
        """
        producers = self._map_producers()
        step_indices = {step: i for i, step in enumerate(self.steps)}
        consumed_vars = set()

        # 1. Create Step Nodes
        for step in self.steps:
            self._add_step_node(step)

        # 2. Link Dependencies
        for step in self.steps:
            sig = inspect.signature(step.fn)
            step_id = self._node_id(step)

            for param_name in sig.parameters:
                consumed_vars.add(param_name)

                # Case A: Produced by another step
                if param_name in producers:
                    producer = producers[param_name]
                    prod_id = self._node_id(producer)
                    
                    # Detect Feedback Loop (Back-edge)
                    is_feedback = step_indices[producer] >= step_indices[step]
                    edge_style = "dashed" if is_feedback else "solid"
                    color = "#d32f2f" if is_feedback else "#555555" # Red for feedback
                    
                    # Note: xlabels/labels can be tricky with ortho splines, 
                    # but simple labels usually work.
                    self.dot.edge(prod_id, step_id, label=param_name, style=edge_style, color=color)

                # Case B: External Input
                elif param_name in self.input_keys:
                    input_id = f"Input_{param_name}"
                    self._add_input_node(input_id, param_name)
                    self.dot.edge(input_id, step_id, style="dotted")

                # Case C: Missing
                else:
                    missing_id = f"Missing_{param_name}"
                    self.dot.node(
                        missing_id, label=f"???\n{param_name}", 
                        shape='hexagon', style='filled', fillcolor='#ffcdd2', color='#b71c1c'
                    )
                    self.dot.edge(missing_id, step_id, style="dotted", color='#b71c1c')

        # 3. Mark Final Outputs (Unused variables)
        for step in self.steps:
            step_id = self._node_id(step)
            for out in step.resolve_output_names():
                if out not in consumed_vars:
                    out_id = f"Final_{out}"
                    self.dot.node(
                        out_id, label=f"Result:\n{out}", 
                        shape='ellipse', style='filled', fillcolor='#c8e6c9', color='#2e7d32'
                    )
                    self.dot.edge(step_id, out_id)

    # --- Bipartite Strategy (Step-Variable-Step) ---

    def _build_bipartite(self):
        """
        Constructs a graph where Variables and Steps are distinct nodes.
        Structure: (Step/Input) -> (Variable) -> (Step/Output)
        """
        producers = self._map_producers()

        # 1. Create Step Nodes
        for step in self.steps:
            self._add_step_node(step)

        # 2. Input Variables -> Consumer Steps
        for step in self.steps:
            step_id = self._node_id(step)
            sig = inspect.signature(step.fn)
            
            for param_name in sig.parameters:
                var_id = f"Var_{param_name}"
                
                # Check if it's an external input (not produced by any step)
                if param_name in self.input_keys and param_name not in producers:
                     self._add_variable_node(var_id, param_name, is_input=True)
                else:
                     self._add_variable_node(var_id, param_name)

                self.dot.edge(var_id, step_id)

        # 3. Producer Steps -> Output Variables
        for step in self.steps:
            step_id = self._node_id(step)
            for out_name in step.resolve_output_names():
                var_id = f"Var_{out_name}"
                # Ensure the variable node exists
                self._add_variable_node(var_id, out_name)
                self.dot.edge(step_id, var_id)

    # --- Helpers ---

    def _map_producers(self) -> Dict[str, Step]:
        mapping = {}
        for step in self.steps:
            for out in step.resolve_output_names():
                mapping[out] = step
        return mapping

    def _node_id(self, step: Step) -> str:
        return f"Step_{id(step)}"

    def _add_step_node(self, step: Step):
        # Using HTML-like labels <...> is required for bolding <b>...</b>
        self.dot.node(
            self._node_id(step), 
            label=f"<<b>{step.name}</b>>", 
            shape='component', 
            style='filled', 
            fillcolor='#fff9c4', 
            color='#fbc02d'
        )

    def _add_input_node(self, node_id: str, label: str):
        self.dot.node(
            node_id, 
            label=label, 
            shape='invhouse', 
            style='filled', 
            fillcolor='#e1f5fe', 
            color='#0277bd'
        )

    def _add_variable_node(self, node_id: str, label: str, is_input: bool = False):
        fill = '#e1f5fe' if is_input else '#e8f5e9'
        color = '#0277bd' if is_input else '#2e7d32'
        self.dot.node(
            node_id, 
            label=label, 
            shape='ellipse', 
            style='filled', 
            fillcolor=fill, 
            color=color,
            width='0.4', height='0.3'
        )

# API adapter for backward compatibility/simplicity
def visualize_pipeline(
    steps: List[Step], 
    inputs: Set[str], 
    output_path: Optional[str] = None,
    orientation: str = "TD",
    graph_type: Literal["flow", "bipartite"] = "flow",
    view: bool = True
):
    """
    Convenience function to instantiate and run the visualizer.
    """
    viz = PipelineVisualizer(steps, inputs, orientation)
    viz.build(graph_type).render(output_path, view=view)