import inspect
from dataclasses import is_dataclass
from typing import List
from .models import Step

def resolve_output_names(step: Step) -> List[str]:
    """
    Determines the variable names a step produces.
    It checks manual_outputs first, then type hints, then defaults to function name.
    """
    if step.manual_outputs:
        return step.manual_outputs

    sig = inspect.signature(step.fn)
    ann = sig.return_annotation
    
    # If the return type is a Dataclass, use its field names
    if isinstance(ann, type) and is_dataclass(ann):
        return list(ann.__dataclass_fields__.keys())

    # Default to the function name
    return [step.name]