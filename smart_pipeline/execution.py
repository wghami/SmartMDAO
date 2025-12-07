import inspect
from dataclasses import is_dataclass, asdict
from collections import defaultdict, deque
from typing import List, Any, Dict

from .models import Step
from .utils import resolve_output_names

def topological_sort(steps: List[Step], inputs: dict) -> List[Step]:
    """
    Sorts steps based on data dependencies using Kahn's Algorithm.
    """
    producers = defaultdict(list)
    consumers = defaultdict(list)

    # 1. Map Producers (Who creates what?)
    for step in steps:
        out_names = resolve_output_names(step)
        for name in out_names:
            producers[name].append(step)

    # 2. Map Consumers (Who needs what?)
    for step in steps:
        sig = inspect.signature(step.fn)
        for param in sig.parameters:
            consumers[step].append(param)

    # 3. Build Graph
    graph = defaultdict(set)
    indegree = {step: 0 for step in steps}

    for step in steps:
        for needed_var in consumers[step]:
            # If variable is in initial inputs, we don't need to wait for a step
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

        for other_step in steps:
            if current_step in graph[other_step]:
                indegree[other_step] -= 1
                graph[other_step].remove(current_step)
                if indegree[other_step] == 0:
                    queue.append(other_step)

    if len(sorted_steps) != len(steps):
        raise ValueError("Cycle detected in the pipeline.")

    return sorted_steps


def execute_step(step: Step, memory: Dict[str, Any]) -> Any:
    """Executes a single step using arguments found in memory."""
    sig = inspect.signature(step.fn)
    try:
        params = {k: memory[k] for k in sig.parameters}
    except KeyError as e:
        raise KeyError(f"Step '{step.name}' failed to run. Missing dependency: {e}")
    
    return step.fn(**params)


def store_result(step: Step, result: Any, memory: Dict[str, Any]):
    """Unpacks results and stores them into the memory dictionary."""
    expected_names = resolve_output_names(step)

    if result is None:
        return

    # Handle manual output overrides (e.g. tuples)
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

    # Handle Dataclasses
    if is_dataclass(result):
        memory.update(asdict(result))
        return

    # Handle Standard Single Return
    memory[expected_names[0]] = result