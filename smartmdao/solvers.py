import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import List, Dict, Any, Set, Protocol, Optional

from .models import Step
from .executor import StepExecutor
from .graph import map_producers as _map_producers, build_dependency_graph as _build_dependency_graph
from .validation import TypeChecker

# Initialize module-level logger
logger = logging.getLogger(__name__)

class Solver(Protocol):
    """Interface for execution logic."""
    def solve(self, steps: List[Step], inputs: Dict[str, Any], type_checker: Optional[TypeChecker] = None) -> Dict[str, Any]:
        ...

class DAGSolver:
    """
    Standard Topological Sort Solver. 
    Ideal for linear workflows.
    """
    def solve(self, steps: List[Step], inputs: Dict[str, Any], type_checker: Optional[TypeChecker] = None) -> Dict[str, Any]:
        logger.info("DAGSolver started.")
        execution_order = self._topological_sort(steps, set(inputs.keys()))
        logger.debug(f"Topological sort order: {[s.name for s in execution_order]}")

        memory = inputs.copy()

        for step in execution_order:
            StepExecutor.run_step(step, memory, type_checker=type_checker)

        return memory

    def _topological_sort(self, steps: List[Step], input_keys: Set[str]) -> List[Step]:
        producers_map = _map_producers(steps)
        adj_list, indegree = _build_dependency_graph(steps, input_keys, producers_map)

        # Kahn's Algorithm
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
            logger.error("Cycle detected in DAGSolver.")
            raise ValueError("Cycle detected in pipeline. Use HybridSolver or IterativeSolver.")

        return sorted_steps

@dataclass
class IterativeSolver:
    """
    Solves systems with feedback loops.
    """
    max_iterations: int = 100
    tolerance: float = 1e-6
    target_var: Optional[str] = None
    execution_order: Optional[List[str]] = None

    def solve(self, steps: List[Step], inputs: Dict[str, Any], type_checker: Optional[TypeChecker] = None) -> Dict[str, Any]:
        memory = inputs.copy()
        residuals = []

        run_sequence = self._determine_execution_order(steps)
        logger.info(f"IterativeSolver started. Sequence: {[s.name for s in run_sequence]}")

        # Identify variables produced by these steps (for auto-convergence)
        produced_vars = set()
        for s in steps:
            produced_vars.update(s.resolve_output_names())

        for i in range(self.max_iterations):
            # Snapshot state for convergence check
            prev_state = {k: memory.get(k) for k in produced_vars if k in memory}

            # Execute
            for step in run_sequence:
                StepExecutor.run_step(step, memory, type_checker=type_checker)
            
            # Check Convergence
            diff = self._calculate_residual(prev_state, memory, produced_vars)
            residuals.append(diff)
            
            # Only break if we actually calculated a numeric difference (not inf)
            if diff != float('inf') and diff < self.tolerance:
                logger.info(f"Converged at iteration {i+1} with residual {diff:.6e}")
                break
            
            logger.debug(f"Iteration {i+1}: residual {diff:.6e}")
        else:
             logger.warning(f"Reached max_iterations ({self.max_iterations}) without converging. Last residual: {residuals[-1]:.6e}")
        
        # Store residuals (append to potentially existing history from other cycles)
        memory.setdefault('residual_history', []).append(residuals)
        return memory

    def _calculate_residual(self, prev_state: Dict, current_memory: Dict, produced_vars: Set[str]) -> float:
        """
        Calculates the maximum change in variables. 
        """
        if self.target_var:
            p = prev_state.get(self.target_var)
            c = current_memory.get(self.target_var)
            return abs(c - p) if (isinstance(p, (int, float)) and isinstance(c, (int, float))) else float('inf')

        max_diff = 0.0
        numeric_vars_found = False

        for k in produced_vars:
            p = prev_state.get(k)
            c = current_memory.get(k)
            
            # Strictly require both to be numeric
            if isinstance(p, (int, float)) and isinstance(c, (int, float)):
                diff = abs(c - p)
                max_diff = max(max_diff, diff)
                numeric_vars_found = True
        
        if numeric_vars_found:
            return max_diff
            
        # If no numeric variables updated, we can't judge convergence numerically.
        return float('inf')

    def _determine_execution_order(self, steps: List[Step]) -> List[Step]:
        if not self.execution_order:
            return steps 
        
        step_map = {s.name: s for s in steps}
        return [step_map[name] for name in self.execution_order if name in step_map]


class HybridSolver:
    """
    Advanced solver that automatically decomposes the pipeline into 
    Linear (DAG) and Iterative (Cyclic) components (Strongly Connected Components).
    """
    def __init__(self, max_iterations: int = 100, tolerance: float = 1e-6):
        self.max_iterations = max_iterations
        self.tolerance = tolerance

    def solve(self, steps: List[Step], inputs: Dict[str, Any], type_checker: Optional[TypeChecker] = None) -> Dict[str, Any]:
        logger.info("HybridSolver started.")
        input_keys = set(inputs.keys())
        producers_map = _map_producers(steps)
        
        # 1. Build Adjacency Graph (Producer -> Consumer)
        adj_list, _ = _build_dependency_graph(steps, input_keys, producers_map)

        # 2. Find Strongly Connected Components (SCCs)
        sccs = self._tarjan_scc(steps, adj_list)
        logger.debug(f"Detected {len(sccs)} execution blocks (SCCs).")
        
        # 3. Build Condensation Graph (DAG of SCCs)
        scc_map = {step: i for i, cluster in enumerate(sccs) for step in cluster}
        scc_adj = defaultdict(set)
        scc_indegree = defaultdict(int)

        for u in steps:
            u_scc = scc_map[u]
            for v in adj_list[u]:
                v_scc = scc_map[v]
                if u_scc != v_scc:
                    if v_scc not in scc_adj[u_scc]:
                        scc_adj[u_scc].add(v_scc)
                        scc_indegree[v_scc] += 1
        
        # Ensure all SCCs have an entry
        for i in range(len(sccs)):
            if i not in scc_indegree:
                scc_indegree[i] = 0

        # 4. Topological Sort of SCCs
        queue = deque([i for i, deg in scc_indegree.items() if deg == 0])
        execution_plan = []
        
        while queue:
            current_scc_idx = queue.popleft()
            execution_plan.append(sccs[current_scc_idx])
            
            for neighbor_scc in scc_adj[current_scc_idx]:
                scc_indegree[neighbor_scc] -= 1
                if scc_indegree[neighbor_scc] == 0:
                    queue.append(neighbor_scc)

        # 5. Execute
        memory = inputs.copy()
        
        for group in execution_plan:
            # Case A: Linear
            if len(group) == 1 and group[0] not in adj_list[group[0]]:
                step = group[0]
                StepExecutor.run_step(step, memory, type_checker=type_checker)
                continue

            # Case B: Cyclic
            # Sort alphabetically to ensure deterministic execution order within the cycle
            group_sorted = sorted(group, key=lambda s: s.name)

            logger.info(f"Cyclic Block Detected: {[s.name for s in group_sorted]}")
            sub_solver = IterativeSolver(
                max_iterations=self.max_iterations,
                tolerance=self.tolerance
            )

            cycle_results = sub_solver.solve(group_sorted, memory, type_checker=type_checker)
            memory.update(cycle_results)

        return memory

    def _tarjan_scc(self, steps: List[Step], adj_list: Dict[Step, List[Step]]) -> List[List[Step]]:
        index = 0
        indices = {}
        lowlinks = {}
        stack = []
        on_stack = set()
        sccs = []

        def strongconnect(v):
            nonlocal index
            indices[v] = index
            lowlinks[v] = index
            index += 1
            stack.append(v)
            on_stack.add(v)

            for w in adj_list[v]:
                if w not in indices:
                    strongconnect(w)
                    lowlinks[v] = min(lowlinks[v], lowlinks[w])
                elif w in on_stack:
                    lowlinks[v] = min(lowlinks[v], indices[w])

            if lowlinks[v] == indices[v]:
                new_scc = []
                while True:
                    w = stack.pop()
                    on_stack.remove(w)
                    new_scc.append(w)
                    if w == v:
                        break
                sccs.append(new_scc)

        for step in steps:
            if step not in indices:
                strongconnect(step)

        return sccs