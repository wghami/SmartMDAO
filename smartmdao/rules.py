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
import threading
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Dict, FrozenSet, List, Optional, Sequence, Tuple, Union

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from .models import Step

logger = logging.getLogger(__name__)

#: A constant an ASP program can carry unquoted: lowercase, then word characters.
_BARE_CONSTANT = re.compile(r"^[a-z][A-Za-z0-9_]*$")


class RuleProgramError(ValueError):
    """A program, or a fact, that cannot be solved as written."""


class RuleBudgetExceeded(RuleProgramError):
    """
    Solving ran past its wall clock.

    Deliberately **not** `INFEASIBLE`. UNSAT is a proof that no model satisfies
    the rules; a timeout is the absence of an answer, and treating the two the
    same would turn "we gave up" into "your architecture is impossible" - a
    silent wrong answer of exactly the kind this library exists to prevent.
    """


@dataclass(frozen=True)
class RuleCost:
    """
    What solving this discipline has actually cost so far.

    Measured rather than estimated, for the reason Phase 3 gives: an engineer
    asked to consent to a run deserves a number, and the cheapest way to get one
    is to have already paid it once.
    """
    calls: int = 0
    cache_hits: int = 0
    grounds: int = 0
    ground_seconds: float = 0.0
    solve_seconds: float = 0.0

    @property
    def total_seconds(self) -> float:
        return self.ground_seconds + self.solve_seconds

    def projected_seconds(self, sweeps: int) -> float:
        """
        What `sweeps` more calls would cost if none of them hit the cache.

        The pessimistic reading on purpose. Inside a converging loop the facts
        repeat and most calls *are* hits, so the real figure is usually far
        lower - but quoting the optimistic number is how someone gets committed
        to a run that does not end.
        """
        if not self.grounds:
            return 0.0
        unit = self.total_seconds / self.grounds
        return unit * sweeps

    def __str__(self) -> str:
        return (
            f"{self.calls} call(s), {self.cache_hits} from cache, "
            f"{self.grounds} ground+solve in {self.total_seconds:.3f}s "
            f"({self.ground_seconds:.3f}s grounding)"
        )


@dataclass(frozen=True)
class Conflict:
    """
    Why a set of facts has no answer — as a **minimal** set of facts, or as a
    statement that the rules contradict themselves.

    `facts` is minimal in the strong sense: remove any one of them and the rules
    become satisfiable. That is what makes it usable in a design review, where
    "these two requirements cannot both hold" is an argument and "something is
    wrong somewhere" is not.
    """
    facts: Dict[str, "Fact"]
    rules_alone: bool
    solves: int

    def __str__(self) -> str:
        if self.rules_alone:
            return (
                "the rules are unsatisfiable on their own: no facts are "
                "involved, so no input could have made this work"
            )
        listed = ", ".join(f"{name}({value})" for name, value in self.facts.items())
        return (
            f"these facts cannot hold together: {listed}. Removing any one of "
            f"them makes the rules satisfiable"
        )


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


#: `#minimize`/`#maximize` bodies. Non-greedy: one match per statement.
_OPTIMISATION = re.compile(r"#(minimize|maximize)\s*\{(.*?)\}", re.S)

#: A choice rule head - `1 { p(X) : q(X) } 1`, or a bare `{ p(X) }`.
_CHOICE = re.compile(r"(?<![#\w])\{[^{}]*\}")

#: An ASP variable: uppercase or underscore, then word characters.
_VARIABLE = re.compile(r"^[A-Z_]\w*$")


def _strip_comments(source: str) -> str:
    """
    Remove `%` comments before any syntactic check.

    Not optional: this repository's own example program *documents* the broken
    tie-break form in a comment, so a checker that reads comments would report
    the file as unpinned for explaining the mistake it avoids.
    """
    return "\n".join(line.split("%", 1)[0] for line in source.splitlines())


def _weight_terms(body: str) -> List[str]:
    """
    The weight of every element of an optimisation statement.

    `3@1,M : spar(M), cost(M,3)` -> `"3"`. The priority is dropped: what decides
    whether a tie-break can separate anything is the weight, and carrying a
    level nothing reads would be a field to keep correct for no purpose.
    """
    weights = []
    for element in body.split(";"):
        head = element.split(":", 1)[0].strip()
        if not head:
            continue
        weight = head.split(",", 1)[0].strip()
        weights.append(weight.split("@", 1)[0].strip())
    return weights


def pinning_concern(source: str) -> Optional[str]:
    """
    Why a program might not pin its own answer, or None if it looks pinned.

    **A syntactic heuristic, and it says so.** It cannot prove a tie-break is
    total - two candidates may still rank equal - so the runtime
    `AmbiguousProgramError` remains the actual proof. What it *can* catch is the
    two shapes that produce ambiguity by construction, both of which look
    correct on the page:

    1. A program that generates candidates and never says which is preferred.
    2. An optimisation statement whose tie-break weight is a **constant**. That
       form separates nothing, because the same number is contributed by every
       candidate - documented in docs/design/004, where it was written by
       accident and caught only by running it.

    A program with no choice rule is not judged at all: plain rules derive one
    answer set by construction, so there is nothing to pin, and flagging them
    would fire on every correct deterministic program.
    """
    clean = _strip_comments(source)

    if not _CHOICE.search(clean):
        return None

    statements = _OPTIMISATION.findall(clean)
    if not statements:
        return (
            "it generates candidates with a choice rule but states no "
            "#minimize or #maximize, so when more than one model satisfies the "
            "rules, which one you get is decided by clingo's search order"
        )

    for _, body in statements:
        for weight in _weight_terms(body):
            if _VARIABLE.match(weight):
                return None

    return (
        "every optimisation weight in it is a constant, so nothing distinguishes "
        "two candidates that are otherwise equally good. A tie-break has to rank "
        "over a DISTINCT value per candidate - `#minimize { 1@0,M : spar(M) }` "
        "looks like one and separates nothing"
    )


def _relay(code, message):
    """
    Send clingo's own diagnostics to logging instead of stderr.

    Left on stderr they are printed straight through a pipeline run - and
    "atom does not occur in any rule head" fires for every injected fact, since
    a fact is by definition not derived by a rule. Correct programs would look
    alarming, once per sweep.
    """
    logger.debug(f"clingo [{code}]: {message}")


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
        budget_seconds: Optional[float] = 10.0,
        memoise: bool = True,
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

        if budget_seconds is not None and budget_seconds <= 0:
            raise RuleProgramError(
                f"budget_seconds must be positive, got {budget_seconds!r}. Pass "
                f"None to solve without a wall clock."
            )
        self.budget_seconds = budget_seconds
        self.memoise = memoise
        self._answers: Dict[Tuple, Answer] = {}
        self.cost = RuleCost()

    @property
    def pinning_concern(self) -> Optional[str]:
        """Why this program might not pin its own answer, or None. Static."""
        return pinning_concern(self.source)

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

        # Convert before keying. Doing it the other way round meant an
        # unrepresentable fact - a list, say - blew up as an unhashable dict key
        # instead of the message explaining that facts must be symbols.
        terms = [(name, _as_term(clingo, name, facts[name])) for name in self.facts]
        key = tuple(str(term) for _, term in terms)

        if self.memoise and key in self._answers:
            # Exact, not approximate. The program is fixed and clingo is
            # deterministic, so the same facts cannot produce a different answer
            # - which is the property this whole direction was chosen for.
            # Inside a converging loop most calls land here, which is what makes
            # re-grounding every sweep survivable.
            self.cost = replace(
                self.cost, calls=self.cost.calls + 1,
                cache_hits=self.cost.cache_hits + 1,
            )
            return self._answers[key]

        injected = "\n".join(f"{name}({term})." for name, term in terms)
        logger.debug(f"Grounding '{self.path}' with: {injected!r}")

        control = clingo.Control(["--opt-mode=optN", "--models=0"], logger=_relay)
        control.add("base", [], f"{self.source}\n{injected}\n")

        started = time.perf_counter()
        control.ground([("base", [])])
        grounded = time.perf_counter()

        optimal: List[FrozenSet[str]] = []
        with control.solve(yield_=True, async_=True) as handle:
            watchdog = self._start_watchdog(handle)
            try:
                handle.resume()
                for model in handle:
                    if model.optimality_proven:
                        optimal.append(
                            frozenset(
                                str(symbol) for symbol in model.symbols(shown=True)
                            )
                        )
                result = handle.get()
            finally:
                if watchdog is not None:
                    watchdog()

        solved = time.perf_counter()
        self.cost = replace(
            self.cost,
            calls=self.cost.calls + 1,
            grounds=self.cost.grounds + 1,
            ground_seconds=self.cost.ground_seconds + (grounded - started),
            solve_seconds=self.cost.solve_seconds + (solved - grounded),
        )

        if result.interrupted:
            raise RuleBudgetExceeded(
                f"Solving '{self.path}' passed its {self.budget_seconds}s budget "
                f"and was stopped after {solved - started:.2f}s "
                f"({grounded - started:.2f}s of that grounding). This is NOT the "
                f"same as UNSAT - no conclusion was reached, so nothing has been "
                f"proved about whether an answer exists. Raise budget_seconds, "
                f"or simplify the program."
            )

        satisfiable = result.satisfiable

        if not satisfiable:
            logger.info(f"'{self.path}' is UNSAT for these facts: INFEASIBLE.")
            # Cached like any other answer. UNSAT is a *proof*, so it is exactly
            # as reusable as a model - and an infeasible fact set is the one a
            # loop is most likely to revisit, so returning early here meant
            # re-grounding every sweep in precisely the worst case.
            return self._remember(key, INFEASIBLE)

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

        return self._remember(key, unique[0])

    def explain_infeasible(self, **facts: Fact) -> Conflict:
        """
        The smallest set of facts that cannot hold together, for facts that have
        no answer.

        **On demand, not automatic.** It costs one solve per fact, so computing
        it inside a convergence loop would charge every sweep for an explanation
        nobody read. `solve` returns `INFEASIBLE` cheaply; ask for the reason
        when you want it.

        **Deletion, not `SolveHandle.core()`.** docs/design/004 recorded the
        core mapping as a risk, and it was right for a sharper reason than the
        one it gave: clingo's core is expressed in solver literals *and is not
        minimal*. Measured on a three-fact conflict, it returned all three
        facts, including one that had no bearing on the contradiction - an
        explanation that points at an innocent constraint is worse than none,
        because it gets acted on. Dropping one fact at a time and asking whether
        it is still unsatisfiable costs more solves and yields a set that is
        minimal by construction, with no dependence on clingo's internals.
        """
        clingo = _load_clingo()

        missing = [name for name in self.facts if name not in facts]
        if missing:
            raise RuleProgramError(f"'{self.path}' is missing fact(s) {missing}.")

        present = {name: facts[name] for name in self.facts}
        solves = 1
        if self._satisfiable(clingo, present):
            raise RuleProgramError(
                f"'{self.path}' is satisfiable for these facts, so there is no "
                f"conflict to explain. Call solve() for the answer."
            )

        remaining = dict(present)
        for name in list(present):
            trial = {key: value for key, value in remaining.items() if key != name}
            solves += 1
            if not self._satisfiable(clingo, trial):
                # Still impossible without it, so it was never part of the
                # reason. Dropping it permanently is what makes the result
                # minimal rather than merely sufficient.
                del remaining[name]

        return Conflict(facts=remaining, rules_alone=not remaining, solves=solves)

    def _satisfiable(self, clingo, facts: Dict[str, Fact]) -> bool:
        """Whether the program admits any model given exactly these facts."""
        injected = "\n".join(
            f"{name}({_as_term(clingo, name, value)})."
            for name, value in facts.items()
        )

        control = clingo.Control(logger=_relay)
        control.add("base", [], f"{self.source}\n{injected}\n")

        started = time.perf_counter()
        control.ground([("base", [])])
        grounded = time.perf_counter()

        with control.solve(yield_=True, async_=True) as handle:
            watchdog = self._start_watchdog(handle)
            try:
                handle.resume()
                handle.wait()
                result = handle.get()
            finally:
                if watchdog is not None:
                    watchdog()

        finished = time.perf_counter()
        self.cost = replace(
            self.cost,
            grounds=self.cost.grounds + 1,
            ground_seconds=self.cost.ground_seconds + (grounded - started),
            solve_seconds=self.cost.solve_seconds + (finished - grounded),
        )

        if result.interrupted:
            raise RuleBudgetExceeded(
                f"Explaining '{self.path}' passed its {self.budget_seconds}s "
                f"budget. An explanation costs one solve per fact, so it is "
                f"more expensive than the answer it explains."
            )

        return bool(result.satisfiable)

    def _remember(self, key: Tuple, answer: Answer) -> Answer:
        """Store an answer against its facts, if memoisation is on."""
        if self.memoise:
            self._answers[key] = answer
        return answer

    def _start_watchdog(self, handle):
        """
        Returns a callable that retires the watchdog, or None if there is no
        budget. Call it when the handle is finished with.

        **This bounds solving, not grounding.** Verified against clingo 5.8.2:
        `Control.interrupt()` called during `ground()` is ignored and grounding
        runs to completion, while a solve handle cancels promptly and reports
        `interrupted`. Since grounding is the worst-case-exponential half
        (docs/design/003, risk 4), the honest statement is that this budget
        catches a hard *search*, not a grounding blow-up. A hard kill for the
        latter needs a separate process - which `run_pipeline` already provides
        for a whole pipeline, at a cost per call that would be absurd per sweep.

        Saying this plainly rather than letting "budget" imply protection it
        does not give: Phase 3 records the same mistake being made about the
        subprocess, and how readily the claim creeps back.
        """
        if self.budget_seconds is None:
            return None

        done = threading.Event()
        # Serialises cancel() against teardown. Without it the watchdog can call
        # into a handle the main thread is already disposing - a narrow race,
        # but one that reaches a C extension, and the symptom would be an
        # occasional hard failure with no Python traceback to read. Suspected
        # rather than proven: CI failed once on a script that passed eight times
        # locally, and this is the only concurrency in the path.
        lock = threading.Lock()
        live = [True]

        def watch():
            if done.wait(self.budget_seconds):
                return
            with lock:
                # `live` is False once the caller has finished with the handle.
                # Cancelling then would reach into a handle being disposed.
                if live[0]:
                    logger.warning(
                        f"'{self.path}' passed its {self.budget_seconds}s budget; "
                        f"cancelling the solve."
                    )
                    handle.cancel()

        threading.Thread(target=watch, daemon=True).start()

        def retire():
            with lock:
                live[0] = False
            done.set()

        return retire

    def _all_models(self, clingo, injected: str) -> List[FrozenSet[str]]:
        """
        Re-solve collecting every model, capped at two.

        Only reached for a program with no optimisation statement, where nothing
        is ever *proven* optimal. Two is enough: the question being asked is
        whether the answer is unique, not how many there are.
        """
        control = clingo.Control(["--models=2"], logger=_relay)
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
        # A marker rather than a name match, for the same reason `Bands`
        # carries one: `name=` is the engineer's to choose, so analysis must not
        # depend on it. Carries the discipline itself, so a static check can ask
        # it about the program without `analysis` needing to learn ASP syntax.
        apply_rules.rule_discipline = self

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
