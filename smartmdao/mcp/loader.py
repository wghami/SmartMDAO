"""
Loading a Pipeline object out of a user's Python file.

SECURITY NOTE, stated plainly because it is easy to oversell this:

SmartMDAO has no serialisable pipeline format - a Step *is* a live Python
function. To learn anything about a pipeline we have to import the module that
defines it, and importing a Python module executes everything at its top level.

What we can honestly promise is narrower, and still worth having: **no
discipline function is ever invoked.** Analysis reads signatures, annotations
and the dependency graph. It does not call a single step.

A module that does real work at import time will do that work here. Phase 3
moves execution into a subprocess with a timeout; until then this is a local,
stdio-only tool operating on files the user already has on disk, which the
coding agent driving it could equally well have run itself.
"""
import importlib.util
import inspect
import sys
import typing
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from ..core import Pipeline


class PipelineLoadError(RuntimeError):
    """Raised when a file cannot be turned into exactly one Pipeline."""


@dataclass(frozen=True)
class Factory:
    """A module-level callable that declares it returns a `Pipeline`."""
    name: str
    required_args: Tuple[str, ...]

    @property
    def callable_without_arguments(self) -> bool:
        return not self.required_args

    def __str__(self) -> str:
        if self.callable_without_arguments:
            return f"{self.name}()"
        return f"{self.name}() [needs: {', '.join(self.required_args)}]"


@dataclass(frozen=True)
class LoadedPipeline:
    pipeline: Pipeline
    variable: str
    path: Path
    #: "variable" if read from a module-level name, "factory" if a callable was
    #: invoked to produce it. Recorded because how a pipeline was obtained is
    #: part of what the engineer needs to know - see docs/design/003.
    source: str = "variable"


def _pipeline_variables(module) -> List[str]:
    return sorted(
        name
        for name, value in vars(module).items()
        if isinstance(value, Pipeline) and not name.startswith("_")
    )


def _returns_a_pipeline(function) -> bool:
    """
    Whether `function` declares a `Pipeline` return type.

    Annotation only. We never call a function to find out what it returns:
    a module's `run_demo()` would happily execute the entire study, and
    "no discipline is ever invoked" is the promise this whole layer rests on.
    """
    try:
        hints = typing.get_type_hints(function)
    except Exception:
        # Unresolvable annotations (forward refs, odd globals): fall back to
        # the raw string, which is what `from __future__ import annotations`
        # leaves behind.
        hints = getattr(function, "__annotations__", {})

    declared = hints.get("return")
    return declared is Pipeline or (
        isinstance(declared, str) and declared.split(".")[-1] == "Pipeline"
    )


def _required_arguments(function) -> Tuple[str, ...]:
    """Parameter names with no default, ignoring *args/**kwargs."""
    try:
        parameters = inspect.signature(function).parameters
    except (TypeError, ValueError):         # pragma: no cover - exotic callables
        return ()
    return tuple(
        name
        for name, parameter in parameters.items()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        not in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)
    )


def _pipeline_factories(module) -> List[Factory]:
    """Module-level functions annotated as returning a `Pipeline`."""
    factories = []

    for name, value in sorted(vars(module).items()):
        if name.startswith("_") or inspect.isclass(value) or not callable(value):
            continue
        if getattr(value, "__module__", None) != module.__name__:
            continue            # imported from elsewhere; not this file's API
        if not _returns_a_pipeline(value):
            continue

        factories.append(
            Factory(name=name, required_args=_required_arguments(value))
        )

    return factories


def _call_factory(function, name: str, path: Path) -> Pipeline:
    try:
        produced = function()
    except Exception as error:
        raise PipelineLoadError(
            f"Calling {name}() in {path.name} failed: "
            f"{type(error).__name__}: {error}"
        ) from error

    if not isinstance(produced, Pipeline):
        raise PipelineLoadError(
            f"{name}() in {path.name} is annotated as returning a Pipeline but "
            f"returned {type(produced).__name__}."
        )
    return produced


def load_pipeline(path, variable: Optional[str] = None) -> LoadedPipeline:
    """
    Imports `path` and returns the Pipeline defined in it.

    Two shapes are recognised, because both are normal Python:

      pipeline = Pipeline(...)            # a module-level instance
      def build() -> Pipeline: ...        # a factory, which is CALLED

    A factory is only ever called when it declares `-> Pipeline`, or when the
    caller names it explicitly. We never call a function speculatively to see
    what it returns: a module's `run_demo()` would execute the entire study,
    and "no discipline is ever invoked" is the promise this layer rests on.
    Calling a factory registers steps; it does not evaluate them.

    Ambiguity is reported rather than guessed at, and a factory that needs
    arguments is reported *with the argument names* rather than failing
    generically.

    Not reachable by any means: a pipeline built inside a function body and
    never returned. There is nothing to call and nothing to read.
    """
    resolved = Path(path).expanduser().resolve()

    if not resolved.is_file():
        raise PipelineLoadError(f"No such file: {resolved}")
    if resolved.suffix != ".py":
        raise PipelineLoadError(f"Not a Python file: {resolved}")

    # A unique module name keeps repeated loads of the same path from colliding
    # in sys.modules and silently returning a stale object.
    module_name = f"_smartmdao_loaded_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:            # pragma: no cover
        raise PipelineLoadError(f"Could not load {resolved} as a Python module.")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module

    # The file's own directory goes on sys.path so sibling imports resolve the
    # way they would if the user ran the script directly.
    parent = str(resolved.parent)
    added_to_path = parent not in sys.path
    if added_to_path:
        sys.path.insert(0, parent)

    try:
        spec.loader.exec_module(module)
    except Exception as error:
        raise PipelineLoadError(
            f"Importing {resolved.name} failed: {type(error).__name__}: {error}"
        ) from error
    finally:
        if added_to_path:
            sys.path.remove(parent)
        sys.modules.pop(module_name, None)

    instances = _pipeline_variables(module)
    factories = _pipeline_factories(module)

    # --- Explicitly named -----------------------------------------------------
    if variable is not None:
        found = getattr(module, variable, None)

        if isinstance(found, Pipeline):
            return LoadedPipeline(
                pipeline=found, variable=variable, path=resolved, source="variable"
            )

        if callable(found) and not inspect.isclass(found):
            declared = next((f for f in factories if f.name == variable), None)

            if declared is not None:
                if declared.required_args:
                    raise PipelineLoadError(
                        f"'{variable}' in {resolved.name} needs argument(s) "
                        f"{list(declared.required_args)}, so it cannot be called "
                        f"automatically. Give them defaults, or wrap it in a "
                        f"zero-argument factory."
                    )
                return LoadedPipeline(
                    pipeline=_call_factory(found, variable, resolved),
                    variable=variable,
                    path=resolved,
                    source="factory",
                )

            # Not annotated as a factory. Honour the caller's choice only if it
            # takes no arguments - otherwise this is almost certainly a
            # discipline, and calling it would be exactly what we promise not
            # to do. `_call_factory` still checks what comes back.
            if not _required_arguments(found):
                return LoadedPipeline(
                    pipeline=_call_factory(found, variable, resolved),
                    variable=variable,
                    path=resolved,
                    source="factory",
                )

        raise PipelineLoadError(
            f"'{variable}' is not a Pipeline or a Pipeline factory in "
            f"{resolved.name}. {_describe(instances, factories)}"
        )

    # --- Discovered -----------------------------------------------------------
    ready = [f for f in factories if f.callable_without_arguments]

    if len(instances) == 1 and not ready:
        return LoadedPipeline(
            pipeline=getattr(module, instances[0]),
            variable=instances[0],
            path=resolved,
            source="variable",
        )

    if not instances and len(ready) == 1:
        return LoadedPipeline(
            pipeline=_call_factory(getattr(module, ready[0].name), ready[0].name, resolved),
            variable=ready[0].name,
            path=resolved,
            source="factory",
        )

    if not instances and not factories:
        raise PipelineLoadError(
            f"No Pipeline found in {resolved.name}. Assign one to a module-level "
            f"variable (`pipeline = Pipeline(...)`), or expose a factory "
            f"annotated as returning a Pipeline (`def build() -> Pipeline:`). "
            f"A pipeline built inside a function body and never returned cannot "
            f"be reached without running that function."
        )

    if not instances and not ready:
        raise PipelineLoadError(
            f"{resolved.name} has Pipeline factories, but none can be called "
            f"without arguments: {', '.join(str(f) for f in factories)}. "
            f"Give the arguments defaults, or add a zero-argument wrapper."
        )

    raise PipelineLoadError(
        f"{resolved.name} offers several pipelines. Pass `variable` to choose "
        f"one. {_describe(instances, factories)}"
    )


def _describe(instances: List[str], factories: List[Factory]) -> str:
    parts = []
    if instances:
        parts.append(f"Pipelines: {instances}.")
    if factories:
        parts.append(f"Factories: {[str(f) for f in factories]}.")
    return " ".join(parts) or "This file defines neither."
