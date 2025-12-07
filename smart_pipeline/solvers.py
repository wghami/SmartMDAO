import inspect
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import List, Dict, Any, Set, Protocol, Optional
import warnings

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
    1. If `execution_order` is provided (List of step names), it strictly follows that sequence.
    2. Otherwise, it follows the order in which steps were added to the Pipeline.

    CRITICAL NOTE:
    In coupled systems (e.g., A -> B -> A), the order of execution determines which 
    values (current iteration vs previous iteration) are used by the steps. 
    This affects convergence speed (Gauss-Seidel effect).
    """
    max_iterations: int = 10
    tolerance: float = 1e-6
    target_var: Optional[str] = None
    execution_order: Optional[List[str]] = None

    def solve(self, steps: List[Step], inputs: Dict[str, Any]) -> Dict[str, Any]:
        memory = inputs.copy()
        residuals = []
        
        # Determine the sequence of execution
        run_sequence = self._determine_execution_order(steps)

        for i in range(self.max_iterations):
            prev_val = memory.get(self.target_var) if self.target_var else None
            
            # Execute in the determined sequence
            for step in run_sequence:
                StepExecutor.run_step(step, memory)
            
            if self.target_var and prev_val is not None:
                current_val = memory.get(self.target_var)
                # Check convergence if numeric
                if isinstance(current_val, (int, float)) and isinstance(prev_val, (int, float)):
                    diff = abs(current_val - prev_val)
                    residuals.append(diff)
                    
                    if diff < self.tolerance:
                        break
        
        memory['residual_history'] = residuals
        return memory

    def _determine_execution_order(self, steps: List[Step]) -> List[Step]:
        """
        Sorts the steps based on self.execution_order if provided.
        """
        if not self.execution_order:
            return steps # Fallback to registration order

        # Map name -> Step object
        step_map = {s.name: s for s in steps}
        
        # Validation
        ordered_steps = []
        missing_steps = []
        
        for name in self.execution_order:
            if name in step_map:
                ordered_steps.append(step_map[name])
            else:
                missing_steps.append(name)
        
        if missing_steps:
            raise ValueError(f"IterativeSolver config references missing steps: {missing_steps}")

        # Check if all registered steps are included in the order? 
        # (Optional: we might want to allow running a SUBSET of the pipeline)
        # For now, we assume if you define an order, you only run those steps.
        
        return ordered_steps