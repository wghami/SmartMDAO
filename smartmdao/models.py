import inspect
from dataclasses import dataclass, is_dataclass
from typing import Callable, Dict, Optional, List, Union, get_args, get_origin, get_type_hints

#: What `effects` may say. See docs/design/005-side-effecting-steps.md.
EFFECT_VALUES = (None, False, True, "once", "every-sweep")

PURE = (None, False)


@dataclass(eq=False)
class Step:
    """
    Represents a single node in the computation graph.
    eq=False ensures hashability is based on object identity.

    `effects` declares that this step touches something outside the pipeline -
    writes a file, launches a subprocess, posts to an API. It matters because a
    step inside a cyclic block runs **once per sweep**, which is the point for a
    numeric discipline and something else entirely for one that sends a message.

    | Value | Meaning |
    |---|---|
    | absent | Undeclared; assumed pure, and nothing is reported. |
    | `True` | Touches the world. **What should happen in a loop is not stated.** |
    | `"once"` | Run on the first sweep of a `run()`; reuse the result after. |
    | `"every-sweep"` | Run every sweep. You meant it. |

    It is declared rather than inferred: a step returning `None` looks like a
    free signal and is not, because a step can write a file *and* return a
    float.
    """
    fn: Callable
    manual_outputs: Optional[List[str]] = None
    effects: Optional[Union[bool, str]] = None
    #: A label for steps that belong together - one part of a model, one stage
    #: of a workflow. It changes nothing about what is computed: the planner
    #: uses it only to choose between blocks that are independent anyway, so
    #: a group's steps run, and are drawn, next to each other.
    group: Optional[str] = None

    def __post_init__(self):
        if self.group is not None and (not isinstance(self.group, str) or not self.group):
            raise TypeError(f"group must be a non-empty string, got {self.group!r}")
        if self.effects not in EFFECT_VALUES:
            raise ValueError(
                f"effects must be one of {list(EFFECT_VALUES)}, got "
                f"{self.effects!r}. A typo here would silently declare a "
                f"destructive step pure, so it is refused rather than reported."
            )

    @property
    def name(self) -> str:
        return self.fn.__name__

    @property
    def has_effects(self) -> bool:
        """Whether this step declares that it touches the world."""
        return self.effects not in PURE

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

    def resolve_input_types(self) -> Dict[str, type]:
        """
        Maps each declared parameter name to its annotated type.
        Parameters without a type hint are omitted (nothing to validate against).
        """
        original_fn = inspect.unwrap(self.fn)
        try:
            hints = get_type_hints(original_fn)
        except Exception:
            # Unresolvable annotations (e.g. forward refs without global context):
            # skip type validation for this step rather than fail construction.
            return {}
        hints.pop('return', None)
        return hints

    def resolve_output_types(self) -> Dict[str, type]:
        """
        Maps each output variable name (see `resolve_output_names`) to its
        declared type, inferred from the function's return annotation:
          - Dataclass return -> per-field types.
          - Tuple return matched against manual `outputs=[...]` -> per-position types.
          - Single output -> the return annotation itself.
        Falls back to an empty mapping wherever a type can't be confidently inferred.
        """
        original_fn = inspect.unwrap(self.fn)
        try:
            hints = get_type_hints(original_fn)
        except Exception:
            return {}

        ann = hints.get('return')
        if ann is None:
            return {}

        output_names = self.resolve_output_names()

        if isinstance(ann, type) and is_dataclass(ann):
            field_hints = get_type_hints(ann)
            return {name: field_hints[name] for name in output_names if name in field_hints}

        if self.manual_outputs and get_origin(ann) is tuple:
            args = get_args(ann)
            if len(args) == len(output_names) and Ellipsis not in args:
                return dict(zip(output_names, args))
            return {}

        if len(output_names) == 1:
            return {output_names[0]: ann}

        return {}