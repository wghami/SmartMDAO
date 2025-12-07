import inspect
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import List, Dict, Any, Set, Protocol, Optional

from .models import Step
from .executor import StepExecutor

class Solver(Protocol):
    """Interface for execution logic."""
    def solve(self, steps: List[Step], inputs: Dict[str, Any]) -> Dict[str, Any]:
        ...

class DAGSolver:
    """
    Standard Topological Sort Solver. 
    Ideal for linear workflows.

    Execution Order:
    Automatically reorders steps based on data dependencies (Topological Sort).
    The order in which steps are added to the pipeline DOES NOT affect execution;
    the solver determines the correct mathematical order.
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

    Execution Order:
    Strictly follows the order in which steps were added to the Pipeline ("Naive" execution).
    Unlike DAGSolver, it DOES NOT reorder steps automatically.

    CRITICAL NOTE:
    In coupled systems (e.g., A -> B -> A), the order of execution determines which 
    values (current iteration vs previous iteration) are used by the steps. 
    This affects convergence speed (Gauss-Seidel effect).
    """
    max_iterations: int = 10
    tolerance: float = 1e-6
    target_var: Optional[str] = None 

    def solve(self, steps: List[Step], inputs: Dict[str, Any]) -> Dict[str, Any]:
        memory = inputs.copy()
        residuals = []
        
        for i in range(self.max_iterations):
            prev_val = memory.get(self.target_var) if self.target_var else None
            
            for step in steps:
                StepExecutor.run_step(step, memory)
            
            if self.target_var and prev_val is not None:
                current_val = memory.get(self.target_var)
                # Check convergence if numeric
                if isinstance(current_val, (int, float)) and isinstance(prev_val, (int, float)):
                    diff = abs(current_val - prev_val)
                    residuals.append(diff)
                    
                    if diff < self.tolerance:
                        break
        
        # Store the history of residuals in memory so the user can access it
        memory['residual_history'] = residuals
        return memory