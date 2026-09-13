import contextlib
import io
import logging
import pathlib
import tempfile
import textwrap

from smartmdao import configure_logging
from smartmdao.mcp import PipelineLoadError, load_pipeline

logger = logging.getLogger(__name__)

# ==============================================================================
# WHY THIS DEMO EXISTS
# ==============================================================================
# Every MCP tool starts by finding the Pipeline in a file. If it cannot, none
# of the analysis happens - so what the loader can and cannot reach decides how
# much of this project is actually usable.
#
# Originally it only saw a Pipeline assigned to a module-level variable. That
# read 7 of this repository's own 23 scripts. Every failure was the same thing:
# a pipeline built inside a factory function, which is normal Python.
#
# It now also calls factories. The rule that matters is what it will NOT do:
#
#   A function is only called when it DECLARES `-> Pipeline`, or when you name
#   it explicitly.
#
# We never call a function speculatively to see what it returns. A module's
# `run_demo()` would execute the whole study - and "no discipline is ever
# invoked" is the promise the entire analysis layer rests on.
# ==============================================================================

SHAPES = {
    "a module-level instance": '''
from smartmdao import Pipeline
pipeline = Pipeline()
pipeline.add(lambda a: a * 2, outputs=["b"])
''',
    "a factory declaring -> Pipeline": '''
from smartmdao import Pipeline
def build() -> Pipeline:
    pipeline = Pipeline()
    pipeline.add(lambda a: a * 2, outputs=["b"])
    return pipeline
''',
    "a factory with defaulted arguments": '''
from smartmdao import Pipeline
def build(tolerance: float = 1e-6) -> Pipeline:
    pipeline = Pipeline()
    pipeline.add(lambda a: a * 2, outputs=["b"])
    return pipeline
''',
    "a factory that NEEDS arguments": '''
from smartmdao import Pipeline
def build(solver, tolerance) -> Pipeline:
    return Pipeline(solver=solver)
''',
    "two factories (ambiguous)": '''
from smartmdao import Pipeline
def build_cruise() -> Pipeline:
    return Pipeline()
def build_climb() -> Pipeline:
    return Pipeline()
''',
    "an unannotated function": '''
from smartmdao import Pipeline
def run_the_whole_study():          # no -> Pipeline
    pipeline = Pipeline()
    pipeline.add(lambda a: a * 2, outputs=["b"])
    return pipeline
''',
    "trapped inside a function body": '''
from smartmdao import Pipeline
def run_demo():
    pipeline = Pipeline()           # local, never returned
    pipeline.add(lambda a: a * 2, outputs=["b"])
    print(pipeline.run(a=1))
''',
}


def try_load(path, **kwargs) -> str:
    try:
        loaded = load_pipeline(path, **kwargs)
        return f"OK    {loaded.variable} ({loaded.source})"
    except PipelineLoadError as error:
        return f"NO    {error}"


def run_pipeline_discovery_demo():
    configure_logging(level=logging.CRITICAL)
    workspace = pathlib.Path(tempfile.mkdtemp(prefix="smartmdao_discovery_"))

    # ==========================================================================
    # CASE 1: what the loader makes of each shape
    # ==========================================================================
    print("=== CASE 1: seven ways to define a pipeline, and what happens ===")
    for index, (label, source) in enumerate(SHAPES.items()):
        path = workspace / f"shape_{index}.py"
        path.write_text(source)
        print(f"\n  {label}:")
        verdict, _, detail = try_load(path).partition("    ")
        wrapped = textwrap.wrap(detail, width=84) or [""]
        print(f"    {verdict}  {wrapped[0]}")
        for continuation in wrapped[1:]:
            print(f"          {continuation}")

    print()
    print("  -> Note the last two. An unannotated function is left alone even")
    print("     though calling it WOULD have produced a pipeline, because the")
    print("     loader cannot know it is not `run_the_whole_study()`. And a")
    print("     pipeline that never leaves a function body is unreachable by")
    print("     any means - there is nothing to call and nothing to read.")

    # ==========================================================================
    # CASE 2: the escape hatches
    # ==========================================================================
    print("\n=== CASE 2: naming it explicitly ===")

    ambiguous = workspace / "shape_4.py"
    print(f"  ambiguous file, no hint  : {try_load(ambiguous)[:70]}")
    print(f"  ...with variable=         : {try_load(ambiguous, variable='build_climb')}")

    unannotated = workspace / "shape_5.py"
    print(f"  unannotated, no hint     : {try_load(unannotated)[:70]}")
    print(f"  ...with variable=         : {try_load(unannotated, variable='run_the_whole_study')}")
    print()
    print("  -> Naming it is you taking responsibility for what gets called.")
    print("     The loader still checks that a Pipeline came back.")

    # ==========================================================================
    # CASE 3: measured against this repository
    # ==========================================================================
    print("\n=== CASE 3: how much of scripts/ can the tools actually read? ===")
    auto, needs_hint, unreachable = [], [], []
    for script in sorted(pathlib.Path("scripts").glob("*.py")):
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                load_pipeline(script)
            auto.append(script.name)
        except PipelineLoadError as error:
            (needs_hint if "Pass `variable`" in str(error) else unreachable).append(script.name)

    total = len(auto) + len(needs_hint) + len(unreachable)
    print(f"  auto-discovered      : {len(auto)} of {total}   (was 7 before factories)")
    print(f"  reachable by name    : {len(needs_hint)}")
    print(f"  genuinely unreachable: {len(unreachable)}")
    print()
    print("  -> The remainder are demo scripts that build their pipelines inside")
    print("     `run_*_demo()`. That is normal for a SCRIPT and unusual for a")
    print("     MODEL, so this understates the picture for real engineering")
    print("     code - but it is the honest number, and it is not 23.")


if __name__ == "__main__":
    run_pipeline_discovery_demo()
