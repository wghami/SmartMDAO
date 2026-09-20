"""
The bridge between a continuous variable and a symbolic fact.

Going from `mass_kg = 880.0` to `mass_band = "heavy"` requires a threshold, and
that threshold is a **hypothesis**: it silently decides the answer, and in most
code it lives in a helper function where nobody reviews it. A perfectly reviewed
set of rules sitting on top of an unreviewed threshold is not traceable - which
is the sharpest risk recorded in docs/design/003.

So the mapping is not glue code. It is a declared object:

    Discretisation(
        mass_band=Bands("mass_kg", edges=[800], names=["light", "heavy"]),
    )

Declared this way, the threshold is visible in the pipeline definition, it
diffs, and `validate()` can reason about it without running anything - which is
the whole reason the form is explicit edges rather than arbitrary predicates.
A predicate can express more and can be checked for nothing.

Boundaries are half-open and the side is explicit (`closed="left"` means
`[lower, upper)`, so an exact 800.0 is "heavy"). Whether the boundary value
belongs to the band above or below is exactly the kind of default that changes
an answer quietly, so it is a declared field rather than a convention.
"""
import inspect
import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Mapping, Sequence, Tuple, Union

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from .models import Step

logger = logging.getLogger(__name__)


def _one_parameter_signature(name: str) -> inspect.Signature:
    """A signature declaring exactly one required parameter, unannotated."""
    return inspect.Signature(
        [inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    )

Number = Union[int, float]

#: Which side of an edge the boundary value itself falls on.
LEFT = "left"
RIGHT = "right"


class DiscretisationError(ValueError):
    """
    A band declaration that cannot classify anything at all.

    Raised at construction, deliberately, and only for incoherence rather than
    for a questionable hypothesis. Two names for three intervals is a typo with
    no defensible reading; a threshold in the wrong place is a judgement call,
    and judgement calls are reported by `validate()` instead of raised here.
    """


@dataclass(frozen=True)
class Bands:
    """
    One continuous variable cut into named, contiguous intervals.

    :param variable: the pipeline variable being discretised.
    :param edges: strictly ascending cut points.
    :param names: one more name than there are edges, low to high.
    :param closed: which band an exact edge value belongs to. ``"left"`` (the
        default) makes each band ``[lower, upper)``, so a value exactly on an
        edge falls into the band *above* it.
    """
    variable: str
    edges: Tuple[Number, ...]
    names: Tuple[str, ...]
    closed: str = LEFT

    def __init__(
        self,
        variable: str,
        edges: Sequence[Number],
        names: Sequence[str],
        closed: str = LEFT,
    ):
        edges = tuple(edges)
        names = tuple(names)

        if closed not in (LEFT, RIGHT):
            raise DiscretisationError(
                f"closed must be '{LEFT}' or '{RIGHT}', got {closed!r}."
            )

        if not edges:
            raise DiscretisationError(
                f"Bands for '{variable}' declares no edges, so it describes one "
                f"band covering everything and classifies nothing."
            )

        if len(names) != len(edges) + 1:
            raise DiscretisationError(
                f"Bands for '{variable}' needs one more name than edges: "
                f"{len(edges)} edge(s) cut the line into {len(edges) + 1} "
                f"intervals, but {len(names)} name(s) were given."
            )

        if len(set(names)) != len(names):
            raise DiscretisationError(
                f"Bands for '{variable}' repeats a band name: {list(names)}. "
                f"Two intervals sharing a name cannot be told apart downstream."
            )

        for edge in edges:
            if isinstance(edge, bool) or not isinstance(edge, (int, float)):
                raise DiscretisationError(
                    f"Bands for '{variable}' has a non-numeric edge {edge!r}."
                )
            if math.isnan(edge):
                raise DiscretisationError(
                    f"Bands for '{variable}' has a NaN edge, which no value can "
                    f"be compared against."
                )

        if any(lower >= upper for lower, upper in zip(edges, edges[1:])):
            raise DiscretisationError(
                f"Bands for '{variable}' has edges that do not strictly ascend: "
                f"{list(edges)}. Equal edges describe a band no value can reach, "
                f"and descending edges have no reading at all."
            )

        object.__setattr__(self, "variable", variable)
        object.__setattr__(self, "edges", edges)
        object.__setattr__(self, "names", names)
        object.__setattr__(self, "closed", closed)

    def classify(self, value: Number) -> str:
        """
        The band `value` falls in.

        Total by construction: the bands tile the whole real line, so there is
        no "no band" answer to represent. That matters downstream - a missing
        fact and a false fact are different failures, and only one of them is
        visible.
        """
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise DiscretisationError(
                f"Cannot classify {value!r} for '{self.variable}': bands are "
                f"numeric intervals."
            )
        if math.isnan(value):
            raise DiscretisationError(
                f"Cannot classify NaN for '{self.variable}'. A NaN compares "
                f"false against every edge, so it would silently take the "
                f"lowest band."
            )

        for index, edge in enumerate(self.edges):
            below = value < edge if self.closed == LEFT else value <= edge
            if below:
                return self.names[index]
        return self.names[-1]

    def as_step(self, produced: str) -> "Step":
        """
        The band expressed as an ordinary pipeline step.

        A band *is* a function from one variable to another, so making it a real
        node rather than a special case is what lets every existing check work
        on it unchanged: `missing-input` already reports a source nothing
        produces, `duplicate-output` already reports a name a step also
        declares, and the solver already knows how to order it. Teaching five
        checks about discretisation separately would be a second planner, and
        the second one drifts.

        The parameter is deliberately left unannotated. Annotating it `float`
        would make a producer declaring `int` fail the strict type checker for
        no reason, and "numeric enough to classify" is a narrower question than
        the type edges answer - `_check_discretisation` asks it directly.
        """
        from .models import Step

        def classify_band(**kwargs) -> str:
            return self.classify(kwargs[self.variable])

        classify_band.__name__ = f"discretise_{produced}"
        classify_band.__qualname__ = classify_band.__name__
        classify_band.__doc__ = f"Derived from {self.describe()}"
        classify_band.__signature__ = _one_parameter_signature(self.variable)
        classify_band.__annotations__ = {"return": str}

        return Step(classify_band, manual_outputs=[produced])

    def describe(self) -> str:
        """One line stating every interval, for `explain()` and for review."""
        open_lower, open_upper = ("[", ")") if self.closed == LEFT else ("(", "]")
        bounds = (None,) + self.edges + (None,)

        intervals = []
        for index, name in enumerate(self.names):
            lower, upper = bounds[index], bounds[index + 1]
            low = "(-inf" if lower is None else f"{open_lower}{lower}"
            high = "+inf)" if upper is None else f"{upper}{open_upper}"
            intervals.append(f"{name}={low}, {high}")

        return f"{self.variable} -> {'; '.join(intervals)}"


@dataclass(frozen=True)
class Discretisation:
    """
    Every band declaration attached to a pipeline, keyed by the symbolic
    variable each one produces.

    Pipeline-level rather than per-discipline so `validate()` sees the
    thresholds without reaching into anything, and so the same band can feed
    more than one consumer.
    """
    bands: Mapping[str, Bands] = field(default_factory=dict)

    def __init__(self, **bands: Bands):
        for name, band in bands.items():
            if not isinstance(band, Bands):
                raise DiscretisationError(
                    f"'{name}' must be a Bands instance, got "
                    f"{type(band).__name__}."
                )
        object.__setattr__(self, "bands", dict(bands))

    def __bool__(self) -> bool:
        return bool(self.bands)

    @property
    def sources(self) -> Tuple[str, ...]:
        """The continuous variables read, in declaration order."""
        return tuple(band.variable for band in self.bands.values())

    @property
    def produced(self) -> Tuple[str, ...]:
        """The symbolic variables produced, in declaration order."""
        return tuple(self.bands)

    def as_steps(self) -> List["Step"]:
        """One synthetic step per band, in declaration order."""
        return [band.as_step(name) for name, band in self.bands.items()]

    def apply(self, state: Mapping[str, Number]) -> Dict[str, str]:
        """
        Classify whatever `state` supplies, skipping bands whose source is
        absent.

        Skipping rather than raising is deliberate: an absent source is a
        *structural* problem, and `validate()` reports it up front with the
        variable named. Raising here would surface the same mistake from deep
        inside a solve, which is the failure mode this library keeps trying to
        move earlier.
        """
        facts = {}
        for name, band in self.bands.items():
            if band.variable in state:
                facts[name] = band.classify(state[band.variable])
        logger.debug(f"Discretisation produced {len(facts)} fact(s).")
        return facts


def effective_steps(pipeline) -> List["Step"]:
    """
    The steps a solve will actually run: those registered, plus one per band.

    Used by `Pipeline.run` and by every entry point in `analysis`, so that what
    the analysis describes and what the solver executes cannot diverge. This is
    the same reason `graph.build_execution_plan` is shared rather than
    reimplemented.
    """
    steps = list(pipeline.steps)
    discretisation = getattr(pipeline, "discretisation", None)
    if discretisation:
        steps.extend(discretisation.as_steps())
    return steps
