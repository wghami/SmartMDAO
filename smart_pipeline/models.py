import inspect
from dataclasses import dataclass, is_dataclass
from typing import Callable, Optional, List, get_type_hints

@dataclass(eq=False)
class Step:
    """
    Represents a single node in the computation graph.
    eq=False ensures hashability is based on object identity.
    """
    fn: Callable
    manual_outputs: Optional[List[str]] = None

    @property
    def name(self) -> str:
        return self.fn.__name__

    def get_signature(self) -> inspect.Signature:
        """
        Robustly retrieves the signature of the underlying function,
        peeling off any decorators (like @cached) to find the real inputs.
        """
        original_fn = inspect.unwrap(self.fn)
        return inspect.signature(original_fn)

    def resolve_output_names(self) -> List[str]:
        """Determines variable names this step produces."""
        if self.manual_outputs:
            return self.manual_outputs

        # FIX: Use get_type_hints to correctly resolve string annotations 
        # (common with 'from __future__ import annotations' or forward refs)
        try:
            original_fn = inspect.unwrap(self.fn)
            hints = get_type_hints(original_fn)
            ann = hints.get('return')
        except Exception:
            # Fallback to standard inspection if get_type_hints fails 
            # (e.g., closures without global context)
            sig = self.get_signature()
            ann = sig.return_annotation
        
        # If the function returns a Dataclass, use field names
        if isinstance(ann, type) and is_dataclass(ann):
            return list(ann.__dataclass_fields__.keys())

        # Default: use function name
        return [self.name]