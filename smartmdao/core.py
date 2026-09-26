import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, List, Literal, Optional, Sequence, Tuple

from .discretisation import Discretisation, effective_steps
from .effects import SideEffectError, latch_once, refuse_unstated_effects
from .models import Step
from .solvers import Solver, DAGSolver
from .visualization import visualize_pipeline
from .validation import TypeChecker, StandardTypeChecker, validate_structure, validate_external_inputs

# Initialize module-level logger
logger = logging.getLogger(__name__)

_suspension = threading.local()


class ExecutionSuspended(RuntimeError):
    """The result of a `run()` made while execution was suspended was used."""


class _NotExecuted:
    """
    What `run()` returns while execution is suspended.

    Any attempt to *use* it raises, with an explanation. A module that merely
    calls `pipeline.run(...)` at top level loads fine; one that goes on to read
    the result has to be told plainly that nothing ran, rather than handed an
    empty dict it would misread as an answer.
    """

    _MESSAGE = (
        "this file calls pipeline.run() at module level and then uses the "
        "result. Nothing was executed: files are loaded for analysis without "
        "running their pipelines. Put the run under "
        "`if __name__ == \"__main__\":` so importing the file stays free."
    )

    def __getitem__(self, key):
        raise ExecutionSuspended(self._MESSAGE)

    def __getattr__(self, name):
        raise ExecutionSuspended(self._MESSAGE)

    def __iter__(self):
        raise ExecutionSuspended(self._MESSAGE)

    def __repr__(self) -> str:
        return "<not executed: pipeline.run() called while loading for analysis>"


@contextmanager
def suspend_execution():
    """
    Make `Pipeline.run()` a no-op on this thread for the duration.

    Used by the MCP loader, which has to *import* a file to find the pipeline in
    it - and importing a script executes its top-level code. Without this, a
    bare `pipeline.run(...)` at module level ran the whole study every time the
    file was analysed, which broke the one promise the analysis layer makes:
    introspection never requires execution. Measured, not theorised: two calls
    to `validate_pipeline` fired a declared side effect twice.

    Thread-local, so a real solve on another thread is unaffected.
    """
    previous = getattr(_suspension, "active", False)
    _suspension.active = True
    try:
        yield
    finally:
        _suspension.active = previous

def declared_names(names) -> Tuple[str, ...]:
    """
    `names` as a tuple of strings, in order, without repeats.

    A bare string is refused rather than iterated: `inputs="span"` would
    otherwise declare four one-letter inputs and every check downstream would
    be answering a question nobody asked.
    """
    if isinstance(names, (str, bytes)):
        raise TypeError(
            f"inputs must be a list of names, not a single string: "
            f"did you mean inputs=[{names!r}]?"
        )
    names = tuple(names)
    wrong = [name for name in names if not isinstance(name, str)]
    if wrong:
        raise TypeError(f"input names must be strings, got {wrong!r}")
    return tuple(dict.fromkeys(names))


def inputs_for(pipeline, inputs: Optional[Sequence[str]]) -> Tuple[str, ...]:
    """
    The input names an analysis should assume: the call's own list when it
    passes one - even an empty one - otherwise what the pipeline declares.
    """
    if inputs is not None:
        return declared_names(inputs)
    return tuple(getattr(pipeline, "inputs", ()))


@dataclass
class Pipeline:
    steps: list[Step] = field(default_factory=list)
    solver: Solver = field(default_factory=DAGSolver)
    # Static structural validation (producer/consumer type compatibility) always
    # runs before the first execution of a given pipeline shape - it's free.
    # Runtime per-call validation is opt-in since it adds overhead to every
    # step invocation, which matters inside IterativeSolver's convergence loop.
    runtime_type_checks: bool = False
    type_checker: TypeChecker = field(default_factory=StandardTypeChecker)
    # Declared thresholds mapping continuous variables to symbolic bands. Held
    # on the pipeline rather than on a consumer so `validate()` can report on
    # them without reaching into anything - the threshold is where the answer
    # is actually decided, so it has to be as visible as the graph is.
    discretisation: Discretisation = field(default_factory=Discretisation)
    # The names a caller is expected to pass to `run()`, declared once. Which
    # inputs are external is a property of the pipeline, not of each call:
    # without it, `analyze`, `validate`, `explain` and `visualize` had to be
    # told the list every time, and a name missed once came back as a false
    # `missing-input`. An explicit `inputs=` on any of those calls still wins.
    inputs: Sequence[str] = ()
    _structure_validated: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self):
        self.inputs = declared_names(self.inputs)

    def add(self, fn: Callable, outputs: list[str] = None, effects=None, group: Optional[str] = None):
        """
        Add a step to the pipeline.
        :param fn: The function to execute, or any object exposing `as_step()`
            - a `RuleDiscipline`, for instance. A discipline backed by a
            reviewed `.lp` file is a step like any other, and registering it as
            one is what lets the planner, the solver and every check in
            `analysis` handle it without a special case.
        :param outputs: Optional list of variable names this function produces.
            Ignored for an object supplying its own step, which already declares
            what it produces.
        :param effects: Declares that this step touches something outside the
            pipeline - see `Step`. A step inside a cyclic block runs once per
            sweep, which is a different proposition for a function that writes a
            file than for one that computes a number.
        :param group: Labels steps that belong together. The planner keeps a
            group's steps next to each other wherever the dependencies allow -
            in the run and on the diagram - and never reorders inside a
            feedback loop. It changes nothing about what is computed.
        """
        builder = getattr(fn, "as_step", None)
        if callable(builder):
            step = builder()
            if group is not None:
                step.group = group
                step.__post_init__()
        else:
            step = Step(fn, outputs, effects=effects, group=group)

        self.steps.append(step)
        self._structure_validated = False
        logger.debug(f"Added step '{step.name}' to pipeline.")
        return self

    def step(self, fn: Callable = None, *, outputs: List[str] = None, effects=None,
             group: Optional[str] = None):
        """
        Decorator to register a step. See `add` for `effects` and `group`.
        """
        if fn is not None and callable(fn):
            self.add(fn, outputs=outputs, effects=effects, group=group)
            return fn

        def wrapper(func):
            self.add(func, outputs=outputs, effects=effects, group=group)
            return func
        
        return wrapper

    def run(self, **inputs):
        """
        Validates types, then delegates execution to the configured Solver.
        """
        if getattr(_suspension, "active", False):
            logger.info(
                "Pipeline.run() called while loading a file for analysis; "
                "not executed."
            )
            return _NotExecuted()

        # A declared band is a step like any other, so the solver plans and runs
        # it alongside the disciplines rather than having facts injected around
        # the outside. See discretisation.effective_steps.
        steps = effective_steps(self)

        logger.info(f"Starting pipeline execution with {len(steps)} steps and inputs: {list(inputs.keys())}")
        try:
            if not self._structure_validated:
                validate_structure(steps, self.type_checker)
                self._structure_validated = True

            validate_external_inputs(steps, inputs, self.type_checker)

            # Before anything executes: a step that touches the world and
            # would repeat without saying so is refused, and "once" steps are
            # latched for this run only. See docs/design/005.
            refuse_unstated_effects(self.solver, steps, inputs.keys())
            steps = latch_once(steps)

            if self.runtime_type_checks:
                result = self.solver.solve(steps, inputs, type_checker=self.type_checker)
            else:
                result = self.solver.solve(steps, inputs)

            logger.info("Pipeline execution completed successfully.")
            return result
        except SideEffectError as e:
            # Not a failure: nothing ran. Calling it one would misdescribe
            # exactly the case the refusal exists to make clear.
            logger.warning(f"Pipeline execution refused, nothing was run: {e}")
            raise
        except Exception as e:
            logger.error(f"Pipeline execution failed: {e}")
            raise

    def visualize(self,
                  inputs: List[str] = None,
                  output_path: str = None,
                  view: bool = True):
        """
        Generates an XDSM diagram of the pipeline. `inputs` defaults to the
        pipeline's declared inputs.
        """
        input_set = set(inputs_for(self, inputs))
        logger.debug("Generating XDSM diagram for pipeline.")
        
        visualize_pipeline(
            steps=effective_steps(self),
            inputs=input_set,
            output_path=output_path,
            view=view
        )