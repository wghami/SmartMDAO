import logging
import pathlib
import tempfile

from smartmdao import configure_logging
from smartmdao.mcp.handlers import compare_runs

logger = logging.getLogger(__name__)

# ==============================================================================
# WHY THIS DEMO EXISTS
# ==============================================================================
# You have a working hand-written model. An agent rewrites it as a SmartMDAO
# pipeline. It looks cleaner, the tests are green, validate() is silent.
#
# How do you know it still gives the same answer?
#
# You cannot know by reading. Both versions are well-formed; the difference
# lives in when each one decides it has finished, which no amount of static
# analysis can see. The only way is to run both on the same inputs and diff.
#
# This demo uses the REAL hand-written loop from this project's own README, so
# the comparison is against code someone actually wrote rather than a case
# constructed to fail. It shows:
#
#   1. how to compare against code that is not a pipeline at all
#   2. what "they agree" looks like, and what it is relative to
#   3. that a real semantic difference can hide below your tolerance
#   4. when the difference is big enough to matter
#
# `scripts/translation_drift_demo.py` is the companion: it shows the failure
# mode in isolation. This one shows the workflow.
# ==============================================================================

# The README's loop, VERBATIM, wrapped as a one-step pipeline so it can be
# compared. This is the technique worth remembering: anything callable can be a
# discipline, so the way to compare non-SmartMDAO code is to wrap it.
#
# Note `y2_guess`, not `y2`. A step consuming and producing the same name is a
# self-loop, and the solver would iterate the whole hand-written loop. Naming
# the seed distinctly keeps it a single pass, which is what we want: the
# original's own loop is the thing under test.
ORIGINAL = '''
"""The hand-written loop from the README, wrapped so it can be compared."""
import math

from smartmdao import Pipeline

pipeline = Pipeline()


@pipeline.step(outputs=["y1", "y2"])
def original_loop(z1: float, z2: float, x1: float, y2_guess: float) -> tuple:
    y2 = y2_guess
    for _ in range(100):
        y1 = z1 ** 2 + z2 + x1 - 0.2 * y2
        y2_next = math.sqrt(abs(y1)) + z1 + z2
        if abs(y2_next - y2) < 1e-6:
            break
        y2 = y2_next
    return y1, y2
'''

TRANSLATED = '''
"""The same model as a SmartMDAO pipeline."""
import math

from smartmdao import Pipeline, HybridSolver

pipeline = Pipeline(solver=HybridSolver(max_iterations=100, tolerance=1e-6{target}))


@pipeline.step(outputs=["y1"])
def discipline_1(z1: float, z2: float, x1: float, y2: float) -> float:
    return z1 ** 2 + z2 + x1 - 0.2 * y2


@pipeline.step(outputs=["y2"])
def discipline_2(z1: float, z2: float, y1: float) -> float:
    return math.sqrt(abs(y1)) + z1 + z2
'''

# Sellar's published optimum, so the numbers below are recognisable.
INPUTS = {"z1": 1.9776, "z2": 0.0, "x1": 0.0, "y2": 1.0, "y2_guess": 1.0}


def show(result) -> None:
    print(f"  match       : {result['match']}")
    print(f"  identical   : {result['identical']} variables")
    if result["differences"]:
        for difference in result["differences"]:
            relative = difference.get("relative")
            print(f"    {difference['variable']:<3} original={difference['a']:.10f}  "
                  f"translated={difference['b']:.10f}"
                  + (f"  rel={relative:.2e}" if relative is not None else ""))
    print(f"  tolerance   : {result['tolerance']}")


def run_translation_equivalence_demo():
    configure_logging(level=logging.CRITICAL)
    workspace = pathlib.Path(tempfile.mkdtemp(prefix="smartmdao_equiv_"))

    original = workspace / "original.py"
    original.write_text(ORIGINAL)

    translated = workspace / "translated.py"
    translated.write_text(TRANSLATED.format(target=""))

    # ==========================================================================
    # CASE 1: comparing against something that is not a pipeline
    # ==========================================================================
    print("=== CASE 1: the original is a plain `for` loop, not a pipeline ===")
    print("  `compare_runs` takes two pipelines, so wrap the original in a")
    print("  one-step pipeline. Any callable can be a discipline, so this costs")
    print("  five lines and changes nothing about how the original behaves.")
    print()
    print("  One catch, worth knowing: the wrapper takes `y2_guess`, not `y2`.")
    print("  A step that consumes and produces the same name is a self-loop, and")
    print("  the solver would iterate your hand-written loop. Naming the seed")
    print("  distinctly keeps it the single pass you are trying to measure.")

    # ==========================================================================
    # CASE 2: do they agree?
    # ==========================================================================
    print("\n=== CASE 2: run both on the same inputs ===")
    result = compare_runs(str(original), str(translated), inputs=INPUTS)
    show(result)
    print()
    print("  -> They agree. That is the answer you wanted, and note what it")
    print("     actually means: every variable is within a RELATIVE 1e-6 of its")
    print("     counterpart. It does not mean the two are identical.")

    # ==========================================================================
    # CASE 3: a real semantic difference, hiding below the tolerance
    # ==========================================================================
    print("\n=== CASE 3: tighten the tolerance and something appears ===")
    from smartmdao.mcp.comparison import diff_states
    from smartmdao.mcp.execution import run_in_subprocess

    runs = {
        label: run_in_subprocess(str(path), inputs=INPUTS, rung="full")
        for label, path in (("a", original), ("b", translated))
    }

    strict = diff_states(runs["a"]["state"], runs["b"]["state"], rtol=1e-12, atol=1e-15)
    print(f"  match at rtol=1e-12 : {strict['match']}")
    for difference in strict["differences"]:
        print(f"    {difference['variable']:<3} original={difference['a']:.10f}  "
              f"translated={difference['b']:.10f}  rel={difference['relative']:.2e}")
    print()
    print("  -> There IS a difference, and it is not rounding. Look at the loop:")
    print()
    print("         if abs(y2_next - y2) < 1e-6:")
    print("             break                      <- breaks BEFORE the assignment")
    print("         y2 = y2_next")
    print()
    print("     The hand-written version returns the PREVIOUS y2, never the one")
    print("     that satisfied the test. SmartMDAO stores the new value. That is")
    print("     a genuine semantic difference, it is about 2e-8 relative, and")
    print("     whether it matters is an engineering judgement - not something")
    print("     the tool should make for you. Your tolerance IS that judgement.")

    # ==========================================================================
    # CASE 4: when the criteria really do diverge
    # ==========================================================================
    print("\n=== CASE 4: the convergence criteria happened to agree here ===")
    faithful = workspace / "faithful.py"
    faithful.write_text(TRANSLATED.format(target=', target_var="y2"'))

    criteria = compare_runs(str(faithful), str(translated), inputs=INPUTS)
    print(f"  watching y2 alone vs watching everything -> match: {criteria['match']}")
    print(f"  iterations: faithful={criteria['runs']['a']['iterations']} "
          f"idiomatic={criteria['runs']['b']['iterations']}")
    print()
    print("  -> Identical, for THIS model. The original loop tested y2 alone;")
    print("     the idiomatic translation tests every produced variable. Here")
    print("     both stop on the same sweep, so the difference never surfaces.")
    print()
    print("     That is the real lesson. Drift is POSSIBLE, not guaranteed - so")
    print("     you check rather than assume. Run translation_drift_demo.py to")
    print("     see the same two criteria disagree by 95% on a model where y1")
    print("     settles more slowly than y2.")

    # ==========================================================================
    # CASE 5: what it will not tell you
    # ==========================================================================
    print("\n=== CASE 5: the limit ===")
    print("  `compare_runs` tells you two pipelines disagree. It cannot tell you")
    print("  which one is RIGHT - that is still yours to decide. What changes is")
    print("  that you decide with the difference in front of you instead of")
    print("  assuming there isn't one.")


if __name__ == "__main__":
    run_translation_equivalence_demo()
