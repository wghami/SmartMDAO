import inspect
import webbrowser
import tempfile
import base64
import json
import urllib.request
from dataclasses import dataclass, field, is_dataclass, asdict
from collections import defaultdict, deque
from pathlib import Path
from typing import Callable, List, Optional, Any, Set

# FIX: eq=False makes the class hashable by its object ID.
@dataclass(eq=False)
class Step:
    fn: Callable
    manual_outputs: Optional[List[str]] = None

    @property
    def name(self):
        return self.fn.__name__

@dataclass
class Pipeline:
    steps: list[Step] = field(default_factory=list)

    def add(self, fn: Callable, outputs: list[str] = None):
        """
        Add a step to the pipeline.
        :param fn: The function to execute.
        :param outputs: Optional list of variable names this function produces. 
                        Required if returning a tuple/list you want unpacked.
        """
        self.steps.append(Step(fn, outputs))
        return self

    def step(self, fn: Callable = None, *, outputs: List[str] = None):
        """
        Decorator to register a step. Supports both:
        1. @pipe.step
        2. @pipe.step(outputs=['a', 'b'])
        """
        # Case 1: Called as @pipe.step (no arguments, fn is passed immediately)
        if fn is not None and callable(fn):
            self.add(fn, outputs=outputs)
            return fn

        # Case 2: Called with arguments @pipe.step(outputs=...)
        # We return a wrapper that Python will call with the function later
        def wrapper(func):
            self.add(func, outputs=outputs)
            return func
        
        return wrapper

    def run(self, **inputs):
        # 1. Infer dependencies and sort
        execution_order = self._topological_sort(inputs)

        # 2. Initialize memory
        memory = dict(inputs)

        # 3. Execute
        for step in execution_order:
            result = self._execute_step(step, memory)
            self._store_result(step, result, memory)

        return memory

    # ------------------------------------------------------------
    # Visualization Logic (New Addition)
    # ------------------------------------------------------------
    def visualize(self, inputs: List[str] = None, output_pdf: str = None):
        """
        Generates a Mermaid diagram of the pipeline.
        
        :param inputs: A list of keys expected to be present in the initial inputs.
                       (Used to visualize entry points into the graph).
        :param output_pdf: If provided, saves the diagram as a PDF to this path.
                           Otherwise, opens a temporary HTML pop-up.
        """
        inputs = set(inputs or [])
        graph_def = self._build_mermaid_graph(inputs)

        if output_pdf:
            self._render_to_pdf(graph_def, output_pdf)
            print(f"Pipeline diagram saved to: {output_pdf}")
        else:
            self._render_to_browser(graph_def)

    def _build_mermaid_graph(self, input_keys: Set[str]) -> str:
            """Constructs the Mermaid flowchart syntax."""
            producers = {}  # var_name -> Step (object)
            all_consumed = set() # Track variables that are used as inputs by ANY step
            
            # 1. Identify all producers
            for step in self.steps:
                for out_name in self._resolve_output_names(step):
                    producers[out_name] = step

            # 2. Identify all consumed variables (to find the 'leaf' outputs later)
            for step in self.steps:
                sig = inspect.signature(step.fn)
                for param_name in sig.parameters:
                    all_consumed.add(param_name)

            lines = ["graph TD;", "classDef default fill:#f9f,stroke:#333,stroke-width:2px;"]
            
            # Track which inputs are actually used to draw explicit Input Nodes
            used_inputs = set()

            for step in self.steps:
                step_id = f"node_{id(step)}"
                lines.append(f'{step_id}("{step.name}")')
                
                # --- Draw Inputs (Upstream Dependencies) ---
                sig = inspect.signature(step.fn)
                for param_name in sig.parameters:
                    if param_name in producers:
                        # Connection from another Step
                        producer_step = producers[param_name]
                        prod_id = f"node_{id(producer_step)}"
                        lines.append(f'{prod_id} -- "{param_name}" --> {step_id}')
                    elif param_name in input_keys:
                        # Connection from Global Input
                        lines.append(f'Input_{param_name}(["Input: {param_name}"]) -- "{param_name}" --> {step_id}')
                        lines.append(f"style Input_{param_name} fill:#fff,stroke:#333,stroke-dasharray: 5 5")
                        used_inputs.add(param_name)
                    else:
                        # Missing dependency
                        lines.append(f'Missing_{param_name}[("???")] -. "{param_name}" .-> {step_id}')
                        lines.append(f"style Missing_{param_name} fill:#ffaaaa,stroke:#f00")

                # --- Draw Final Outputs (Downstream Leaves) ---
                # If a variable produced by this step is NOT consumed by any other step,
                # it is a "Final Output". We draw it explicitly.
                outputs = self._resolve_output_names(step)
                for out_name in outputs:
                    if out_name not in all_consumed:
                        # Generate a unique ID for the output node
                        out_node_id = f"Output_{out_name}"
                        # [[text]] syntax creates a 'Terminal' shape in Mermaid
                        lines.append(f'{step_id} -- "{out_name}" --> {out_node_id}[["Final: {out_name}"]]')
                        # Style it Green to signify success/result
                        lines.append(f"style {out_node_id} fill:#ccffcc,stroke:#090,stroke-width:2px")

            return "\n".join(lines)

    def _render_to_browser(self, graph_def: str):
        """Creates a temp HTML file and opens it."""
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Pipeline Visualization</title>
        </head>
        <body>
            <div class="mermaid">
            {graph_def}
            </div>
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

    def _render_to_pdf(self, graph_def: str, output_path: str):
        """
        Uses mermaid.ink service to generate a PDF. 
        Note: This requires internet access. 
        """
        # Mermaid.ink expects a base64 encoded JSON dictionary containing the code
        state = {"code": graph_def, "mermaid": {"theme": "default"}}
        json_str = json.dumps(state)
        encoded = base64.urlsafe_b64encode(json_str.encode('utf-8')).decode('utf-8')
        
        # Create URL (Mermaid.ink handles the conversion)
        url = f"https://mermaid.ink/pdf/{encoded}"
        
        try:
            with urllib.request.urlopen(url) as response:
                if response.status != 200:
                    raise RuntimeError(f"Failed to generate PDF. HTTP {response.status}")
                data = response.read()
                
            with open(output_path, 'wb') as f:
                f.write(data)
        except Exception as e:
            raise RuntimeError(f"Could not generate PDF via mermaid.ink: {e}")

    # ------------------------------------------------------------
    # Dependency Graph & Topological Sort
    # ------------------------------------------------------------
    def _topological_sort(self, inputs) -> List[Step]:
        producers = defaultdict(list)
        consumers = defaultdict(list)

        # 1. Map Producers
        for step in self.steps:
            out_names = self._resolve_output_names(step)
            for name in out_names:
                producers[name].append(step)

        # 2. Map Consumers
        for step in self.steps:
            sig = inspect.signature(step.fn)
            for param in sig.parameters:
                consumers[step].append(param)

        # 3. Build Graph
        graph = defaultdict(set)
        indegree = {step: 0 for step in self.steps}

        for step in self.steps:
            for needed_var in consumers[step]:
                if needed_var in inputs:
                    continue

                if needed_var in producers:
                    for producer_step in producers[needed_var]:
                        if producer_step is not step:
                            if producer_step not in graph[step]:
                                graph[step].add(producer_step)
                                indegree[step] += 1
                else:
                    raise ValueError(
                        f"Step '{step.name}' requires variable '{needed_var}' "
                        f"which is never produced or given as input."
                    )

        # 4. Kahn's Algorithm
        queue = deque([s for s, deg in indegree.items() if deg == 0])
        sorted_steps = []

        while queue:
            current_step = queue.popleft()
            sorted_steps.append(current_step)

            for other_step in self.steps:
                if current_step in graph[other_step]:
                    indegree[other_step] -= 1
                    graph[other_step].remove(current_step)
                    if indegree[other_step] == 0:
                        queue.append(other_step)

        if len(sorted_steps) != len(self.steps):
            raise ValueError("Cycle detected in the pipeline.")

        return sorted_steps

    # ------------------------------------------------------------
    # Execution Logic
    # ------------------------------------------------------------
    def _execute_step(self, step: Step, memory: dict):
        sig = inspect.signature(step.fn)
        try:
            params = {k: memory[k] for k in sig.parameters}
        except KeyError as e:
            raise KeyError(f"Step '{step.name}' failed to run. Missing dependency: {e}")
        
        return step.fn(**params)

    def _store_result(self, step: Step, result: Any, memory: dict):
        expected_names = self._resolve_output_names(step)

        if result is None:
            return

        if step.manual_outputs:
            if len(expected_names) == 1:
                memory[expected_names[0]] = result
            else:
                if not isinstance(result, (list, tuple)):
                    raise TypeError(f"Step '{step.name}' expected iterable, got {type(result)}")
                if len(result) != len(expected_names):
                    raise ValueError(f"Step '{step.name}' returned {len(result)} items, expected {len(expected_names)}")
                
                for name, val in zip(expected_names, result):
                    memory[name] = val
            return

        if is_dataclass(result):
            memory.update(asdict(result))
            return

        memory[expected_names[0]] = result

    # ------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------
    def _resolve_output_names(self, step: Step) -> List[str]:
        if step.manual_outputs:
            return step.manual_outputs

        sig = inspect.signature(step.fn)
        ann = sig.return_annotation
        
        if isinstance(ann, type) and is_dataclass(ann):
            return list(ann.__dataclass_fields__.keys())

        return [step.name]
