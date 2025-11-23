import inspect
from dataclasses import dataclass, field, is_dataclass, asdict
from collections import defaultdict, deque
from typing import Callable, List, Optional, Any

# FIX: eq=False makes the class hashable by its object ID (memory address).
# This allows us to use 'Step' objects as dictionary keys.
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

    def run(self, **inputs):
        # 1. Infer dependencies and sort
        # We pass the full Step objects to keep track of output configurations
        execution_order = self._topological_sort(inputs)

        # 2. Initialize memory
        memory = dict(inputs)

        # 3. Execute
        for step in execution_order:
            result = self._execute_step(step, memory)
            self._store_result(step, result, memory)

        return memory

    # ------------------------------------------------------------
    # Dependency Graph & Topological Sort
    # ------------------------------------------------------------
    def _topological_sort(self, inputs) -> List[Step]:
        producers = defaultdict(list)  # var_name -> list of Steps producing it
        consumers = defaultdict(list)  # Step -> list of var_names needed

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
        graph = defaultdict(set)      # Step -> set of dependency Steps
        indegree = {step: 0 for step in self.steps}

        for step in self.steps:
            for needed_var in consumers[step]:
                # Case A: Variable is provided in initial inputs -> No dependency needed
                if needed_var in inputs:
                    continue

                # Case B: Variable is produced by another step
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
        # Inject arguments from memory
        try:
            params = {
                k: memory[k] for k in sig.parameters
            }
        except KeyError as e:
            raise KeyError(f"Step '{step.name}' failed to run. Missing dependency: {e}")
        
        return step.fn(**params)

    def _store_result(self, step: Step, result: Any, memory: dict):
        expected_names = self._resolve_output_names(step)

        # Case 1: Step returns None
        if result is None:
            return

        # Case 2: User specified manual outputs (e.g. unpacking a tuple)
        if step.manual_outputs:
            if len(expected_names) == 1:
                # Assign single value to single manual name
                memory[expected_names[0]] = result
            else:
                # Unpack tuple/list
                if not isinstance(result, (list, tuple)):
                    raise TypeError(f"Step '{step.name}' expected to return iterable for unpacking, got {type(result)}")
                if len(result) != len(expected_names):
                    raise ValueError(f"Step '{step.name}' returned {len(result)} items, expected {len(expected_names)}")
                
                for name, val in zip(expected_names, result):
                    memory[name] = val
            return

        # Case 3: Dataclass (Automatic Unpacking)
        # Note: We check if the class IS a dataclass instance
        if is_dataclass(result):
            memory.update(asdict(result))
            return

        # Case 4: Default (Assign result to function name)
        # We know expected_names has exactly 1 item here based on _resolve_output_names logic
        memory[expected_names[0]] = result

    # ------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------
    def _resolve_output_names(self, step: Step) -> List[str]:
        """
        Determines variable names a step produces.
        Priority:
        1. Manual 'outputs' list passed to .add()
        2. If Return Annotation is a Dataclass -> Field names
        3. Function Name
        """
        # 1. Explicit Overrides
        if step.manual_outputs:
            return step.manual_outputs

        # 2. Dataclass Annotation
        sig = inspect.signature(step.fn)
        ann = sig.return_annotation
        
        if isinstance(ann, type) and is_dataclass(ann):
            return list(ann.__dataclass_fields__.keys())

        # 3. Fallback to function name
        return [step.name]
    