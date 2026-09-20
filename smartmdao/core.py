import logging
from dataclasses import dataclass, field
from typing import Callable, List, Literal

from .discretisation import Discretisation, effective_steps
from .models import Step
from .solvers import Solver, DAGSolver
from .visualization import visualize_pipeline
from .validation import TypeChecker, StandardTypeChecker, validate_structure, validate_external_inputs

# Initialize module-level logger
logger = logging.getLogger(__name__)

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
    _structure_validated: bool = field(default=False, init=False, repr=False, compare=False)

    def add(self, fn: Callable, outputs: list[str] = None, effects=None):
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
        """
        builder = getattr(fn, "as_step", None)
        if callable(builder):
            step = builder()
        else:
            step = Step(fn, outputs, effects=effects)

        self.steps.append(step)
        self._structure_validated = False
        logger.debug(f"Added step '{step.name}' to pipeline.")
        return self

    def step(self, fn: Callable = None, *, outputs: List[str] = None, effects=None):
        """
        Decorator to register a step. See `add` for `effects`.
        """
        if fn is not None and callable(fn):
            self.add(fn, outputs=outputs, effects=effects)
            return fn

        def wrapper(func):
            self.add(func, outputs=outputs, effects=effects)
            return func
        
        return wrapper

    def run(self, **inputs):
        """
        Validates types, then delegates execution to the configured Solver.
        """
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

            if self.runtime_type_checks:
                result = self.solver.solve(steps, inputs, type_checker=self.type_checker)
            else:
                result = self.solver.solve(steps, inputs)

            logger.info("Pipeline execution completed successfully.")
            return result
        except Exception as e:
            logger.error(f"Pipeline execution failed: {e}")
            raise

    def visualize(self,
                  inputs: List[str] = None,
                  output_path: str = None,
                  view: bool = True):
        """
        Generates an XDSM diagram of the pipeline.
        """
        input_set = set(inputs or [])
        logger.debug("Generating XDSM diagram for pipeline.")
        
        visualize_pipeline(
            steps=effective_steps(self),
            inputs=input_set,
            output_path=output_path,
            view=view
        )