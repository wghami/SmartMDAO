"""
A discipline whose behaviour is a reviewed set of rules rather than a function.

The direction is docs/design/003 and the naming is docs/design/004: a language
model writes an ASP program **once, at authoring time, under engineer review**,
and that program - not the model - becomes the discipline. At run time nothing
is generated: clingo derives an answer set from the program and the facts, which
is deterministic, inspectable and diffable in a way a prompt is not.

    rules = RuleDiscipline(
        program="wing_architecture.lp",
        facts=["mass_band", "certification"],
        produces="decisions",
    )
    pipeline.add(rules)

Three properties that make this fit the rest of the library:

* **The output is a frozenset of atoms**, so the existing non-numeric
  convergence couples it to a feedback loop with no solver change. 002's rule -
  couple on the decision, never on the explanation of it - holds automatically,
  because a set of atoms has no prose in it to perturb structural equality.

* **UNSAT is the honest INFEASIBLE.** Phase 1 had to invent a sentinel because a
  discipline must be total. Here "no feasible architecture exists" is a proof,
  not a convention - and it is an explicit value, never `None`, which would
  store nothing and read as converged.

* **Facts are symbols, not measurements.** ASP has no floats, so a continuous
  variable cannot be injected. It has to cross the bridge first, through a
  declared `Bands` (see discretisation.py). That is not a limitation worked
  around - it is the reason 4.1 was built before this file.

`clingo` is an optional extra (`pip install smartmdao[asp]`) and is imported
lazily, so a base install can import this module and only fails, helpfully, at
the point a program is actually solved.
"""
import logging
import pathlib
import re
from typing import TYPE_CHECKING, Any, Dict, FrozenSet, List, Optional, Sequence, Union

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from .models import Step

logger = logging.getLogger(__name__)

#: A constant an ASP program can carry unquoted: lowercase, then word characters.
_BARE_CONSTANT = re.compile(r"^[a-z][A-Za-z0-9_]*$")


class RuleProgramError(ValueError):
    """A program, or a fact, that cannot be solved as written."""


class AmbiguousProgramError(RuleProgramError):
    """
    More than one optimal answer set, so the program does not pin its own
    answer.

    Raised rather than resolved. docs/design/003 is explicit that silently
    taking the first model buys a false sense of rigour: the engineer would get
    one architecture with nothing anywhere indicating another was equally good.
    docs/design/004 records how easily this happens - a tie-break written the
    obvious way separates nothing at all.
    """

    def __init__(self, program: str, models: Sequence[FrozenSet[str]]):
        self.models = tuple(models)
        listed = "\n".join(f"  {index}. {sorted(m)}" for index, m in enumerate(self.models, 1))
        super().__init__(
            f"'{program}' has {len(self.models)} equally optimal answer sets, so "
            f"which one you get is decided by clingo's search order rather than "
            f"by the program:\n{listed}\n"
            f"Pin it with an optimisation statement plus a total tie-break that "
            f"ranks over a DISTINCT value per candidate - a constant weight "
            f"looks like a tie-break and separates nothing."
        )


class _Infeasible:
    """
    The value a rule-backed discipline returns when its rules are unsatisfiable.

    A singleton rather than `None`, because a step returning `None` stores
    nothing - leaving the previous value in place, which a convergence checker
    reads as *at rest*. A discipline must be total, and "no feasible answer" has
    to be a value like any other.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "INFEASIBLE"

    def __bool__(self) -> bool:
        return False


#: Returned when the rules admit no model at all. See `_Infeasible`.
INFEASIBLE = _Infeasible()

Fact = Union[str, int]
Answer = Union[FrozenSet[str], _Infeasible]


def _load_clingo():
    """Import clingo, or explain what to install."""
    try:
        import clingo
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise ImportError(
            "Rule-backed disciplines need clingo, which ships as an optional "
            "extra: pip install 'smartmdao[asp]'. Nothing else in SmartMDAO "
            "requires it."
        ) from error
    return clingo


def _as_term(clingo, name: str, value: Fact):
    """
    One pipeline value as an ASP term.

    Floats are refused rather than rounded. ASP has no floating point, so any
    conversion here would be an undeclared threshold - precisely the hypothesis
    `Bands` exists to make visible.
    """
    if isinstance(value, bool):
        raise RuleProgramError(
            f"Fact '{name}' is a bool. ASP has no booleans as terms; state the "
            f"condition as a symbol instead, e.g. {name}(yes) / {name}(no)."
        )
    if isinstance(value, int):
        return clingo.Number(value)
    if isinstance(value, float):
        raise RuleProgramError(
            f"Fact '{name}' is the float {value!r}. ASP has no floats, so "
            f"injecting one means choosing a threshold - declare a Bands for "
            f"'{name}' and pass the band instead. See docs/design/003."
        )
    if isinstance(value, str):
        if _BARE_CONSTANT.match(value):
            return clingo.Function(value, [])
        return clingo.String(value)

    raise RuleProgramError(
        f"Fact '{name}' is a {type(value).__name__}, which has no ASP term. "
        f"Facts must be symbols or integers."
    )


class RuleDiscipline:
    """
    An `.lp` file, wired into a pipeline as one step.

    :param program: path to the ASP program. Read at construction, so a missing
        or unreadable file fails immediately rather than inside a solve.
    :param facts: the pipeline variables injected as facts, one atom each -
        ``mass_band="heavy"`` becomes ``mass_band(heavy).``
    :param produces: the name of the variable this step outputs. Its value is a
        `frozenset` of the shown atoms, or `INFEASIBLE`.
    :param name: the step name. Defaults to ``rules_<program stem>``. Worth
        setting deliberately: inside a feedback loop, step names are sorted
        alphabetically and the first one decides which variable needs a seed.
    """

    def __init__(
        self,
        program: Union[str, pathlib.Path],
        facts: Sequence[str],
        produces: str,
        name: Optional[str] = None,
    ):
        self.path = pathlib.Path(program)
        if not self.path.is_file():
            raise RuleProgramError(
                f"No ASP program at '{self.path}'. The program is the artifact "
                f"an engineer reviews, so it has to be a real file on disk."
            )

        self.source = self.path.read_text()
        if not self.source.strip():
            raise RuleProgramError(f"'{self.path}' is empty.")

        self.facts = tuple(facts)
        if not self.facts:
            raise RuleProgramError(
                f"'{self.path}' is declared with no facts, so it would derive "
                f"the same answer set on every call and could not respond to "
                f"the pipeline at all."
            )

        duplicates = {f for f in self.facts if self.facts.count(f) > 1}
        if duplicates:
            raise RuleProgramError(
                f"'{self.path}' declares fact(s) {sorted(duplicates)} more than "
                f"once."
            )

        self.produces = produces
        if produces in self.facts:
            raise RuleProgramError(
                f"'{produces}' is both a fact and the output of '{self.path}', "
                f"which would make the step overwrite its own input."
            )

        self.name = name or f"rules_{self.path.stem}"

    # ------------------------------------------------------------------
    # Solving
    # ------------------------------------------------------------------

    def solve(self, **facts: Fact) -> Answer:
        """
        Derive the answer set for one set of facts.

        Every optimal model is enumerated rather than the first taken, because a
        program admitting more than one is a finding and not a detail.
        """
        clingo = _load_clingo()

        missing = [name for name in self.facts if name not in facts]
        if missing:
            raise RuleProgramError(
                f"'{self.path}' is missing fact(s) {missing}."
            )

        injected = "\n".join(
            f"{name}({_as_term(clingo, name, facts[name])})." for name in self.facts
        )
        logger.debug(f"Grounding '{self.path}' with: {injected!r}")

        control = clingo.Control(["--opt-mode=optN", "--models=0"])
        control.add("base", [], f"{self.source}\n{injected}\n")
        control.ground([("base", [])])

        optimal: List[FrozenSet[str]] = []
        with control.solve(yield_=True) as handle:
            for model in handle:
                if model.optimality_proven:
                    optimal.append(
                        frozenset(str(symbol) for symbol in model.symbols(shown=True))
                    )
            satisfiable = handle.get().satisfiable

        if not satisfiable:
            logger.info(f"'{self.path}' is UNSAT for these facts: INFEASIBLE.")
            return INFEASIBLE

        # An unoptimised program proves no model optimal; every model is then an
        # answer, and more than one is the same ambiguity by a different route.
        if not optimal:
            optimal = self._all_models(clingo, injected)

        # dict.fromkeys rather than a loop with a membership test: clingo can
        # re-report an equally optimal model, and a branch that only fires when
        # it happens to is a branch nobody can honestly test.
        unique = list(dict.fromkeys(optimal))

        if len(unique) > 1:
            raise AmbiguousProgramError(str(self.path), unique)

        return unique[0]

    def _all_models(self, clingo, injected: str) -> List[FrozenSet[str]]:
        """
        Re-solve collecting every model, capped at two.

        Only reached for a program with no optimisation statement, where nothing
        is ever *proven* optimal. Two is enough: the question being asked is
        whether the answer is unique, not how many there are.
        """
        control = clingo.Control(["--models=2"])
        control.add("base", [], f"{self.source}\n{injected}\n")
        control.ground([("base", [])])

        models = []
        with control.solve(yield_=True) as handle:
            for model in handle:
                models.append(
                    frozenset(str(symbol) for symbol in model.symbols(shown=True))
                )
        return models

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def as_step(self) -> "Step":
        """
        The discipline as an ordinary pipeline step.

        Same reasoning as `Bands.as_step`: a rule-backed discipline is a
        function from some variables to another, so making it a real node means
        every existing check, the planner and the solver handle it unchanged.
        """
        from .models import Step

        def apply_rules(**kwargs) -> frozenset:
            return self.solve(**kwargs)

        apply_rules.__name__ = self.name
        apply_rules.__qualname__ = self.name
        apply_rules.__doc__ = f"Answer set of {self.path}"
        apply_rules.__signature__ = _signature_for(self.facts)
        # Deliberately unannotated inputs: what a fact may be is narrower than
        # any annotation (symbols and integers, never floats), and `solve`
        # states that directly. The return is annotated because a consumer
        # genuinely receives a frozenset - or INFEASIBLE.
        apply_rules.__annotations__ = {}
        # A marker rather than a name match, for the same reason `Bands` carries
        # one: `name=` is the engineer's to choose, so analysis must not depend
        # on it. Carries the program path, which is what a finding wants to
        # quote - the file is the artifact under review.
        apply_rules.decides_from_rules = str(self.path)

        return Step(apply_rules, manual_outputs=[self.produces])


def _signature_for(names: Sequence[str]):
    """A signature of required, unannotated parameters, in declared order."""
    import inspect

    return inspect.Signature(
        [
            inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            for name in names
        ]
    )
