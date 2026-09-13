import logging
from collections import deque
from dataclasses import dataclass, field
from typing import List, Dict, Any, Set, Protocol, Optional, runtime_checkable

from .models import Step
from .executor import StepExecutor
from .graph import (
    map_producers as _map_producers,
    build_dependency_graph as _build_dependency_graph,
    build_execution_plan as _build_execution_plan,
)
from .validation import TypeChecker

# Initialize module-level logger
logger = logging.getLogger(__name__)

class Solver(Protocol):
    """Interface for execution logic."""
    def solve(self, steps: List[Step], inputs: Dict[str, Any], type_checker: Optional[TypeChecker] = None) -> Dict[str, Any]:
        ...

@runtime_checkable
class ConvergenceChecker(Protocol):
    """
    Pluggable strategy deciding how "close" two successive values of a
    coupling variable are, i.e. what a MDA solver's residual means.

    Implement this to customize convergence for domain-specific types
    (e.g. relative error, a norm over arrays) without touching the
    core solvers.
    """
    def distance(self, previous: Any, current: Any) -> float:
        """0.0 means "unchanged"; larger (up to inf) means "still moving"."""
        ...

class StandardConvergenceChecker:
    """
    Numeric values (int/float) converge on shrinking |Δ|, same as before.

    Everything else - strings, dicts, dataclasses, sets, custom objects -
    converges on structural equality: unchanged since the last iteration
    is treated as "at rest" (distance 0.0), anything else as "still moving"
    (distance inf). This lets IterativeSolver/HybridSolver detect a fixed
    point on non-numeric coupling variables (e.g. an agent-authored MDA
    negotiating a shared plan or a resolved set of dependencies), which
    OpenMDAO/GEMSEO-style numeric-residual solvers can't express.
    """
    def distance(self, previous: Any, current: Any) -> float:
        if isinstance(previous, (int, float)) and isinstance(current, (int, float)):
            return abs(current - previous)
        try:
            return 0.0 if previous == current else float('inf')
        except Exception:
            # Values that can't be compared (e.g. raise on `==`) are
            # treated as "still moving" - never falsely claim convergence.
            return float('inf')

class OscillationDetectedError(RuntimeError):
    """
    Raised when a coupling variable is cycling between repeating values
    instead of settling, so the run can be abandoned rather than burning
    the remaining iterations on a fixed point that will never arrive.

    Carries the detected `period` and the `cycle` of values themselves,
    because "which two answers is it flip-flopping between?" is the
    actionable part.
    """
    def __init__(self, period: int, cycle: List[Any], iteration: int):
        self.period = period
        self.cycle = cycle
        self.iteration = iteration
        super().__init__(
            f"Coupling variable is oscillating with period {period} "
            f"(detected at iteration {iteration}); it cycles between: {cycle!r}. "
            f"This will never satisfy the convergence tolerance."
        )


@dataclass
class OscillationAwareConvergenceChecker:
    """
    Wraps another ConvergenceChecker and additionally notices when a value
    is *cycling* rather than settling - A -> B -> A -> B forever.

    `StandardConvergenceChecker` reports non-numeric distance as a binary
    0.0-or-inf, so a 2-cycle is indistinguishable from steady progress: both
    look like inf every sweep. The loop therefore runs to `max_iterations`.
    That is merely slow for cheap numeric disciplines and genuinely expensive
    when a step costs an LLM call, which is the case this exists for
    (see docs/design/002-agent-as-discipline.md).

    IMPORTANT - use with `IterativeSolver(target_var=...)`.
    `_calculate_residual` calls `distance()` once per *produced variable* when
    no target is set, iterating a `set` whose order is arbitrary. This checker
    keeps a single history and cannot tell those interleaved calls apart, so
    without `target_var` its history is meaningless. With a target it is called
    exactly once per iteration, which is the contract it needs.

    Detection requires two full repetitions of a block, so a period-p cycle is
    reported after 2p sweeps. Period 1 is not a cycle - it is convergence, and
    the inner checker already reports it as 0.0.
    """
    inner: ConvergenceChecker = field(default_factory=StandardConvergenceChecker)
    max_period: int = 4
    raise_on_detection: bool = True

    history: List[Any] = field(default_factory=list, init=False, repr=False)
    detected_period: Optional[int] = field(default=None, init=False)
    detected_cycle: Optional[List[Any]] = field(default=None, init=False)

    def reset(self) -> None:
        """
        Clears accumulated history.

        Call this between runs. The instance is stateful and nothing in the
        solvers resets it: `HybridSolver` reuses one checker across every
        cyclic block, and a `Pipeline` reuses its solver across every
        `run()`. History is cleared automatically on convergence, but a block
        that exhausts `max_iterations` leaves its history behind.
        """
        self.history.clear()
        self.detected_period = None
        self.detected_cycle = None

    def distance(self, previous: Any, current: Any) -> float:
        distance = self.inner.distance(previous, current)

        if distance == 0.0:
            # At rest - this run is over as far as this variable is concerned.
            self.reset()
            return distance

        self.history.append(current)

        period = self._find_period()
        if period is not None:
            self.detected_period = period
            self.detected_cycle = list(self.history[-period:])
            if self.raise_on_detection:
                raise OscillationDetectedError(
                    period=period,
                    cycle=self.detected_cycle,
                    iteration=len(self.history),
                )

        # Keep only what the widest period comparison can still need.
        excess = len(self.history) - 2 * self.max_period
        if excess > 0:
            del self.history[:excess]

        return distance

    def _find_period(self) -> Optional[int]:
        """
        Smallest p in [2, max_period] whose last two p-length blocks match.

        Values only have to support `==` (the same contract
        `StandardConvergenceChecker` relies on). A type that raises on
        comparison is treated as "no cycle here" rather than being allowed to
        break the solve.
        """
        for period in range(2, self.max_period + 1):
            if len(self.history) < 2 * period:
                return None
            try:
                if self.history[-period:] == self.history[-2 * period:-period]:
                    return period
            except Exception:
                continue
        return None


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
    convergence_checker: ConvergenceChecker = field(default_factory=StandardConvergenceChecker)

    def solve(self, steps: List[Step], inputs: Dict[str, Any], type_checker: Optional[TypeChecker] = None) -> Dict[str, Any]:
        memory = inputs.copy()
        residuals = []

        run_sequence = self.determine_execution_order(steps)
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
        Calculates the maximum change across produced variables, via
        `self.convergence_checker` (numeric |Δ| by default, structural
        equality for anything else).
        """
        if self.target_var:
            p = prev_state.get(self.target_var)
            c = current_memory.get(self.target_var)
            return self.convergence_checker.distance(p, c)

        if not produced_vars:
            return float('inf')

        return max(
            self.convergence_checker.distance(prev_state.get(k), current_memory.get(k))
            for k in produced_vars
        )

    def determine_execution_order(self, steps: List[Step]) -> List[Step]:
        if not self.execution_order:
            return steps 
        
        step_map = {s.name: s for s in steps}
        return [step_map[name] for name in self.execution_order if name in step_map]


class HybridSolver:
    """
    Advanced solver that automatically decomposes the pipeline into 
    Linear (DAG) and Iterative (Cyclic) components (Strongly Connected Components).
    """
    def __init__(self, max_iterations: int = 100, tolerance: float = 1e-6,
                 convergence_checker: Optional[ConvergenceChecker] = None):
        self.max_iterations = max_iterations
        self.tolerance = tolerance
        self.convergence_checker = convergence_checker or StandardConvergenceChecker()

    def solve(self, steps: List[Step], inputs: Dict[str, Any], type_checker: Optional[TypeChecker] = None) -> Dict[str, Any]:
        logger.info("HybridSolver started.")

        # Structural decomposition lives in graph.py, so smartmdao.analysis can
        # describe what this solve *would* do without running it.
        execution_plan = _build_execution_plan(steps, set(inputs.keys()))
        logger.debug(f"Detected {len(execution_plan)} execution blocks.")

        memory = inputs.copy()

        for block in execution_plan:
            # Case A: Linear
            if not block.is_cyclic:
                StepExecutor.run_step(block.steps[0], memory, type_checker=type_checker)
                continue

            # Case B: Cyclic - already ordered deterministically by the planner.
            group_sorted = list(block.steps)

            logger.info(f"Cyclic Block Detected: {[s.name for s in group_sorted]}")
            sub_solver = IterativeSolver(
                max_iterations=self.max_iterations,
                tolerance=self.tolerance,
                convergence_checker=self.convergence_checker
            )

            cycle_results = sub_solver.solve(group_sorted, memory, type_checker=type_checker)
            memory.update(cycle_results)

        return memory