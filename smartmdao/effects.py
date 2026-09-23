"""
What happens when a step touches something outside the pipeline.

A step inside a cyclic block runs once per sweep. For a numeric discipline that
is how convergence works; for one that writes a file, launches a subprocess or
posts to an API it is something else, and unlike every other failure this
library reports, the consequence is **not recoverable** - you cannot un-send an
email. docs/design/005 has the full reasoning.

This module is the run-time half. `validate()` reports the same situations
statically, and both ask `repeating_step_names`, so what the analysis describes
and what the solver refuses cannot drift apart.

Three rules:

* ``effects=True`` on a step that would run more than once is **refused**
  before anything executes. The author marked the function honestly without
  saying what should happen in a loop, which is exactly when to stop and ask.
* ``effects="once"`` is **latched**: it executes on its first invocation in a
  ``run()``, and every later sweep reuses that result.
* Anything that multiplies runs - an optimizer, a comparison of two files -
  refuses a pipeline with **any** declared effect unless the caller passes
  ``allow_effects=True``. A per-run latch cannot help there: each evaluation is
  a new run.
"""
import functools
import logging
from typing import Iterable, List, Set

from .graph import build_execution_plan
from .models import Step

logger = logging.getLogger(__name__)


class SideEffectError(RuntimeError):
    """
    A side-effecting step would run more times than its declaration allows.

    Raised rather than reported, deliberately. Every other finding in SmartMDAO
    is a warning because every other failure is recoverable; this one is not,
    and a warning printed alongside thirty created tickets is a post-mortem, not
    information the engineer can act on.
    """


def repeating_step_names(solver, steps: Iterable[Step], input_keys) -> Set[str]:
    """
    The steps `solver` would run more than once - which is **not** the same
    question as which steps sit in a cycle.

    * `IterativeSolver` sweeps every step it was given, cyclic or not.
    * `DAGSolver` runs each step exactly once (and refuses a cycle outright).
    * Anything else is taken to follow the graph, repeating only inside a
      cyclic block - which is what `HybridSolver` does, and the conservative
      reading for a custom solver.

    Uses `graph.build_execution_plan` with the same input keys the solver uses,
    so this cannot describe a plan the solver would not follow.
    """
    # Imported here: solvers imports graph, and graph must stay free of solvers.
    from .solvers import DAGSolver, IterativeSolver

    steps = list(steps)

    if isinstance(solver, IterativeSolver):
        return {step.name for step in steps}
    if isinstance(solver, DAGSolver):
        return set()

    return {
        step.name
        for block in build_execution_plan(steps, set(input_keys))
        if block.is_cyclic
        for step in block.steps
    }


def refuse_unstated_effects(solver, steps: List[Step], input_keys) -> None:
    """
    Raise before anything runs if a step with ``effects=True`` would repeat.

    `"once"` and `"every-sweep"` are answers and pass; `True` is the author
    saying "this touches the world" without saying what should happen in a
    loop, and in a loop that is the question.
    """
    repeating = repeating_step_names(solver, steps, input_keys)
    unstated = [s for s in steps if s.effects is True and s.name in repeating]
    if not unstated:
        return

    solver_name = type(solver).__name__
    names = ", ".join(f"'{s.name}'" for s in unstated)
    raise SideEffectError(
        f"{names} {'has' if len(unstated) == 1 else 'have'} side effects "
        f"(effects=True) and would run more than once under {solver_name}, "
        f"once per sweep. Nothing has been executed. Declare which you want:\n"
        f"    effects=\"once\"        run on the first sweep, reuse the result\n"
        f"    effects=\"every-sweep\" run every time - you meant it\n"
        f"validate() reports this statically as side-effect-in-cycle."
    )


def latch_once(steps: List[Step]) -> List[Step]:
    """
    Wrap every ``effects="once"`` step so it executes once per call to this.

    Called at the start of each ``run()``, so the latch is scoped to a single
    solve: a second ``run()`` is a second study and executes the step again.
    Within a run the step's outputs are **frozen** after the first sweep while
    the rest of the loop keeps iterating - a real semantic change, which is why
    ``"once"`` has to be asked for rather than being a default.
    """
    return [_latched(step) if step.effects == "once" else step for step in steps]


def _latched(step: Step) -> Step:
    original = step.fn
    unset = object()
    result = [unset]

    # functools.wraps sets __wrapped__, so Step.get_signature and the type
    # hints still see the real function through inspect.unwrap - the latch is
    # invisible to wiring, ordering and type checking.
    @functools.wraps(original)
    def once(**kwargs):
        if result[0] is unset:
            result[0] = original(**kwargs)
        else:
            logger.debug(f"'{step.name}' is effects=\"once\"; reusing its first result.")
        return result[0]

    return Step(once, step.manual_outputs, effects=step.effects)


def refuse_declared_effects(steps: Iterable[Step], *, runs_many_times_because: str) -> None:
    """
    Raise if any step declares effects, for callers that multiply runs.

    Every declaration counts here, `"once"` and `"every-sweep"` included: both
    are scoped to a single ``run()``, and the whole point of the caller is to
    make many of them.
    """
    declared = [step for step in steps if step.has_effects]
    if not declared:
        return

    raise SideEffectError(effects_refusal_message(declared, runs_many_times_because))


def effects_refusal_message(declared: List[Step], runs_many_times_because: str) -> str:
    """The shared wording, so the optimizer and the MCP tools say the same thing."""
    names = ", ".join(f"'{step.name}' ({step.effects!r})" for step in declared)
    return (
        f"{names} declare{'s' if len(declared) == 1 else ''} side effects. "
        f"{runs_many_times_because} - and effects=\"once\" is scoped to a single "
        f"run, so it would still fire every time. Nothing has been executed. "
        f"Pass allow_effects=True if that is intended."
    )
