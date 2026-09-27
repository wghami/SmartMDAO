"""
What happens to input values at the JSON boundary. See docs/design/009.

A tool call arrives as JSON, and JSON cannot tell 2.0 from 2: many clients send
a whole-number float as an integer. SmartMDAO's type rule is strict - an `int`
does not satisfy `float` - so `run_pipeline(inputs={"altitude_km": 550})`
was refused although the caller meant 550.0 and the same value works from
Python (F-011).

This module repairs exactly that, and nothing else. It is applied only to the
values a tool call supplies, never to Python callers, and every value it
changes is reported. Structures JSON cannot carry - tuples, arrays, float
keys - are not guessed at here; `inputs_from` builds them in the project's own
process instead.
"""
import importlib
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..discretisation import effective_steps

#: Above this an integer may not be exactly representable as a float, and a
#: "repair" that rounds would change the value the caller sent.
EXACT_FLOAT_LIMIT = 2 ** 53


def whole_number_floats(
    pipelines: Iterable[Any], values: Dict[str, Any]
) -> Tuple[Dict[str, Any], List[str], Optional[str]]:
    """
    `values` with whole numbers passed as floats where every consumer wants one.

    A value is converted only when it is an `int` (never a `bool`), and every
    step reading it - in every pipeline given - declares a type that accepts
    a float and refuses an int. A step that declares nothing might be using
    the value as an index, so its presence leaves the value alone.

    Returns `(values, coerced names, refusal)`. The refusal is a message when
    a value would need converting but is too large to convert exactly.
    """
    pipelines = list(pipelines)
    result = dict(values)
    coerced: List[str] = []

    for name, value in values.items():
        if type(value) is not int:
            continue

        wants_float = True
        consumers = 0
        for pipeline in pipelines:
            checker = pipeline.type_checker
            for step in effective_steps(pipeline):
                if name not in step.get_signature().parameters:
                    continue
                consumers += 1
                declared = step.resolve_input_types().get(name)
                if declared is None or checker.check_value(value, declared) \
                        or not checker.check_value(float(value), declared):
                    wants_float = False

        if not consumers or not wants_float:
            continue
        if abs(value) > EXACT_FLOAT_LIMIT:
            return values, [], (
                f"Input '{name}'={value} is an integer where every consumer declares a float, "
                f"and it is too large to become a float exactly. Pass it as a float "
                f"yourself if rounding is acceptable."
            )
        result[name] = float(value)
        coerced.append(name)

    return result, sorted(coerced), None


class InputsFromError(Exception):
    """`inputs_from` could not produce inputs; the message says why."""


def build_inputs(
    spec: str,
    args: Optional[Sequence[Any]],
    kwargs: Optional[Dict[str, Any]],
    pipeline_file: Path,
) -> Dict[str, Any]:
    """
    Calls the project's own `module:function` and returns the inputs it builds.

    Run inside the run process - in the project's environment, under the run's
    wall clock - so arrays, tuples and float-keyed dicts go straight into the
    pipeline without ever becoming JSON (F-011, F-016, F-017). Only the spec
    and its arguments cross the boundary. The module is looked up the way the
    entry file's own imports are, so a module beside it or in its package is
    found.

    Never called by analyze / validate / explain / render: they do not execute
    project code (invariant 1).
    """
    from .loader import search_directory

    module_name, separator, attribute = spec.partition(":")
    if not separator or not module_name or not attribute:
        raise InputsFromError(
            f"inputs_from must look like 'package.module:function', got {spec!r}."
        )

    search = str(search_directory(Path(pipeline_file).resolve()))
    added = search not in sys.path
    if added:
        sys.path.insert(0, search)
    try:
        try:
            target = importlib.import_module(module_name)
            for part in attribute.split("."):
                target = getattr(target, part)
        except (ImportError, AttributeError) as error:
            raise InputsFromError(f"inputs_from {spec!r} could not be found: {error}") from error

        try:
            built = target(*(args or ()), **(kwargs or {}))
        except Exception as error:
            raise InputsFromError(
                f"inputs_from {spec!r} raised {type(error).__name__}: {error}"
            ) from error
    finally:
        if added:
            sys.path.remove(search)

    if not isinstance(built, Mapping) or not all(isinstance(key, str) for key in built):
        raise InputsFromError(
            f"inputs_from {spec!r} returned {type(built).__name__}; it must return a mapping "
            f"from input names to values."
        )
    return dict(built)
