import logging
from typing import Any, Dict, List, Literal, Optional, Protocol, Union, get_args, get_origin, runtime_checkable

from .graph import map_producers
from .models import Step

# Initialize module-level logger
logger = logging.getLogger(__name__)


class TypeMismatchError(TypeError):
    """Raised when a value, or a declared type, doesn't satisfy an expected type annotation."""


@runtime_checkable
class TypeChecker(Protocol):
    """
    Pluggable strategy deciding what "type compatible" means.

    Implement this to customize how strict/lenient SmartMDAO's type
    enforcement is - e.g. to accept numpy scalars, or to allow `int`
    where a `float` is declared - without touching the core framework.
    """

    def check_value(self, value: Any, expected: type) -> bool:
        """Does `value` satisfy the `expected` annotation?"""
        ...

    def check_types(self, produced: type, expected: type) -> bool:
        """Is everything satisfying `produced` guaranteed to also satisfy `expected`?"""
        ...


def _concrete_classes(annotation: Any) -> tuple:
    """
    Reduces a (possibly Optional/Union) type annotation down to a flat
    tuple of concrete classes suitable for isinstance/issubclass checks.

    Returns () for annotations we can't resolve to concrete classes
    (e.g. `typing.Any`, a bare `TypeVar`) - callers should treat that as
    "no constraint" rather than a failure.
    """
    if annotation is Any:
        return ()

    origin = get_origin(annotation)
    if origin is Literal:
        # Literal["a", 1] is, class-wise, str or int; the values themselves
        # are checked by StandardTypeChecker, which knows about Literal.
        return tuple(dict.fromkeys(type(value) for value in get_args(annotation)))
    if origin is Union:
        classes = []
        for arg in get_args(annotation):
            classes.extend(_concrete_classes(arg))
        return tuple(classes)

    # Generic containers (e.g. list[float], dict[str, int]) are checked
    # structurally on their origin only - contents are not inspected.
    if origin is not None:
        return (origin,)

    if isinstance(annotation, type):
        return (annotation,)

    return ()


def _format_type(tp: Any) -> str:
    """`float`, or `Literal['steiner', 'shortest']` - a composite type is shown whole."""
    if get_origin(tp) is not None:
        return str(tp).replace("typing.", "")
    return getattr(tp, "__name__", str(tp))


class StandardTypeChecker:
    """
    Strict, dependency-free type checker built on `isinstance`/`issubclass`.

    Understands `Optional[X]`, `Union[X, Y]`, `Literal[...]` (a value must be
    one of the options), generic containers (checked structurally on their
    origin), and `typing.Any` (always satisfied). Does not consider `int`
    compatible with `float` - if you need that, write a `TypeChecker` that
    loosens `_concrete_classes` accordingly.

    Anything `isinstance` cannot test - a `Protocol` not marked
    `@runtime_checkable`, for instance - is *unchecked*, never an error
    (invariant 2). Until 1.28.0 a `Literal` or such a `Protocol` made every
    call raise `TypeError`, valid values included.
    """

    def check_value(self, value: Any, expected: type) -> bool:
        origin = get_origin(expected)
        if origin is Union:
            return any(self.check_value(value, arg) for arg in get_args(expected))
        if origin is Literal:
            # type() too: Literal[1] does not accept True, though 1 == True.
            return any(value == option and type(value) is type(option) for option in get_args(expected))
        classes = _concrete_classes(expected)
        return not classes or _safely(isinstance, value, classes)

    def check_types(self, produced: type, expected: type) -> bool:
        if not _concrete_classes(expected) or not _concrete_classes(produced):
            return True
        if get_origin(produced) is Union:
            return all(self.check_types(arg, expected) for arg in get_args(produced))
        if get_origin(produced) is Literal:
            return all(self.check_value(option, expected) for option in get_args(produced))
        if get_origin(expected) is Union:
            return any(self.check_types(produced, arg) for arg in get_args(expected))
        if get_origin(expected) is Literal:
            # A plain type cannot promise to produce only the allowed options.
            return False
        expected_classes = _concrete_classes(expected)
        return all(_safely(issubclass, p, expected_classes) for p in _concrete_classes(produced))


def _safely(test, subject, classes) -> bool:
    """`isinstance`/`issubclass`, with anything they cannot test counted as satisfied."""
    try:
        return test(subject, classes)
    except TypeError:
        return True


def validate_structure(steps: List[Step], checker: Optional[TypeChecker] = None) -> None:
    """
    Statically validates every producer -> consumer edge in the pipeline:
    the type a step declares for an output must be compatible with the
    type every consumer of that variable declares for its input.

    This is pure structural analysis - no step is ever executed - so it
    only needs to run once per pipeline shape, regardless of how many
    times the pipeline is later evaluated (e.g. inside an optimization loop).
    """
    checker = checker or StandardTypeChecker()
    producers = map_producers(steps)

    for consumer in steps:
        for param_name, expected_type in consumer.resolve_input_types().items():
            producer = producers.get(param_name)
            if producer is None:
                continue  # External input; validated per-call in validate_external_inputs.

            produced_type = producer.resolve_output_types().get(param_name)
            if produced_type is None:
                continue  # Producer didn't declare a type for this output.

            if not checker.check_types(produced_type, expected_type):
                raise TypeMismatchError(
                    f"Type mismatch on '{param_name}': step '{producer.name}' declares "
                    f"{param_name} -> {_format_type(produced_type)}, but step "
                    f"'{consumer.name}' expects {param_name}: {_format_type(expected_type)}."
                )

    logger.debug(f"Structural type validation passed for {len(steps)} steps.")


def validate_external_inputs(
    steps: List[Step], inputs: Dict[str, Any], checker: Optional[TypeChecker] = None
) -> None:
    """
    Validates the concrete input values passed into `Pipeline.run(**inputs)`
    against the declared parameter types of the steps that consume them
    directly (i.e. variables that aren't produced internally by another step).
    """
    checker = checker or StandardTypeChecker()
    producers = map_producers(steps)

    for consumer in steps:
        for param_name, expected_type in consumer.resolve_input_types().items():
            if param_name in producers or param_name not in inputs:
                continue

            value = inputs[param_name]
            if not checker.check_value(value, expected_type):
                raise TypeMismatchError(
                    f"Input '{param_name}'={value!r} ({type(value).__name__}) does not match "
                    f"the type expected by step '{consumer.name}': {param_name}: {_format_type(expected_type)}."
                )
