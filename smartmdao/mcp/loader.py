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
import ast
import contextlib
import importlib.util
import inspect
import sys
import typing
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core import Pipeline


#: Marks an input found in source whose value is computed rather than literal. The
#: name is known; the value is not, so the caller must supply it to run.
_UNRESOLVED = object()


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
    #: Variable names the file itself passes to `run()`, recovered from the
    #: source without executing it. See `inputs_in_source`.
    inputs_in_source: Tuple[str, ...] = ()


def inputs_in_source(path) -> Tuple[str, ...]:
    """
    The variable names a script passes to `pipeline.run(...)`, read statically.

    Without this, a caller has to *guess* what the file supplies - and an agent
    that guesses the design variables but forgets the cycle's initial guess gets
    told the seed is missing, which is exactly the thing it forgot to mention.
    The tool then reports a working file as broken. Reading what the file
    already says removes the guess.

    Two shapes are recognised, covering how people actually write it:

        pipeline.run(z1=1.0, y2=1.0)
        inputs = {"z1": 1.0, "y2": 1.0}; pipeline.run(**inputs)

    Anything dynamic is simply not found; this narrows the guessing, it does not
    eliminate it. Pure AST work - nothing is imported or executed.
    """
    return tuple(sorted(input_map_in_source(path)))


def input_map_in_source(path) -> Dict[str, Any]:
    """
    The same names, with their **values** where those are literal constants.

    Names alone answer "is this seed already supplied?" - enough for `analyze`
    and `validate`, which never run anything. Actually *running* the pipeline
    needs the values too, so that `run_pipeline(path)` behaves the way running
    the script does.

    Only literal constants are recovered. A value computed at run time is
    invisible here and simply absent from the map, which is why the caller can
    always override or supply its own.
    """
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):          # pragma: no cover - load_pipeline catches first
        return {}

    def constant(node):
        """A literal, including the collection literals people write in inputs."""
        if isinstance(node, ast.Constant):
            return node.value, True
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            items = [constant(item) for item in node.elts]
            if all(ok for _, ok in items):
                values = [value for value, _ in items]
                return (
                    set(values) if isinstance(node, ast.Set) else
                    tuple(values) if isinstance(node, ast.Tuple) else values
                ), True
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            inner, ok = constant(node.operand)
            if ok and isinstance(inner, (int, float)):
                return -inner, True
        return None, False

    literals: Dict[str, Dict[str, Any]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            entries: Dict[str, Any] = {}
            for key, value in zip(node.value.keys, node.value.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    resolved, ok = constant(value)
                    entries[key.value] = resolved if ok else _UNRESOLVED
            for target in node.targets:
                if isinstance(target, ast.Name):
                    literals[target.id] = entries

    found: Dict[str, Any] = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
        ):
            continue
        for keyword in node.keywords:
            if keyword.arg is not None:
                resolved, ok = constant(keyword.value)
                found[keyword.arg] = resolved if ok else _UNRESOLVED
            elif isinstance(keyword.value, ast.Name):
                found.update(literals.get(keyword.value.id, {}))

    return found


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


def user_output_to_stderr():
    """
    Sends whatever the user's code prints to stderr while it runs.

    The MCP server speaks JSON-RPC on stdout, and the run and worker processes
    answer in JSON on stdout. A file that prints while it is imported - a
    progress line, a banner - wrote into those channels: over MCP the client
    logged "Failed to parse JSONRPC message" (measured with the Python client,
    which recovered; others need not), and in run_pipeline any print() in a
    discipline turned a successful run into "not valid JSON". What is printed
    is not lost; it is simply not allowed where a protocol is being spoken.
    """
    return contextlib.redirect_stdout(sys.stderr)


def _call_factory(function, name: str, path: Path) -> Pipeline:
    try:
        with user_output_to_stderr():
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


def _package_root(directory: Path) -> Optional[Path]:
    """The top of the `__init__.py` chain `directory` sits in, if it is in one."""
    root = None
    while (directory / "__init__.py").is_file():
        root = directory
        directory = directory.parent
    return root


def _forget_user_modules(names, search_dir: Path) -> None:
    """
    Drop the user's own modules this load imported, so the next load reads them
    again.

    The MCP server is one long-lived process. Without this, a sibling module -
    `physics.py` next to the pipeline - was imported once and cached, and an
    edit to it was invisible to every later analysis until the server
    restarted: the pipeline file was re-read, the discipline it imported was
    not. Installed libraries are left alone; re-importing those is slow at
    best, and some extension modules cannot be imported twice.
    """
    for name in names:
        location = getattr(sys.modules.get(name), "__file__", None)
        if not location:
            continue
        path = Path(location).resolve()
        if path.is_relative_to(search_dir) and not {"site-packages", "dist-packages"} & set(path.parts):
            sys.modules.pop(name, None)


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

    # A file inside a package is imported as what it is - `pkg.module`, with
    # the package's parent on sys.path - so its relative imports resolve the
    # way `python -m pkg.module` would resolve them. A standalone file gets a
    # unique name instead, so repeated loads of one path cannot collide.
    package_root = _package_root(resolved.parent)
    if package_root is None:
        module_name = f"_smartmdao_loaded_{uuid.uuid4().hex}"
        search_dir = resolved.parent
    else:
        parts = resolved.relative_to(package_root.parent).with_suffix("").parts
        module_name = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
        search_dir = package_root.parent

    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:            # pragma: no cover
        raise PipelineLoadError(f"Could not load {resolved} as a Python module.")

    module = importlib.util.module_from_spec(spec)
    already_loaded = set(sys.modules)
    sys.modules[module_name] = module

    # The search directory goes on sys.path so sibling imports resolve the way
    # they would if the user ran the script directly.
    parent = str(search_dir)
    added_to_path = parent not in sys.path
    if added_to_path:
        sys.path.insert(0, parent)

    # Importing executes the file's top-level code, so a bare
    # `pipeline.run(...)` there would run the study just to find the pipeline -
    # twice under run_pipeline, which then runs it for real. Suspending
    # execution keeps "no discipline is ever invoked" true for that shape.
    from ..core import suspend_execution

    try:
        with suspend_execution(), user_output_to_stderr():
            spec.loader.exec_module(module)
    except Exception as error:
        raise PipelineLoadError(
            f"Importing {resolved.name} failed: {type(error).__name__}: {error}"
        ) from error
    finally:
        if added_to_path:
            sys.path.remove(parent)
        sys.modules.pop(module_name, None)
        _forget_user_modules(set(sys.modules) - already_loaded, search_dir)

    instances = _pipeline_variables(module)
    factories = _pipeline_factories(module)
    in_source = inputs_in_source(resolved)

    # --- Explicitly named -----------------------------------------------------
    if variable is not None:
        found = getattr(module, variable, None)

        if isinstance(found, Pipeline):
            return LoadedPipeline(
                pipeline=found,
                variable=variable,
                path=resolved,
                source="variable",
                inputs_in_source=in_source,
            )

        if callable(found) and not inspect.isclass(found):
            # Not `declared`: reusing that name here once replaced the inputs
            # read from source with this descriptor, and every handler given an
            # explicitly named factory crashed iterating it.
            factory = next((f for f in factories if f.name == variable), None)

            if factory is not None:
                if factory.required_args:
                    raise PipelineLoadError(
                        f"'{variable}' in {resolved.name} needs argument(s) "
                        f"{list(factory.required_args)}, so it cannot be called "
                        f"automatically. Give them defaults, or wrap it in a "
                        f"zero-argument factory."
                    )
                return LoadedPipeline(
                    pipeline=_call_factory(found, variable, resolved),
                    variable=variable,
                    path=resolved,
                    source="factory",
                    inputs_in_source=in_source,
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
                    inputs_in_source=in_source,
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
            inputs_in_source=in_source,
        )

    if not instances and len(ready) == 1:
        return LoadedPipeline(
            pipeline=_call_factory(getattr(module, ready[0].name), ready[0].name, resolved),
            variable=ready[0].name,
            path=resolved,
            source="factory",
            inputs_in_source=in_source,
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
