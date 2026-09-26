"""
Units, checked for consistency and never converted. See docs/design/008.

A unit is declared with a marker in the annotation:

    dB = Annotated[float, Unit("dB")]

    @pipeline.step(outputs=["gain"])
    def antenna(power: Annotated[float, Unit("W")]) -> dB: ...

`validate()` then reports a connection whose two ends declare different units.
Nothing else happens: values stay plain numbers, nothing is rescaled, and a
connection with either end undeclared is simply unchecked (invariant 2). Units
are read from annotations, never by calling a step (invariant 1).

Bare strings in `Annotated` are ignored on purpose. That space is shared with
pydantic, Typer and people's descriptions ("wing span"); reading any string as
a unit would be a guess.
"""
import inspect
from dataclasses import dataclass, is_dataclass
from typing import Annotated, Dict, Optional, Protocol, get_args, get_origin, get_type_hints, runtime_checkable

from .models import Step

#: Logarithmic units. A mismatch between one of these and anything else gets a
#: hint in its message: they differ by a logarithm, not by a factor. A hint in
#: a message, never a conversion.
#: Not "B": a bel in acoustics, but a byte in every data workflow.
LOGARITHMIC = frozenset({"dB", "dBm", "dBW", "dBi", "dBc", "dBV", "dBA", "dBFS", "Np"})


@dataclass(frozen=True)
class Unit:
    """Marks the unit of a value in an annotation: `Annotated[float, Unit("dB")]`."""
    symbol: str

    def __post_init__(self):
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise TypeError(f"a unit must be a non-empty string, got {self.symbol!r}")

    def __str__(self) -> str:
        return self.symbol


@runtime_checkable
class UnitChecker(Protocol):
    """
    Whether a value in `produced` units may be used where `expected` units are
    declared. Yes or no - there is deliberately no way to rescale a value here.
    """

    def consistent(self, produced: str, expected: str) -> bool:
        ...


class StandardUnitChecker:
    """
    The same unit, exactly, after trimming whitespace.

    `km` into `m` is reported, and so is `meter` into `m`: deciding that two
    spellings are the same unit is an equivalence SmartMDAO does not guess.
    `dB` and `dBm` look close and differ by a reference power. A project that
    wants more - aliases, or dimensional analysis from a unit library - supplies
    its own `UnitChecker`.
    """

    def consistent(self, produced: str, expected: str) -> bool:
        return produced.strip() == expected.strip()


def unit_of(annotation) -> Optional[str]:
    """The first `Unit` in an `Annotated` annotation, if there is one."""
    if get_origin(annotation) is not Annotated:
        return None
    for extra in get_args(annotation)[1:]:
        if isinstance(extra, Unit):
            return extra.symbol
    return None


def _hints(target) -> Dict[str, object]:
    try:
        return get_type_hints(target, include_extras=True)
    except Exception:
        # Unresolvable annotations are unchecked, never an error (invariant 2).
        return {}


def input_units(step: Step) -> Dict[str, str]:
    """Each parameter's declared unit."""
    hints = _hints(inspect.unwrap(step.fn))
    hints.pop("return", None)
    return {name: unit for name, hint in hints.items() if (unit := unit_of(hint))}


def output_units(step: Step) -> Dict[str, str]:
    """
    Each output's declared unit, from the same three shapes the type checker
    reads: a single return value, a dataclass's fields, a tuple's elements.
    """
    returned = _hints(inspect.unwrap(step.fn)).get("return")
    if returned is None:
        return {}
    names = step.resolve_output_names()

    bare = get_args(returned)[0] if get_origin(returned) is Annotated else returned
    if isinstance(bare, type) and is_dataclass(bare):
        fields = _hints(bare)
        return {name: unit for name in names if (unit := unit_of(fields.get(name)))}

    if step.manual_outputs and get_origin(bare) is tuple:
        elements = get_args(bare)
        if len(elements) == len(names):
            return {name: unit for name, element in zip(names, elements) if (unit := unit_of(element))}
        return {}

    if len(names) == 1 and (unit := unit_of(returned)):
        return {names[0]: unit}
    return {}


def mismatch_hint(produced: str, expected: str) -> str:
    """Extra words for a mismatch between a logarithmic unit and anything else."""
    if (produced in LOGARITHMIC) != (expected in LOGARITHMIC):
        return " One side is logarithmic: the two differ by a logarithm, not a factor."
    return ""
