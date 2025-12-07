import inspect
import webbrowser
import tempfile
import base64
import json
import urllib.request
from pathlib import Path
from typing import List, Set

from .models import Step
from .utils import resolve_output_names

def build_mermaid_graph(steps: List[Step], input_keys: Set[str]) -> str:
    """Constructs the Mermaid flowchart syntax."""
    producers = {} 
    all_consumed = set()
    
    # 1. Identify all producers
    for step in steps:
        for out_name in resolve_output_names(step):
            producers[out_name] = step

    # 2. Identify all consumed variables
    for step in steps:
        sig = inspect.signature(step.fn)
        for param_name in sig.parameters:
            all_consumed.add(param_name)

    lines = ["graph TD;", "classDef default fill:#f9f,stroke:#333,stroke-width:2px;"]
    
    for step in steps:
        step_id = f"node_{id(step)}"
        lines.append(f'{step_id}("{step.name}")')
        
        # --- Draw Inputs ---
        sig = inspect.signature(step.fn)
        for param_name in sig.parameters:
            if param_name in producers:
                producer_step = producers[param_name]
                prod_id = f"node_{id(producer_step)}"
                lines.append(f'{prod_id} -- "{param_name}" --> {step_id}')
            elif param_name in input_keys:
                lines.append(f'Input_{param_name}(["Input: {param_name}"]) -- "{param_name}" --> {step_id}')
                lines.append(f"style Input_{param_name} fill:#fff,stroke:#333,stroke-dasharray: 5 5")
            else:
                lines.append(f'Missing_{param_name}[("???")] -. "{param_name}" .-> {step_id}')
                lines.append(f"style Missing_{param_name} fill:#ffaaaa,stroke:#f00")

        # --- Draw Outputs ---
        outputs = resolve_output_names(step)
        for out_name in outputs:
            if out_name not in all_consumed:
                out_node_id = f"Output_{out_name}"
                lines.append(f'{step_id} -- "{out_name}" --> {out_node_id}[["Final: {out_name}"]]')
                lines.append(f"style {out_node_id} fill:#ccffcc,stroke:#090,stroke-width:2px")

    return "\n".join(lines)


def render_to_browser(graph_def: str):
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><title>Pipeline Visualization</title></head>
    <body>
        <div class="mermaid">{graph_def}</div>
        <script type="module">
            import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
            mermaid.initialize({{ startOnLoad: true }});
        </script>
    </body>
    </html>
    """
    with tempfile.NamedTemporaryFile('w', delete=False, suffix='.html') as f:
        f.write(html_content)
        url = Path(f.name).as_uri()
    
    webbrowser.open(url)


def render_to_pdf(graph_def: str, output_path: str):
    state = {"code": graph_def, "mermaid": {"theme": "default"}}
    json_str = json.dumps(state)
    encoded = base64.urlsafe_b64encode(json_str.encode('utf-8')).decode('utf-8')
    url = f"https://mermaid.ink/pdf/{encoded}"
    
    try:
        with urllib.request.urlopen(url) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status}")
            data = response.read()
            
        with open(output_path, 'wb') as f:
            f.write(data)
    except Exception as e:
        raise RuntimeError(f"Could not generate PDF via mermaid.ink: {e}")