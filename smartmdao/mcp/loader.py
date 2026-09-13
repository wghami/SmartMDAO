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
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from ..core import Pipeline


class PipelineLoadError(RuntimeError):
    """Raised when a file cannot be turned into exactly one Pipeline."""


@dataclass(frozen=True)
class LoadedPipeline:
    pipeline: Pipeline
    variable: str
    path: Path


def _pipeline_variables(module) -> List[str]:
    return sorted(
        name
        for name, value in vars(module).items()
        if isinstance(value, Pipeline) and not name.startswith("_")
    )


def load_pipeline(path, variable: Optional[str] = None) -> LoadedPipeline:
    """
    Imports `path` and returns the Pipeline defined in it.

    If `variable` is given, that name is used. Otherwise the module must define
    exactly one Pipeline - ambiguity is reported rather than guessed at, with
    the candidates listed so the caller can pick.
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

    candidates = _pipeline_variables(module)

    if variable is not None:
        found = getattr(module, variable, None)
        if not isinstance(found, Pipeline):
            raise PipelineLoadError(
                f"'{variable}' is not a Pipeline in {resolved.name}. "
                f"Pipelines defined here: {candidates or 'none'}."
            )
        return LoadedPipeline(pipeline=found, variable=variable, path=resolved)

    if not candidates:
        raise PipelineLoadError(
            f"No Pipeline found in {resolved.name}. Assign one to a module-level "
            f"variable, e.g. `pipeline = Pipeline(...)`."
        )
    if len(candidates) > 1:
        raise PipelineLoadError(
            f"{resolved.name} defines several pipelines: {candidates}. "
            f"Pass `variable` to choose one."
        )

    return LoadedPipeline(
        pipeline=getattr(module, candidates[0]),
        variable=candidates[0],
        path=resolved,
    )
