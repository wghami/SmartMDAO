import os
import logging
from typing import List, Set, Dict, Literal, Optional, Tuple

from .models import Step

# Initialize module-level logger
logger = logging.getLogger(__name__)

# Try importing graphviz; handle absence gracefully
try:
    import graphviz
except ImportError:
    graphviz = None
    logger.warning("Graphviz not found. Visualization features will be unavailable.")


class PipelineVisualizer:
    """
    A modern, modular visualizer for the Pipeline using Graphviz.
    Focuses on standardizing workflow visualization with clear separation of concerns.
    """

    # --- Standard Palette (Material Design Pastels) ---
    STYLE_INPUT = {
        "shape": "parallelogram", 
        "style": "filled", 
        "fillcolor": "#E3F2FD", # Blue 50
        "color": "#1565C0",     # Blue 800
        "penwidth": "1.5",
        "margin": "0.2"
    }
    STYLE_STEP = {
        "shape": "component", 
        "style": "filled", 
        "fillcolor": "#FFF3E0", # Orange 50
        "color": "#EF6C00",     # Orange 800
        "penwidth": "1.5",
        "margin": "0.3"
    }
    STYLE_INTERMEDIATE = {
        "shape": "ellipse", 
        "style": "filled", 
        "fillcolor": "#F5F5F5", # Grey 100
        "color": "#757575",     # Grey 600
        "penwidth": "1.0",
        "height": "0.4"
    }
    STYLE_FINAL = {
        "shape": "parallelogram", 
        "style": "filled", 
        "fillcolor": "#E8F5E9", # Green 50
        "color": "#2E7D32",     # Green 800
        "penwidth": "2.0",      # Thicker border for emphasis
        "peripheries": "2",     # Double border
        "margin": "0.2"
    }
    STYLE_MISSING = {
        "shape": "hexagon",
        "style": "filled",
        "fillcolor": "#FFEBEE", # Red 50
        "color": "#C62828",     # Red 800
        "penwidth": "2.0"
    }

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
        
        # Initialize the graph
        self.dot = graphviz.Digraph(comment='Pipeline Graph')
        self._setup_graph_attributes()

    def _setup_graph_attributes(self):
        """Configures global graph styling for a professional look."""
        self.dot.attr(rankdir=self.orientation)
        self.dot.attr(compound='true') # Allow edges between clusters
        
        # Global typography
        self.dot.attr('node', fontname='Helvetica', fontsize='11')
        self.dot.attr('edge', fontname='Helvetica', fontsize='9', color='#616161')
        
        # 'ortho' provides clean, rect-linear lines suitable for technical diagrams
        # 'splines'='polyline' is also a good option if ortho gets messy.
        self.dot.attr(splines='ortho') 

    def build(self, graph_type: Literal["flow", "bipartite"] = "flow") -> "PipelineVisualizer":
        """
        Builds the nodes and edges.
        Note: The 'bipartite' (Data Flow) view is recommended for detailed analysis 
        of Inputs vs Intermediates vs Finals.
        """
        if graph_type == "bipartite":
            self._build_bipartite_standard()
        else:
            self._build_flow_standard()
        return self

    def render(self, output_path: Optional[str] = None, view: bool = True):
        """
        Renders the graph to a file or temporary view.
        """
        try:
            if output_path:
                filename, ext = os.path.splitext(output_path)
                fmt = ext.lstrip('.').lower() if ext else 'pdf'
                out_file = self.dot.render(filename, format=fmt, cleanup=True, view=view)
                if not view:
                    logger.info(f"Pipeline diagram saved to: {out_file}")
            else:
                self.dot.view(cleanup=True)
                logger.info("Pipeline diagram opened in viewer.")
        except Exception as e:
            logger.error(f"Graph rendered successfully, but viewer failed: {e}")
            if output_path:
                logger.info(f"File saved at: {output_path}")

    # --- Classification Logic ---

    def _analyze_variables(self) -> Tuple[Set[str], Set[str], Set[str], Set[str], Dict[str, Step]]:
        """
        Categorizes all variables in the pipeline.
        Returns: (inputs, intermediates, finals, missing, producer_map)
        """
        producers_map = {}
        consumed = set()
        produced = set()

        for step in self.steps:
            # Outputs
            for out in step.resolve_output_names():
                producers_map[out] = step
                produced.add(out)
            
            # Inputs (Use unwrapped signature)
            sig = step.get_signature()
            for param in sig.parameters:
                consumed.add(param)

        # 2. Categorize
        # Inputs: Variables consumed but NOT produced internally.
        # (We strictly use input_keys to validate, but graph logic relies on structural dependency)
        real_inputs = {v for v in consumed if v not in produced}
        
        # Missing: Required inputs that are NOT in the provided input_keys
        missing = {v for v in real_inputs if v not in self.input_keys}
        
        # Valid Inputs: Real inputs that exist in input_keys
        valid_inputs = real_inputs.intersection(self.input_keys)
        
        # Intermediates: Produced AND Consumed
        intermediates = produced.intersection(consumed)
        
        # Finals: Produced but NEVER Consumed
        finals = produced - consumed

        return valid_inputs, intermediates, finals, missing, producers_map

    # --- Bipartite (Data Flow) Strategy ---

    def _build_bipartite_standard(self):
        """
        Constructs a Data Flow Diagram (DFD).
        Strictly separates: Input Nodes -> Step Nodes -> Intermediate Nodes -> Step Nodes -> Final Nodes.
        """
        inputs, intermediates, finals, missing, producers = self._analyze_variables()

        # 1. Draw Inputs (Rank Source to force top/left)
        with self.dot.subgraph(name='cluster_inputs') as c:
            c.attr(rank='source', style='invis') # Invisible container for grouping
            for var in inputs:
                self._add_node(c, f"Var_{var}", var, self.STYLE_INPUT)
            for var in missing:
                self._add_node(c, f"Missing_{var}", f"{var} (?)", self.STYLE_MISSING)

        # 2. Draw Finals (Rank Sink to force bottom/right)
        with self.dot.subgraph(name='cluster_finals') as c:
            c.attr(rank='sink', style='invis')
            for var in finals:
                self._add_node(c, f"Var_{var}", var, self.STYLE_FINAL)

        # 3. Draw Intermediates
        for var in intermediates:
            self._add_node(self.dot, f"Var_{var}", var, self.STYLE_INTERMEDIATE)

        # 4. Draw Steps
        for step in self.steps:
            self._add_step_node(self.dot, step)

        # 5. Draw Edges
        for step in self.steps:
            step_id = self._node_id(step)
            sig = step.get_signature()

            # Inputs to Step
            for param in sig.parameters:
                if param in missing:
                    self.dot.edge(f"Missing_{param}", step_id, style="dotted", color="#D32F2F")
                else:
                    # It's either a valid input or an intermediate/produced var
                    var_id = f"Var_{param}"
                    self.dot.edge(var_id, step_id)

            # Step to Outputs
            for out in step.resolve_output_names():
                var_id = f"Var_{out}"
                self.dot.edge(step_id, var_id)

    # --- Flow Strategy (Process Flow) ---

    def _build_flow_standard(self):
        """
        Constructs a Process Flow Diagram.
        Focuses on Steps. Data is shown as explicit nodes ONLY if it is an Input or Final Output.
        Intermediates are labels on edges.
        """
        inputs, intermediates, finals, missing, producers = self._analyze_variables()
        step_indices = {step: i for i, step in enumerate(self.steps)}

        # 1. Draw Inputs
        with self.dot.subgraph(name='cluster_inputs') as c:
            c.attr(rank='source', style='invis')
            for var in inputs:
                self._add_node(c, f"Input_{var}", var, self.STYLE_INPUT)
            for var in missing:
                self._add_node(c, f"Missing_{var}", f"{var} (?)", self.STYLE_MISSING)

        # 2. Draw Finals
        with self.dot.subgraph(name='cluster_finals') as c:
            c.attr(rank='sink', style='invis')
            for var in finals:
                 # Note: In flow view, we link the step directly to this final node
                self._add_node(c, f"Final_{var}", var, self.STYLE_FINAL)

        # 3. Draw Steps
        for step in self.steps:
            self._add_step_node(self.dot, step)

        # 4. Draw Edges
        for step in self.steps:
            step_id = self._node_id(step)
            sig = step.get_signature()
            
            for param in sig.parameters:
                # Case A: Missing
                if param in missing:
                    self.dot.edge(f"Missing_{param}", step_id, style="dotted", color="#D32F2F")
                
                # Case B: External Input
                elif param in inputs:
                    self.dot.edge(f"Input_{param}", step_id)

                # Case C: Produced by another step (Intermediate)
                elif param in producers:
                    producer = producers[param]
                    prod_id = self._node_id(producer)
                    
                    # Cycle Detection
                    is_feedback = step_indices[producer] >= step_indices[step]
                    style = "dashed" if is_feedback else "solid"
                    color = "#D32F2F" if is_feedback else "#616161"
                    
                    self.dot.edge(prod_id, step_id, label=param, style=style, color=color)

        # 5. Link Steps to Final Outputs
        for step in self.steps:
            step_id = self._node_id(step)
            for out in step.resolve_output_names():
                if out in finals:
                    self.dot.edge(step_id, f"Final_{out}")

    # --- Helpers ---

    def _node_id(self, step: Step) -> str:
        return f"Step_{id(step)}"

    def _add_node(self, graph, node_id: str, label: str, style_dict: Dict[str, str]):
        """Generic node adder using a style dictionary."""
        # Make a copy to avoid mutating the class constant
        attrs = style_dict.copy()
        attrs['label'] = label
        graph.node(node_id, **attrs)

    def _add_step_node(self, graph, step: Step):
        """Adds a function/step node."""
        attrs = self.STYLE_STEP.copy()
        # HTML label for bold text
        attrs['label'] = f"<<b>{step.name}</b>>" 
        graph.node(self._node_id(step), **attrs)

# API adapter
def visualize_pipeline(
    steps: List[Step], 
    inputs: Set[str], 
    output_path: Optional[str] = None,
    orientation: str = "TD",
    graph_type: Literal["flow", "bipartite"] = "flow",
    view: bool = True
):
    viz = PipelineVisualizer(steps, inputs, orientation)
    viz.build(graph_type).render(output_path, view=view)