"""
Static analysis of a pipeline: what a solve *would* do, without doing it.

Every fact here is derived from step signatures, type annotations and the
dependency graph. No discipline function is ever invoked, which is what makes
this safe to expose to tooling (see docs/design/001-mcp-connector.md).

Three entry points:

    analyze(pipeline)   -> PipelineAnalysis   what will run, in what order
    validate(pipeline)  -> tuple[Finding]     what is wrong with it
    explain(pipeline)   -> str                the same thing, in prose

The planning itself is delegated to `graph.build_execution_plan`, the same
function `HybridSolver` uses to drive a real solve. Reimplementing it here
would let the analysis drift away from the behaviour it claims to describe.
"""
import ast
import inspect
import logging
import textwrap
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .graph import (
    ExecutionBlock,
    build_execution_plan,
    group_conflicts,
    map_producers,
    weakly_connected_components,
)
from .core import inputs_for
from .discretisation import effective_steps
from .effects import repeating_step_names
from .models import Step
from .solvers import (
    DAGSolver,
    HybridSolver,
    IterativeSolver,
    StandardConvergenceChecker,
)
from .validation import (
    StandardTypeChecker,
    TypeChecker,
    _format_type,
)

logger = logging.getLogger(__name__)

ERROR = "error"
WARNING = "warning"
INFO = "info"


@dataclass(frozen=True)
class Finding:
    """One problem, or potential problem, found without running anything."""
    code: str
    severity: str
    message: str
    step: Optional[str] = None
    variable: Optional[str] = None

    def __str__(self) -> str:
        location = ""
        if self.step:
            location = f" [{self.step}]"
        elif self.variable:
            location = f" [{self.variable}]"
        return f"{self.severity.upper()}: {self.message}{location}"


@dataclass(frozen=True)
class InitialGuess:
    """A variable that must be seeded before a cycle's first sweep."""
    variable: str
    consumed_by: str


@dataclass(frozen=True)
class CycleAnalysis:
    """A feedback loop the solver will have to converge."""
    steps: Tuple[str, ...]
    feedback_variables: Tuple[str, ...]


@dataclass(frozen=True)
class PipelineAnalysis:
    steps: Tuple[str, ...]
    execution_order: Tuple[str, ...]
    cycles: Tuple[CycleAnalysis, ...]
    external_inputs: Tuple[str, ...]
    terminal_outputs: Tuple[str, ...]
    initial_guesses_required: Tuple[InitialGuess, ...]
    recommended_solver: str
    reason: str
    #: Steps whose body only raises `NotImplementedError` - declared, not yet
    #: written. A step whose source cannot be read is never listed here.
    stubs: Tuple[str, ...] = ()

    @property
    def has_cycles(self) -> bool:
        return bool(self.cycles)


# ==============================================================================
# Shared derivation
# ==============================================================================

def _step_inputs(step: Step) -> Tuple[str, ...]:
    """Parameter names, seen through decorators like @cached."""
    return tuple(step.get_signature().parameters)


def _required_inputs(step: Step) -> Tuple[str, ...]:
    """Parameters with no default - the ones that must come from somewhere."""
    return tuple(
        name
        for name, parameter in step.get_signature().parameters.items()
        if parameter.default is inspect.Parameter.empty
    )


def _is_not_implemented(node) -> bool:
    """`NotImplementedError` or `NotImplementedError(...)`."""
    if isinstance(node, ast.Call):
        node = node.func
    return isinstance(node, ast.Name) and node.id == "NotImplementedError"


def stub_status(step: Step) -> Optional[bool]:
    """
    Whether `step` is a stub: its body, docstring aside, is one
    `raise NotImplementedError`.

    `True` or `False` when the source can be read; `None` when it cannot - a
    builtin, or a function made by `exec`. Unknown is never reported as a stub
    (invariant 2), because the one thing worse than not knowing how much of a
    pipeline is real is being told the wrong number.

    Read from the source with `ast`; the function is never called.
    """
    fn = inspect.unwrap(step.fn)
    if getattr(fn, "__name__", "") == "<lambda>":
        return False                        # a lambda cannot hold a raise statement
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError, SyntaxError):
        return None

    node = tree.body[0] if tree.body else None
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None

    body = node.body
    if (
        len(body) > 1
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]

    return (
        len(body) == 1
        and isinstance(body[0], ast.Raise)
        and _is_not_implemented(body[0].exc)
    )


def _guesses_for_order(
    ordered_steps: Sequence[Step], available: Set[str]
) -> Tuple[InitialGuess, ...]:
    """
    Which variables must be seeded before the first sweep, and which step
    reaches for each one first.

    Walks the steps in the order the *configured solver* will actually run
    them, tracking what has been produced so far. Anything a step needs that is
    neither already available nor produced earlier in the walk has to be
    supplied to `run()` as an initial guess.

    This is why the answer depends on the solver and on step names: HybridSolver
    runs cyclic blocks in alphabetical order, while IterativeSolver runs every
    step in registration order. Renaming a step, or swapping the solver, can
    change which variable you must seed. See docs/known-issues.md.
    """
    produced_so_far: Set[str] = set()
    needed: List[InitialGuess] = []
    seen: Set[str] = set()

    for step in ordered_steps:
        for name in _required_inputs(step):
            if name not in available and name not in produced_so_far and name not in seen:
                needed.append(InitialGuess(variable=name, consumed_by=step.name))
                seen.add(name)
        produced_so_far.update(step.resolve_output_names())

    return tuple(needed)


def _ordered_steps(pipeline, steps: List[Step], input_keys: Set[str]) -> List[Step]:
    """
    The order the configured solver will actually execute steps in.

    `IterativeSolver` sweeps every step in registration order (or its explicit
    `execution_order`); the graph plays no part. Everything else follows the
    dependency graph. Calling the solver's own method rather than guessing is
    what keeps this honest.
    """
    solver = pipeline.solver
    if isinstance(solver, IterativeSolver):
        return list(solver.determine_execution_order(steps))

    return [
        step
        for block in build_execution_plan(steps, input_keys)
        for step in block.steps
    ]


def _feedback_variables(block: ExecutionBlock) -> Tuple[str, ...]:
    """Variables produced inside a cyclic block and consumed inside it too."""
    produced = {
        name for step in block.steps for name in step.resolve_output_names()
    }
    consumed = {name for step in block.steps for name in _step_inputs(step)}
    return tuple(sorted(produced & consumed))


# ==============================================================================
# analyze
# ==============================================================================

def analyze(pipeline, inputs: Optional[Sequence[str]] = None) -> PipelineAnalysis:
    """
    Describes what running `pipeline` would do.

    `inputs` is the set of variable names that would be passed to `run()`.
    Supplying it sharpens the analysis - without it, every external input looks
    like a missing initial guess. When omitted, the pipeline's declared
    `inputs` are used; an explicit list, even an empty one, wins.
    """
    steps = effective_steps(pipeline)
    input_keys = set(inputs_for(pipeline, inputs))

    producers = map_producers(steps)

    all_consumed = {name for step in steps for name in _step_inputs(step)}
    all_produced = set(producers)

    external_inputs = tuple(sorted(all_consumed - all_produced))
    terminal_outputs = tuple(sorted(all_produced - all_consumed))

    # Cycles are a property of the dependency graph, independent of which
    # solver is configured - DAGSolver does not make a feedback loop go away,
    # it just refuses to run it.
    cycles = tuple(
        CycleAnalysis(
            steps=tuple(step.name for step in block.steps),
            feedback_variables=_feedback_variables(block),
        )
        for block in build_execution_plan(steps, input_keys)
        if block.is_cyclic
    )

    # Ordering and seeding, by contrast, depend entirely on the solver.
    ordered = _ordered_steps(pipeline, steps, input_keys)
    execution_order = [step.name for step in ordered]
    initial_guesses_required = _guesses_for_order(
        ordered, set(input_keys) | set(external_inputs)
    )

    if cycles:
        solver = "HybridSolver"
        reason = (
            f"{len(cycles)} feedback loop(s) detected; DAGSolver would raise. "
            "HybridSolver runs acyclic steps once and iterates only the cyclic blocks."
        )
    else:
        solver = "DAGSolver"
        reason = "No feedback loops; a single topological pass is enough."

    return PipelineAnalysis(
        steps=tuple(step.name for step in steps),
        execution_order=tuple(execution_order),
        cycles=cycles,
        external_inputs=external_inputs,
        terminal_outputs=terminal_outputs,
        initial_guesses_required=initial_guesses_required,
        recommended_solver=solver,
        reason=reason,
        stubs=tuple(step.name for step in steps if stub_status(step)),
    )


# ==============================================================================
# validate
# ==============================================================================

def _check_duplicate_outputs(steps: List[Step]) -> List[Finding]:
    """
    Two steps declaring the same output name. `map_producers` keeps only the
    last, so the earlier step still runs but its result is unreachable and
    consumers silently wire to the wrong producer.
    """
    seen: Dict[str, str] = {}
    findings = []

    for step in steps:
        for name in step.resolve_output_names():
            if name in seen:
                findings.append(
                    Finding(
                        code="duplicate-output",
                        severity=ERROR,
                        message=(
                            f"'{name}' is declared as an output by both "
                            f"'{seen[name]}' and '{step.name}'. Only "
                            f"'{step.name}' will be wired up; the other result "
                            f"is computed and discarded."
                        ),
                        step=step.name,
                        variable=name,
                    )
                )
            seen[name] = step.name

    return findings


def _check_type_edges(steps: List[Step], checker: TypeChecker) -> List[Finding]:
    """
    Producer/consumer type compatibility. Mirrors `validate_structure`, but
    collects every mismatch instead of raising on the first - an agent fixing
    generated code wants the whole list.
    """
    producers = map_producers(steps)
    findings = []

    for consumer in steps:
        for name, expected in consumer.resolve_input_types().items():
            producer = producers.get(name)
            if producer is None:
                continue

            produced = producer.resolve_output_types().get(name)
            if produced is None:
                continue

            if not checker.check_types(produced, expected):
                findings.append(
                    Finding(
                        code="type-mismatch",
                        severity=ERROR,
                        message=(
                            f"'{producer.name}' declares {name} -> "
                            f"{_format_type(produced)}, but '{consumer.name}' "
                            f"expects {name}: {_format_type(expected)}."
                        ),
                        step=consumer.name,
                        variable=name,
                    )
                )

    return findings


def _check_connectivity(steps: List[Step]) -> List[Finding]:
    """
    Whether every discipline is wired to the rest of the pipeline.

    A model whose dependency graph falls into separate pieces has a discipline
    connected to nothing - which means part of it cannot influence the answer,
    however well the rest converges. That failure is invisible at run time: the
    pipeline is valid, it converges, the arithmetic is right, and the result
    quietly ignores whole inputs.

    Deliberately *not* a general "orphaned output" check. Every healthy pipeline
    has terminal outputs - objective, constraints - so flagging unused variables
    on their own would fire on almost everything. It is the *disconnection* that
    is diagnostic; the dangling outputs are reported as supporting detail.
    """
    groups = weakly_connected_components(steps)
    if len(groups) < 2:
        return []

    consumed = {name for step in steps for name in _step_inputs(step)}

    # Every piece is described, and none is designated "the real pipeline".
    # Picking one - by size, say - is arbitrary the moment the pieces are
    # comparable, and the engineer is better placed to say which was intended.
    described = []
    for group in groups:
        names = [step.name for step in group]
        dangling = sorted(
            name
            for step in group
            for name in step.resolve_output_names()
            if name not in consumed
        )
        detail = f" -> {dangling} consumed by nothing" if dangling else ""
        described.append(f"{names}{detail}")

    return [
        Finding(
            code="disconnected-graph",
            severity=WARNING,
            message=(
                f"The pipeline falls into {len(groups)} disconnected pieces, so "
                f"nothing computed in one can affect another: "
                f"{'; '.join(described)}. This usually means a discipline was "
                f"never wired in - its inputs will have no influence on the "
                f"result, however well the rest converges."
            ),
        )
    ]


def _check_missing_inputs(
    steps: List[Step], input_keys: Set[str]
) -> List[Finding]:
    """
    Required parameters that nothing produces and nobody passes in.

    Grouped by variable rather than by step: a shared input like `z2` consumed
    by five disciplines is one thing to fix, not five, and an agent reading a
    finding list should see it that way.
    """
    producers = map_producers(steps)
    missing: Dict[str, List[str]] = {}

    for step in steps:
        for name in _required_inputs(step):
            if name not in producers and name not in input_keys:
                missing.setdefault(name, []).append(step.name)

    return [
        Finding(
            code="missing-input",
            severity=ERROR,
            message=(
                f"'{name}' is required by {', '.join(repr(s) for s in consumers)} "
                f"but no step produces it and it was not listed as an input."
            ),
            step=consumers[0] if len(consumers) == 1 else None,
            variable=name,
        )
        for name, consumers in missing.items()
    ]


def _check_unconsumed_inputs(steps: List[Step], inputs: Sequence[str]) -> List[Finding]:
    """
    Input names no step reads.

    A warning rather than information because of what it usually is: a typo.
    When the misspelt name belongs to a parameter with a default, nothing else
    fails - the default is quietly used and the value passed in is ignored.
    """
    consumed = {name for step in steps for name in _step_inputs(step)}
    return [
        Finding(
            code="unconsumed-input",
            severity=WARNING,
            message=(
                f"'{name}' is listed as an input but no step reads it, so its "
                f"value would be ignored. A typo, or a step that was renamed "
                f"or removed?"
            ),
            variable=name,
        )
        for name in inputs
        if name not in consumed
    ]


def _check_groups(steps: List[Step], input_keys: Set[str]) -> List[Finding]:
    """
    Groups the planner could not keep together, with the dependencies that
    prove it could not. Information: nothing computed is affected, only where
    steps sit in the run and on the diagram.
    """
    findings = []
    for conflict in group_conflicts(steps, input_keys):
        names = " and ".join(repr(group) for group in conflict.groups)
        if conflict.in_loop:
            message = (
                f"Groups {names} share a feedback loop ({', '.join(conflict.steps)}), "
                f"so their steps are interleaved there. A loop runs in alphabetical "
                f"order, which decides which variable needs a seed, so it is never "
                f"reordered for grouping."
            )
        else:
            shown = ", ".join(f"{producer} -> {consumer}" for producer, consumer in conflict.edges[:4])
            more = f" and {len(conflict.edges) - 4} more" if len(conflict.edges) > 4 else ""
            message = (
                f"{'Group' if len(conflict.groups) == 1 else 'Groups'} {names} cannot "
                f"{'' if len(conflict.groups) == 1 else 'all '}be kept together: "
                f"dependencies leave and re-enter them "
                f"({shown}{more}). Those steps are placed in plain execution order."
            )
        findings.append(Finding(code="groups-interleaved", severity=INFO, message=message))
    return findings


def _check_stubs(analysis: PipelineAnalysis) -> List[Finding]:
    """
    Steps that are declared but not written yet.

    Information, not a warning: a contract-first pipeline is stubs on purpose,
    and every other check still means something for it. Listed so progress is
    read from the pipeline rather than from a list kept by hand - and so nobody
    is surprised when a run stops at the first one.
    """
    return [
        Finding(
            code="stub-step",
            severity=INFO,
            message=(
                f"'{name}' only raises NotImplementedError - declared, not yet "
                f"written. Analysis is unaffected; a run will stop here."
            ),
            step=name,
        )
        for name in analysis.stubs
    ]


def _is_numeric_annotation(declared) -> bool:
    """
    Whether an annotation describes something `Bands.classify` can compare.

    `bool` is excluded even though it subclasses `int`: True would silently
    classify as 1.0 and land in whichever band contains it, which is a fact
    nobody intended. Anything that is not a plain class - `Any`, a generic, a
    missing annotation - is not judged here at all, per the invariant that
    absent type information degrades to unchecked rather than to a failure.
    """
    return (
        isinstance(declared, type)
        and not issubclass(declared, bool)
        and issubclass(declared, (int, float))
    )


def _check_discretisation(pipeline, steps: List[Step]) -> List[Finding]:
    """
    The declared thresholds that turn a number into a symbolic fact.

    This is the check docs/design/003 asks for by name. A reviewed set of rules
    sitting on an unreviewed mapping is not traceable, and the mapping is where
    the answer is actually decided - so the thresholds are reported alongside
    every other structural finding rather than left in a helper function.

    Only two questions are asked here, because modelling a band as a real step
    means the general checks already answer the rest: a source nothing produces
    is reported by `missing-input` against the synthetic step, and a band name a
    discipline also declares is reported by `duplicate-output`. What remains is
    what those checks cannot see.

    Nothing here classifies a value. Every finding comes from the declaration.
    """
    discretisation = getattr(pipeline, "discretisation", None)
    if not discretisation:
        return []

    producers = map_producers(steps)
    consumed: Set[str] = set()
    for step in steps:
        consumed.update(_step_inputs(step))

    findings = []

    for produced_name, band in discretisation.bands.items():
        source = band.variable

        if produced_name not in consumed:
            findings.append(
                Finding(
                    code="discretisation-unused",
                    severity=INFO,
                    message=(
                        f"'{produced_name}' is discretised from '{source}' and "
                        f"no step inside the pipeline consumes it. That is a "
                        f"mistake if you expected it to be wired in, and "
                        f"perfectly correct if you read it from the result and "
                        f"act on it outside - which is what the recommended "
                        f"topology in docs/design/002 does. Both look identical "
                        f"from here, which is why this is information rather "
                        f"than a warning."
                    ),
                    variable=produced_name,
                )
            )

        producer = producers.get(source)
        if producer is None:
            continue

        declared = producer.resolve_output_types().get(source)
        if isinstance(declared, type) and not _is_numeric_annotation(declared):
            findings.append(
                Finding(
                    code="discretisation-non-numeric",
                    severity=WARNING,
                    message=(
                        f"'{source}' is declared as {_format_type(declared)} by "
                        f"'{producer.name}', but bands for '{produced_name}' "
                        f"compare it against numeric edges. Classification will "
                        f"raise at run time. The band's own parameter is left "
                        f"unannotated on purpose, so the type edges cannot "
                        f"catch this."
                    ),
                    step=producer.name,
                    variable=source,
                )
            )

    return findings


def _decision_marker(step: Step, attribute: str) -> Optional[str]:
    """Read a marker a synthetic step carries, if it carries one."""
    return getattr(inspect.unwrap(step.fn), attribute, None)


def _check_decisions_in_cycles(
    analysis: PipelineAnalysis, steps: List[Step]
) -> List[Finding]:
    """
    Steps that make a discrete choice from values the loop has not settled yet.

    Two different mistakes with one shape. A step inside a cyclic block runs
    once per sweep, on *intermediate* values - and intermediate values are an
    artifact of the iteration path, not a result. When the step's output is
    continuous that is simply how fixed-point iteration works. When it is a
    **discrete choice**, three things follow that do not apply to a smooth
    discipline:

    * the residual is binary, so there is no notion of getting closer, and
      oscillation replaces slow convergence as the failure mode;
    * the loop can have several stable answers, each self-consistent and each
      reporting success, with the initial guess alone deciding which one - this
      was measured, not theorised (see docs/known-issues.md);
    * the choice becomes a function of the solver's trajectory, so it cannot be
      explained by pointing at the settled numbers.

    Reported as a warning, never an error. docs/design/002 finds the topology
    defensible when the choice genuinely must react to intermediate state, and
    docs/design/003 says to inform the engineer rather than decide for them.
    """
    if not analysis.cycles:
        return []

    by_name = {step.name: step for step in steps}
    findings = []

    for cycle in analysis.cycles:
        loop = " -> ".join(cycle.steps)

        for name in cycle.steps:
            step = by_name.get(name)
            if step is None:  # pragma: no cover - cycles are built from `steps`
                continue

            rules = _decision_marker(step, "rule_discipline")
            if rules is not None:
                program = rules.path
                findings.append(
                    Finding(
                        code="rules-in-cycle",
                        severity=WARNING,
                        message=(
                            f"'{name}' applies the rules in '{program}' from "
                            f"inside the loop {loop}, so it runs once per sweep "
                            f"and chooses from values that have not settled "
                            f"yet. A discrete choice inside a loop can leave it "
                            f"oscillating, or give it several stable answers "
                            f"that each report success. Putting the decision "
                            f"outside the cycle and iterating it explicitly "
                            f"avoids all of that - see docs/design/002."
                        ),
                        step=name,
                    )
                )

            band = _decision_marker(step, "derives_band")
            if band is not None:
                findings.append(
                    Finding(
                        code="discretisation-in-cycle",
                        severity=WARNING,
                        message=(
                            f"'{band}' is derived from a threshold inside the "
                            f"loop {loop}. A value crossing an edge mid-solve "
                            f"changes the band, which changes the result, which "
                            f"can move the value back - so this loop may settle "
                            f"in more than one place, each converged and "
                            f"self-consistent, chosen by the initial guess "
                            f"alone. Nothing reports which one you got."
                        ),
                        step=name,
                        variable=band,
                    )
                )

    return findings


def _check_side_effects(pipeline, steps: List[Step], input_keys: Set[str]) -> List[Finding]:
    """
    A step that declares side effects without saying what should happen when it
    repeats, on a pipeline where it would.

    A numeric discipline re-running is how convergence works. A step that writes
    a file, launches a subprocess or posts to an API re-running thirty times is
    something else, and unlike every other finding in this module the
    consequence is **not recoverable** - see docs/design/005. So this is also
    the one situation `run()` refuses outright; the finding exists so it is
    known before anything is run.

    `effects=True` is reported as `side-effect-in-cycle`; `run()` refuses it.
    `"once"` is reported as `side-effect-latched`, because the latch freezes a
    coupling the loop depends on and can change the answer while still
    reporting convergence. `"every-sweep"` is a stated intent and is silent.
    Which steps repeat is decided by
    `effects.repeating_step_names`, the same function `run()` asks - one
    planner, not two.
    """
    repeating = repeating_step_names(pipeline.solver, steps, input_keys)
    solver = type(pipeline.solver).__name__
    findings = []

    for step in steps:
        if step.name not in repeating or step.effects not in (True, "once"):
            continue

        where = (
            f"every step is swept repeatedly by {solver}"
            if solver == "IterativeSolver"
            else f"it sits inside a feedback loop, which {solver} iterates"
        )

        if step.effects == "once":
            # Measured, not argued: on a two-step loop whose fixed point is
            # 20, latching one step made it settle at 10 - and report
            # CONVERGED. A step inside a cyclic block feeds the loop back by
            # definition, so latching it always freezes a coupling.
            findings.append(
                Finding(
                    code="side-effect-latched",
                    severity=WARNING,
                    message=(
                        f"'{step.name}' is effects=\"once\" and would otherwise "
                        f"repeat, because {where}. Its output is frozen after the "
                        f"first sweep, so everything downstream in the loop reads "
                        f"the first-sweep value - the loop converges against that, "
                        f"not against its own fixed point, and still reports "
                        f"success. If the effect should happen once but the "
                        f"computation should iterate, split them: a pure step "
                        f"inside the loop, and the side effect on the linear part "
                        f"after it."
                    ),
                    step=step.name,
                )
            )
            continue

        findings.append(
            Finding(
                code="side-effect-in-cycle",
                severity=WARNING,
                message=(
                    f"'{step.name}' declares side effects (effects=True) and "
                    f"would run more than once, because {where}. run() will "
                    f"refuse it before executing anything. Say which you mean: "
                    f"effects=\"once\" to run on the first sweep and reuse the "
                    f"result, or effects=\"every-sweep\" if repeating is intended."
                ),
                step=step.name,
            )
        )

    return findings


def _check_rule_programs(steps: List[Step]) -> List[Finding]:
    """
    Programs that may not pin their own answer.

    The question is asked of the discipline rather than answered here: ASP
    syntax belongs in `rules.py`, and a second place that knows how to read an
    `.lp` file is a second thing to keep correct.

    Static and heuristic, and the finding says so. Only the runtime enumeration
    can prove an optimum is unique; what this catches are the two shapes that
    are ambiguous *by construction* and look right on the page.
    """
    findings = []

    for step in steps:
        rules = _decision_marker(step, "rule_discipline")
        if rules is None:
            continue

        concern = rules.pinning_concern
        if concern is None:
            continue

        findings.append(
            Finding(
                code="unpinned-program",
                severity=WARNING,
                message=(
                    f"'{rules.path}' may not pin its own answer: {concern}. "
                    f"Solving reports this exactly when it happens, but only "
                    f"for the facts it was given - this reads the program "
                    f"itself, and is a syntactic check rather than a proof."
                ),
                step=step.name,
            )
        )

    return findings


def _check_initial_guesses(
    analysis: PipelineAnalysis, solver_name: str
) -> List[Finding]:
    """
    Feedback variables that need a starting value before the first sweep.

    Which ones they are depends on the *alphabetical* order of step names
    inside the cyclic block, which nobody guesses correctly. Getting it wrong
    is a KeyError from deep inside the solve rather than an up-front complaint.
    """
    # Anything already passed to run() is treated as available when the guesses
    # are derived, so everything reaching here is genuinely unseeded.
    findings = []

    for guess in analysis.initial_guesses_required:
        findings.append(
            Finding(
                code="initial-guess-required",
                severity=ERROR,
                message=(
                    f"'{guess.consumed_by}' consumes '{guess.variable}' before "
                    f"anything produces it, so the first sweep has nothing to "
                    f"read. Ordering here is decided by {solver_name}, so "
                    f"renaming a step or swapping solvers can change which "
                    f"variable this is. Pass {guess.variable}=... to run()."
                ),
                step=guess.consumed_by,
                variable=guess.variable,
            )
        )

    return findings


def _check_solver_fit(
    pipeline,
    analysis: PipelineAnalysis,
    steps: List[Step],
    input_keys: Set[str],
) -> List[Finding]:
    """
    Whether the configured solver can actually run this pipeline.

    The expensive mistake is `IterativeSolver` on an acyclic pipeline whose
    registration order is not a valid execution order: it will sweep - possibly
    to `max_iterations` - and can report convergence on a state that was never
    properly evaluated. See docs/known-issues.md.
    """
    solver = pipeline.solver
    findings = []

    if isinstance(solver, DAGSolver) and analysis.has_cycles:
        findings.append(
            Finding(
                code="solver-mismatch",
                severity=ERROR,
                message=(
                    f"Pipeline has {len(analysis.cycles)} feedback loop(s) but "
                    f"uses DAGSolver, which raises on cycles. Use HybridSolver."
                ),
            )
        )

    if isinstance(solver, IterativeSolver):
        # `analysis.execution_order` IS registration order for this solver, so
        # compare against what the dependency graph would have chosen instead.
        # Compared by Step identity, not by name: two anonymous lambdas are both
        # called "<lambda>" and would otherwise look identically ordered.
        actual = _ordered_steps(pipeline, steps, input_keys)
        planned_steps = [
            step
            for block in build_execution_plan(steps, input_keys)
            for step in block.steps
        ]
        registered = [step.name for step in actual]
        planned = [step.name for step in planned_steps]

        if not analysis.has_cycles and actual != planned_steps:
            findings.append(
                Finding(
                    code="avoidable-iteration",
                    severity=WARNING,
                    message=(
                        "Pipeline is acyclic, but IterativeSolver runs steps in "
                        f"registration order {registered} rather than dependency "
                        f"order {planned}. It will sweep repeatedly, and a step "
                        "that returns its input unchanged on the first sweep can "
                        "make the solver report convergence before anything was "
                        "evaluated. Use DAGSolver or HybridSolver, or reorder."
                    ),
                )
            )

        if analysis.has_cycles and solver.target_var is None:
            # Specific to IterativeSolver: it sweeps the whole pipeline as one
            # block, so the max() spans every variable including ones with
            # nothing to do with the loop. HybridSolver scopes it to the cycle,
            # which is why this is not raised for it.
            findings.append(
                Finding(
                    code="no-target-var",
                    severity=INFO,
                    message=(
                        "IterativeSolver without target_var judges convergence on "
                        "a max() across every produced variable, so one noisy "
                        "variable holds the whole system back. Set target_var to "
                        "converge on the variable that matters."
                    ),
                )
            )

    # `target_var` is offered by both IterativeSolver and HybridSolver.
    solver_name = type(solver).__name__
    target = getattr(solver, "target_var", None)
    checker = getattr(solver, "convergence_checker", None)

    # A custom ConvergenceChecker without a target is the case that actually
    # bites. `_calculate_residual` calls distance() once per *produced variable*
    # when no target is set, iterating a set in arbitrary order - so a checker
    # carrying state across calls (OscillationAwareConvergenceChecker, say)
    # cannot tell those interleaved calls apart and its history is meaningless.
    #
    # Deliberately NOT flagged: HybridSolver with the standard checker and no
    # target. That is the idiomatic default, it works, and warning about it
    # would fire on almost every correct pipeline.
    if (
        analysis.has_cycles
        and target is None
        and checker is not None
        and not isinstance(checker, StandardConvergenceChecker)
    ):
        findings.append(
            Finding(
                code="checker-needs-target-var",
                severity=WARNING,
                message=(
                    f"{solver_name} uses a custom convergence checker "
                    f"({type(checker).__name__}) but sets no target_var. Without "
                    "one, distance() is called once per produced variable in "
                    "arbitrary order, so any checker that keeps state across "
                    "calls will see interleaved values and misbehave. Set "
                    "target_var to the coupling variable that matters."
                ),
            )
        )

    if target is not None:
        # IterativeSolver sweeps everything as one block, so any produced
        # variable is a legitimate target. HybridSolver only forwards the
        # target to the cyclic block producing it, so a target outside every
        # cycle is silently ignored.
        if isinstance(solver, HybridSolver):
            targetable = {
                name
                for block in build_execution_plan(steps, input_keys)
                if block.is_cyclic
                for step in block.steps
                for name in step.resolve_output_names()
            }
            scope = "any cyclic block"
        else:
            targetable = {
                name for step in steps for name in step.resolve_output_names()
            }
            scope = "any step"

        if target not in targetable:
            findings.append(
                Finding(
                    code="target-var-not-produced",
                    severity=WARNING,
                    message=(
                        f"target_var={target!r} is not produced by {scope}, so it "
                        f"cannot be converged on. {solver_name} falls back to the "
                        "default max() across all produced variables. Worse, if "
                        "nothing produces the name at all, the residual becomes "
                        "distance(None, None) = 0.0 and the loop reports "
                        "convergence on its first sweep without iterating."
                    ),
                    variable=target,
                )
            )

    return findings


def validate(
    pipeline,
    inputs: Optional[Sequence[str]] = None,
    type_checker: Optional[TypeChecker] = None,
) -> Tuple[Finding, ...]:
    """
    Collects everything statically wrong with `pipeline`, worst first.

    Unlike `validate_structure`, this never raises and never stops at the first
    problem - the point is to hand back a complete list to fix in one pass.
    `inputs` defaults to the pipeline's declared inputs, as in `analyze`.
    """
    steps = effective_steps(pipeline)
    inputs = inputs_for(pipeline, inputs)
    input_keys = set(inputs)
    checker = type_checker or StandardTypeChecker()

    analysis = analyze(pipeline, inputs)

    findings: List[Finding] = []
    findings.extend(_check_duplicate_outputs(steps))
    findings.extend(_check_connectivity(steps))
    findings.extend(_check_type_edges(steps, checker))
    findings.extend(_check_missing_inputs(steps, input_keys))
    findings.extend(_check_unconsumed_inputs(steps, inputs))
    findings.extend(
        _check_initial_guesses(analysis, type(pipeline.solver).__name__)
    )
    findings.extend(_check_solver_fit(pipeline, analysis, steps, input_keys))
    findings.extend(_check_discretisation(pipeline, steps))
    findings.extend(_check_decisions_in_cycles(analysis, steps))
    findings.extend(_check_rule_programs(steps))
    findings.extend(_check_side_effects(pipeline, steps, input_keys))
    findings.extend(_check_stubs(analysis))
    findings.extend(_check_groups(steps, input_keys))

    rank = {ERROR: 0, WARNING: 1, INFO: 2}
    findings.sort(key=lambda finding: rank[finding.severity])

    logger.debug(f"Validation produced {len(findings)} finding(s).")
    return tuple(findings)


# ==============================================================================
# explain
# ==============================================================================

def explain(pipeline, inputs: Optional[Sequence[str]] = None) -> str:
    """Human-readable account of the pipeline - for docs, review, or an agent."""
    inputs = inputs_for(pipeline, inputs)
    analysis = analyze(pipeline, inputs)
    findings = validate(pipeline, inputs)

    lines = [
        f"Pipeline with {len(analysis.steps)} step(s): {', '.join(analysis.steps)}.",
        "",
        f"Recommended solver: {analysis.recommended_solver}",
        f"  {analysis.reason}",
        "",
    ]

    if analysis.external_inputs:
        lines.append(f"External inputs: {', '.join(analysis.external_inputs)}")
    else:
        lines.append("External inputs: none - every variable is produced internally.")

    if analysis.terminal_outputs:
        lines.append(f"Final outputs:   {', '.join(analysis.terminal_outputs)}")
    lines.append("")

    if analysis.cycles:
        lines.append(f"Feedback loops ({len(analysis.cycles)}):")
        for index, cycle in enumerate(analysis.cycles, start=1):
            lines.append(f"  {index}. {' -> '.join(cycle.steps)}")
            lines.append(
                f"     coupling on: {', '.join(cycle.feedback_variables) or 'nothing detectable'}"
            )
    else:
        lines.append("Feedback loops: none.")
    lines.append("")

    if analysis.initial_guesses_required:
        names = ", ".join(
            f"{guess.variable} (for {guess.consumed_by})"
            for guess in analysis.initial_guesses_required
        )
        lines.append(f"Needs initial values for: {names}")
        lines.append("")

    lines.append(f"Execution order: {' -> '.join(analysis.execution_order)}")

    if analysis.stubs:
        lines.append("")
        lines.append(
            f"Not written yet ({len(analysis.stubs)} of {len(analysis.steps)} steps "
            f"only raise NotImplementedError): {', '.join(analysis.stubs)}"
        )

    touching = [step for step in effective_steps(pipeline) if step.has_effects]
    if touching:
        lines.append("")
        lines.append(f"Touches the world ({len(touching)}):")
        for step in touching:
            stated = "unspecified in a loop" if step.effects is True else str(step.effects)
            lines.append(f"  {step.name}: {stated}")

    discretisation = getattr(pipeline, "discretisation", None)
    if discretisation:
        lines.append("")
        lines.append(f"Discretisation ({len(discretisation.bands)}):")
        for name, band in discretisation.bands.items():
            lines.append(f"  {name}: {band.describe()}")
            lines.append(
                f"     edge values fall in the band "
                f"{'above' if band.closed == 'left' else 'below'} "
                f"(closed={band.closed!r})"
            )

    # Stubs are summarised above; one line each here would bury the rest.
    findings = [finding for finding in findings if finding.code != "stub-step"]
    if findings:
        lines.append("")
        lines.append(f"Findings ({len(findings)}):")
        lines.extend(f"  {finding}" for finding in findings)

    return "\n".join(lines)
