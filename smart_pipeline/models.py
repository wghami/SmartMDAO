from dataclasses import dataclass
from typing import Callable, Optional, List

@dataclass(eq=False)
class Step:
    """
    Represents a single unit of work in the pipeline.
    eq=False ensures hashing is done by object identity, not value.
    """
    fn: Callable
    manual_outputs: Optional[List[str]] = None

    @property
    def name(self) -> str:
        return self.fn.__name__