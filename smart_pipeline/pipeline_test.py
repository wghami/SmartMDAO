import inspect
import webbrowser
import tempfile
import base64
import json
import urllib.request
from dataclasses import dataclass, field, is_dataclass, asdict
from collections import defaultdict, deque
from pathlib import Path
from typing import Callable, List, Optional, Any, Dict, Set, Protocol, Union

# ==============================================================================
# 1. CORE DATA STRUCTURES
# ==============================================================================

@dataclass(eq=False)
class Step:
    """
    Represents a single node in the computation graph.
    eq=False ensures hashability is based on object identity.
    """
    fn: Callable
    manual_outputs: Optional[List[str]] = None

    @property
    def name(self):
        return self.fn.__name__

    def resolve_output_names(self) -> List[str]:
        """Determines variable names this step produces."""
        if self.manual_outputs:
            return self.manual_outputs

        sig = inspect.signature(self.fn)
        ann = sig.return_annotation
        
        # If the function returns a Dataclass, use field names
        if isinstance(ann, type) and is_dataclass(ann):
            return list(ann.__dataclass_fields__.keys())

        # Default: use function name
        return [self.name]

# ==============================================================================
# 2. EXECUTOR ENGINE (The "How")
# ==============================================================================

class StepExecutor:
    """
    Static helper responsible for binding arguments from memory 
    and updating memory with results.
    """
    @staticmethod
    def run_step(step: Step, memory: Dict[str, Any]):
        sig = inspect.signature(step.fn)
        
        # 1. Bind Arguments
        params = {}
        missing_required = []
        
        for name, param in sig.parameters.items():
            if name in memory:
                params[name] = memory[name]
            elif param.default == inspect.Parameter.empty:
                missing_required.append(name)
        
        if missing_required:
            raise KeyError(
                f"Step '{step.name}' cannot run. Missing inputs: {missing_required}. "
                f"Available in memory: {list(memory.keys())}"
            )
        
        # 2. Execute
        try:
            result = step.fn(**params)
        except Exception as e:
            raise RuntimeError(f"Error executing step '{step.name}': {e}") from e

        # 3. Store Result
        StepExecutor._update_memory(step, result, memory)

    @staticmethod
    def _update_memory(step: Step, result: Any, memory: Dict[str, Any]):
        output_keys = step.resolve_output_names()

        # Case A: Explicit Manual Outputs (e.g. outputs=['a', 'b'])
        if step.manual_outputs:
            if len(output_keys) == 1:
                memory[output_keys[0]] = result
                return
            
            # Handle Dictionary Return with Manual Outputs
            if isinstance(result, dict):
                for k in output_keys:
                    if k not in result:
                        raise KeyError(f"Step '{step.name}' expected output key '{k}' but it was missing in returned dict.")
                    memory[k] = result[k]
                return

            # Handle Tuple/List Return with Manual Outputs
            if not isinstance(result, (list, tuple)):
                raise TypeError(f"Step '{step.name}' expected iterable (or dict) output for keys {output_keys}, got {type(result)}")
            
            if len(result) != len(output_keys):
                raise ValueError(f"Step '{step.name}' returned {len(result)} items, expected {len(output_keys)}")
            
            for k, v in zip(output_keys, result):
                memory[k] = v
            return

        # Case B: Dataclass Expansion (Auto-unpacking based on type hint/runtime check)
        if is_dataclass(result):
            memory.update(asdict(result))
            return

        # Case C: Single Default Output
        memory[output_keys[0]] = result

# ==============================================================================
# 3. SOLVER STRATEGIES (The "When")
# ==============================================================================

class Solver(Protocol):
    """Interface for execution logic."""
    def solve(self, steps: List[Step], inputs: Dict[str, Any]) -> Dict[str, Any]:
        ...

class DAGSolver:
    """
    Standard Topological Sort Solver. 
    Ideal for linear workflows.
    """
    def solve(self, steps: List[Step], inputs: Dict[str, Any]) -> Dict[str, Any]:
        execution_order = self._topological_sort(steps, set(inputs.keys()))
        memory = inputs.copy()
        
        for step in execution_order:
            StepExecutor.run_step(step, memory)
            
        return memory

    def _topological_sort(self, steps: List[Step], input_keys: Set[str]) -> List[Step]:
        # 1. Map Outputs -> Producer Step
        producers_map = {} 
        for step in steps:
            for out_name in step.resolve_output_names():
                producers_map[out_name] = step
        
        # 2. Build Dependency Graph
        adj_list = defaultdict(list)
        indegree = {step: 0 for step in steps}

        for consumer in steps:
            sig = inspect.signature(consumer.fn)
            for param in sig.parameters:
                if param in input_keys:
                    continue 
                
                if param in producers_map:
                    producer = producers_map[param]
                    if producer is not consumer:
                        adj_list[producer].append(consumer)
                        indegree[consumer] += 1
                # Note: We can implement stricter checks for missing dependencies here.

        # 3. Kahn's Algorithm
        queue = deque([s for s, deg in indegree.items() if deg == 0])
        sorted_steps = []

        while queue:
            current = queue.popleft()
            sorted_steps.append(current)

            for neighbor in adj_list[current]:
                indegree[neighbor] -= 1
                if indegree[neighbor] == 0:
                    queue.append(neighbor)

        if len(sorted_steps) != len(steps):
            raise ValueError("Cycle detected in pipeline (or disjoint graph with unresolved deps). Use IterativeSolver for loops.")

        return sorted_steps

@dataclass
class IterativeSolver:
    """
    Solves systems with feedback loops (e.g., Newton's Method).
    """
    max_iterations: int = 10
    tolerance: float = 1e-6
    target_var: Optional[str] = None 

    def solve(self, steps: List[Step], inputs: Dict[str, Any]) -> Dict[str, Any]:
        memory = inputs.copy()
        
        for i in range(self.max_iterations):
            prev_val = memory.get(self.target_var) if self.target_var else None
            
            for step in steps:
                StepExecutor.run_step(step, memory)
            
            if self.target_var and prev_val is not None:
                current_val = memory.get(self.target_var)
                if isinstance(current_val, (int, float)) and isinstance(prev_val, (int, float)):
                    if abs(current_val - prev_val) < self.tolerance:
                        break
        
        return memory

# ==============================================================================
# 4. PIPELINE FACADE
# ==============================================================================

@dataclass
class Pipeline:
    steps: list[Step] = field(default_factory=list)
    solver: Solver = field(default_factory=DAGSolver)

    def add(self, fn: Callable, outputs: list[str] = None):
        self.steps.append(Step(fn, outputs))
        return self

    def step(self, fn: Callable = None, *, outputs: List[str] = None):
        if fn is not None and callable(fn):
            self.add(fn, outputs=outputs)
            return fn
        def wrapper(func):
            self.add(func, outputs=outputs)
            return func
        return wrapper

    def run(self, **inputs):
        return self.solver.solve(self.steps, inputs)

    # --- Visualization ---
    def visualize(self, inputs: List[str] = None, output_pdf: str = None):
        inputs = set(inputs or [])
        graph_def = self._build_mermaid_graph(inputs)

        if output_pdf:
            self._render_to_pdf(graph_def, output_pdf)
            print(f"Pipeline diagram saved to: {output_pdf}")
        else:
            self._render_to_browser(graph_def)

    def _build_mermaid_graph(self, input_keys: Set[str]) -> str:
        producers = {}
        all_consumed = set()
        
        for step in self.steps:
            for out_name in step.resolve_output_names():
                producers[out_name] = step

        lines = ["graph TD;", "classDef default fill:#f9f,stroke:#333,stroke-width:2px;"]

        for step in self.steps:
            step_id = f"node_{id(step)}"
            lines.append(f'{step_id}("{step.name}")')
            
            sig = inspect.signature(step.fn)
            for param_name in sig.parameters:
                all_consumed.add(param_name)
                
                if param_name in producers:
                    producer_step = producers[param_name]
                    if producer_step is step:
                         lines.append(f'{step_id} -- "{param_name} (feedback)" --> {step_id}')
                    else:
                        prod_id = f"node_{id(producer_step)}"
                        lines.append(f'{prod_id} -- "{param_name}" --> {step_id}')
                elif param_name in input_keys:
                    lines.append(f'Input_{param_name}(["Input: {param_name}"]) -- "{param_name}" --> {step_id}')
                    lines.append(f"style Input_{param_name} fill:#fff,stroke:#333,stroke-dasharray: 5 5")
                else:
                    lines.append(f'Missing_{param_name}[("???")] -. "{param_name}" .-> {step_id}')
                    lines.append(f"style Missing_{param_name} fill:#ffaaaa,stroke:#f00")

            outputs = step.resolve_output_names()
            for out_name in outputs:
                if out_name not in all_consumed:
                    out_node_id = f"Output_{out_name}"
                    lines.append(f'{step_id} -- "{out_name}" --> {out_node_id}[["Final: {out_name}"]]')
                    lines.append(f"style {out_node_id} fill:#ccffcc,stroke:#090,stroke-width:2px")

        return "\n".join(lines)

    def _render_to_browser(self, graph_def: str):
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

    def _render_to_pdf(self, graph_def: str, output_path: str):
        state = {"code": graph_def, "mermaid": {"theme": "default"}}
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
