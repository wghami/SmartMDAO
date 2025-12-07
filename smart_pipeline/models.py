import inspect
from dataclasses import dataclass, is_dataclass
from typing import Callable, Optional, List

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

    def resolve_output_names(self) -> List[str]:
        """Determines variable names this step produces."""
        if self.manual_outputs:
            return self.manual_outputs

        sig = inspect.signature(self.fn)
        ann = sig.return_annotation
        
        # If the function returns a Dataclass, use field names
        if isinstance(ann, type) and is_dataclass(ann):
            return list(ann.__dataclass_fields__.keys())

        # Default: use function name
        return [self.name]