import inspect
from dataclasses import is_dataclass, asdict
from typing import Dict, Any
from .models import Step

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

        if result is None:
             # Even if result is None, we might need to verify if outputs were expected?
             # For now, we assume None means no output to store unless manual_outputs dictated otherwise.
             return

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