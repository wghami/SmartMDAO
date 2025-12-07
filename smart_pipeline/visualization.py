import inspect
import webbrowser
import tempfile
import base64
import json
import urllib.request
from pathlib import Path
from typing import List, Set, Literal
from .models import Step

def build_mermaid_graph(
    steps: List[Step], 
    input_keys: Set[str], 
    orientation: str = "TD",
    graph_type: Literal["flow", "bipartite"] = "flow"
) -> str:
    """
    Constructs a Mermaid graph definition.
    
    :param steps: List of pipeline steps.
    :param input_keys: Set of input variable names.
    :param orientation: Graph orientation ('TD' or 'LR').
    :param graph_type: 'flow' (Step->Step) or 'bipartite' (Step->Variable->Step).
    """
    
    # 1. Deterministic Sort: Ensure graph doesn't change if .add() order changes
    steps = sorted(steps, key=lambda s: s.name)
    
    # Map for quick lookups
    producers = {}
    step_indices = {step: i for i, step in enumerate(steps)}
    all_consumed = set()
    
    for step in steps:
        for out_name in step.resolve_output_names():
            producers[out_name] = step

    lines = [f"graph {orientation};"]
    
    # Style Definitions
    lines.append("classDef default fill:#f9f9f9,stroke:#333,stroke-width:1px;")
    lines.append("classDef inputNode fill:#e1f5fe,stroke:#01579b,stroke-dasharray: 5 5;")
    lines.append("classDef stepNode fill:#fff9c4,stroke:#fbc02d,stroke-width:2px;")
    lines.append("classDef varNode fill:#e8f5e9,stroke:#2e7d32,rx:5,ry:5;")
    lines.append("classDef outputNode fill:#ccffcc,stroke:#090,stroke-width:2px;")

    if graph_type == "bipartite":
        _build_bipartite_graph(steps, input_keys, producers, lines)
    else:
        _build_flow_graph(steps, input_keys, producers, step_indices, lines, all_consumed)

    return "\n".join(lines)

def _build_flow_graph(steps, input_keys, producers, step_indices, lines, all_consumed):
    """Standard Step-to-Step visualization."""
    
    link_count = 0 
    style_commands = [] # Collect styles to append at the end

    # Define Nodes first
    for step in steps:
        step_id = f"node_{id(step)}"
        lines.append(f'{step_id}("{step.name}"):::stepNode')

    # Define Edges
    for step in steps:
        step_id = f"node_{id(step)}"
        sig = inspect.signature(step.fn)
        
        for param_name in sig.parameters:
            all_consumed.add(param_name)
            
            # Case 1: Input from another Step
            if param_name in producers:
                producer_step = producers[param_name]
                prod_id = f"node_{id(producer_step)}"
                
                # Detect Feedback
                is_feedback = step_indices[producer_step] >= step_indices[step]
                arrow = "-.->" if is_feedback else "-->"
                
                if is_feedback:
                     lines.append(f'{prod_id} {arrow} |"{param_name}"| {step_id}')
                     # Defer style definition to ensure link exists
                     style_commands.append(f"linkStyle {link_count} stroke:#f44336,stroke-width:2px,stroke-dasharray: 5 5;")
                else:
                     lines.append(f'{prod_id} -- "{param_name}" --> {step_id}')
                
                link_count += 1

            # Case 2: External Input
            elif param_name in input_keys:
                input_id = f"Input_{param_name}"
                # Check if we already added this input node
                if f'{input_id}([' not in "".join(lines):
                    lines.append(f'{input_id}(["{param_name}"]):::inputNode')
                
                lines.append(f'{input_id} -.-> {step_id}')
                link_count += 1
            
            # Case 3: Missing
            else:
                lines.append(f'Missing_{param_name}[("???")] -.-> {step_id}')
                lines.append(f"style Missing_{param_name} fill:#ffaaaa,stroke:#f00")
                link_count += 1

    # Add Final Outputs
    for step in steps:
        step_id = f"node_{id(step)}"
        for out_name in step.resolve_output_names():
            if out_name not in all_consumed:
                out_node_id = f"Output_{out_name}"
                # FIXED: syntax was [["..."]:::class], changed to [["..."]]:::class
                lines.append(f'{step_id} -- "{out_name}" --> {out_node_id}[["Final: {out_name}"]]:::outputNode')
                link_count += 1

    # Append collected styles
    lines.extend(style_commands)

def _build_bipartite_graph(steps, input_keys, producers, lines):
    """
    Data-Centric visualization: Steps and Variables are both nodes.
    """
    # 1. Define Step Nodes
    for step in steps:
        step_id = f"step_{id(step)}"
        lines.append(f'{step_id}["{step.name}"]:::stepNode')

    # 2. Define Variable Nodes & Edges
    for step in steps:
        step_id = f"step_{id(step)}"
        
        # Inputs (Variable -> Step)
        sig = inspect.signature(step.fn)
        for param_name in sig.parameters:
            var_id = f"var_{param_name}"
            
            if param_name in input_keys:
                 lines.append(f'{var_id}(["{param_name} (Input)"]):::inputNode')
            else:
                 lines.append(f'{var_id}(["{param_name}"]):::varNode')
            
            lines.append(f'{var_id} --> {step_id}')

        # Outputs (Step -> Variable)
        for out_name in step.resolve_output_names():
            var_id = f"var_{out_name}"
            lines.append(f'{var_id}(["{out_name}"]):::varNode')
            lines.append(f'{step_id} --> {var_id}')

def render_to_browser(graph_def: str):
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><title>Pipeline Visualization</title></head>
    <body style="background-color: #fafafa; font-family: sans-serif;">
        <h2 style="text-align:center; color:#333;">Pipeline Graph</h2>
        <div class="mermaid" style="display:flex; justify-content:center;">{graph_def}</div>
        <script type="module">
            import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
            mermaid.initialize({{ startOnLoad: true, theme: 'neutral' }});
        </script>
    </body>
    </html>
    """
    with tempfile.NamedTemporaryFile('w', delete=False, suffix='.html') as f:
        f.write(html_content)
        url = Path(f.name).as_uri()
    webbrowser.open(url)

def render_to_pdf(graph_def: str, output_path: str):
    state = {"code": graph_def, "mermaid": {"theme": "neutral"}}
    json_str = json.dumps(state)
    encoded = base64.urlsafe_b64encode(json_str.encode('utf-8')).decode('utf-8')
    url = f"https://mermaid.ink/pdf/{encoded}"
    try:
        with urllib.request.urlopen(url) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
            with open(output_path, 'wb') as f:
                f.write(response.read())
    except Exception as e:
        raise RuntimeError(f"PDF Generation failed: {e}")