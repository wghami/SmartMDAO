import inspect
import logging
from dataclasses import is_dataclass, asdict
from typing import Dict, Any, Optional
from .models import Step
from .validation import TypeChecker, TypeMismatchError

# Initialize module-level logger
logger = logging.getLogger(__name__)

class StepExecutor:
    """
    Static helper responsible for binding arguments from memory
    and updating memory with results.
    """
    @staticmethod
    def run_step(step: Step, memory: Dict[str, Any], type_checker: Optional[TypeChecker] = None):
        logger.debug(f"Preparing to execute step '{step.name}'")

        # Use the robust unwrapped signature to find expected parameters
        sig = step.get_signature()

        # 1. Bind Arguments
        params = {}
        missing_required = []

        for name, param in sig.parameters.items():
            if name in memory:
                params[name] = memory[name]
            elif param.default == inspect.Parameter.empty:
                missing_required.append(name)

        if missing_required:
            error_msg = (f"Step '{step.name}' cannot run. Missing inputs: {missing_required}. "
                         f"Available in memory: {list(memory.keys())}")
            logger.error(error_msg)
            raise KeyError(error_msg)

        # 1b. Optional Runtime Input Type Check
        if type_checker is not None:
            StepExecutor._check_input_types(step, params, type_checker)

        # 2. Execute
        try:
            logger.debug(f"Invoking '{step.name}' with inputs: {list(params.keys())}")
            result = step.fn(**params)
        except Exception as e:
            logger.error(f"Error executing step '{step.name}': {e}", exc_info=True)
            raise RuntimeError(f"Error executing step '{step.name}': {e}") from e

        # 3. Store Result
        StepExecutor._update_memory(step, result, memory)

        # 3b. Optional Runtime Output Type Check
        if type_checker is not None and result is not None:
            StepExecutor._check_output_types(step, memory, type_checker)

        logger.debug(f"Finished step '{step.name}'.")

    @staticmethod
    def _check_input_types(step: Step, params: Dict[str, Any], type_checker: TypeChecker):
        expected_types = step.resolve_input_types()
        for name, value in params.items():
            expected = expected_types.get(name)
            if expected is None:
                continue
            if not type_checker.check_value(value, expected):
                raise TypeMismatchError(
                    f"Step '{step.name}' received {name}={value!r} ({type(value).__name__}), "
                    f"expected {name}: {getattr(expected, '__name__', expected)}."
                )

    @staticmethod
    def _check_output_types(step: Step, memory: Dict[str, Any], type_checker: TypeChecker):
        # `resolve_output_types()` keys are always a subset of `resolve_output_names()`,
        # and `_update_memory` guarantees every one of those names lands in `memory`
        # whenever it returns without raising - so a plain lookup is safe here.
        expected_types = step.resolve_output_types()
        for name, expected in expected_types.items():
            value = memory[name]
            if not type_checker.check_value(value, expected):
                raise TypeMismatchError(
                    f"Step '{step.name}' produced {name}={value!r} ({type(value).__name__}), "
                    f"expected {name}: {getattr(expected, '__name__', expected)}."
                )

    @staticmethod
    def _update_memory(step: Step, result: Any, memory: Dict[str, Any]):
        output_keys = step.resolve_output_names()

        if result is None:
             logger.debug(f"Step '{step.name}' returned None. No outputs stored.")
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
                        logger.error(f"Step '{step.name}' missing output key '{k}' in returned dict.")
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