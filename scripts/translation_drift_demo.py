import logging
import pathlib
import tempfile

from smartmdao import configure_logging
from smartmdao.mcp.handlers import compare_runs

logger = logging.getLogger(__name__)

# ==============================================================================
# WHY THIS DEMO EXISTS
# ==============================================================================
# Converting hand-written code to SmartMDAO is a stated use case, and its
# failure mode is the worst kind: SILENT SEMANTIC DRIFT. The translation looks
# cleaner, so it gets trusted, and it quietly returns a different number.
#
# The README's own "without SmartMDAO" example is the illustration. Its loop
# ends on:
#
#     if abs(y2_next - y2) < 1e-6: break          # y2 ALONE
#
# The obvious translation converges on a max() across EVERY produced variable.
# Those two criteria look equivalent and are not: they can stop in different
# places, and both will report success.
#
# Nothing catches that by reading the code. Not the agent, not validate(), not
# a type checker - both versions are structurally perfect. Only running both
# and diffing the answers reveals it, which is why `compare_runs` exists and
# why it is a better argument for execution tooling than "run my pipeline".
# ==============================================================================

# One model, two translations. The physics is identical; only the stopping
# rule differs. y1 settles slowly, y2 barely moves - so a criterion watching y2
# is satisfied almost at once while one watching everything keeps going.
MODEL = '''
"""{label}"""
from smartmdao import Pipeline, HybridSolver

pipeline = Pipeline(solver=HybridSolver(max_iterations=300, tolerance=1e-2{target}))


@pipeline.step(outputs=["y1"])
def discipline_1(y1: float, y2: float) -> float:
    return 0.95 * y1 + 0.05 * y2        # settles slowly


@pipeline.step(outputs=["y2"])
def discipline_2(y1: float) -> float:
    return 10.0 + 0.0001 * y1           # barely moves at all


pipeline.run(y1=0.0, y2=10.0)
'''


def run_translation_drift_demo():
    configure_logging(level=logging.CRITICAL)
    workspace = pathlib.Path(tempfile.mkdtemp(prefix="smartmdao_drift_"))

    faithful = workspace / "faithful.py"
    faithful.write_text(
        MODEL.format(
            label="FAITHFUL: preserves the original loop's rule - watch y2 only.",
            target=', target_var="y2"',
        )
    )

    idiomatic = workspace / "idiomatic.py"
    idiomatic.write_text(
        MODEL.format(
            label="IDIOMATIC: the default rule - watch every produced variable.",
            target="",
        )
    )

    # ==========================================================================
    # CASE 1: both look right, and both report success
    # ==========================================================================
    print("=== CASE 1: two translations of one model ===")
    print("  Same disciplines. Same physics. Same inputs. The ONLY difference is")
    print("  which variable the solver watches to decide it has finished.")
    print()

    result = compare_runs(str(faithful), str(idiomatic))

    for label, name in (("a", "faithful "), ("b", "idiomatic")):
        run = result["runs"][label]
        print(f"  {name}: converged={run['converged']}  "
              f"after {run['iterations'][0]} sweeps")
    print()
    print("  -> BOTH report success. Neither raised. Neither is structurally")
    print("     invalid. A reviewer reading either file would find nothing wrong.")

    # ==========================================================================
    # CASE 2: the answers are not the same
    # ==========================================================================
    print("\n=== CASE 2: ...and they disagree ===")
    print(f"  match      : {result['match']}")
    print(f"  identical  : {result['identical']} of "
          f"{result['identical'] + len(result['differences'])} variables")
    print()
    for difference in result["differences"]:
        print(f"  {difference['variable']:<4} faithful = {difference['a']:.6f}")
        print(f"       idiomatic = {difference['b']:.6f}")
        print(f"       relative  = {difference['relative']:.1%}")
    print()
    print("  -> y1 is out by about 95%. The faithful version stopped after one")
    print("     sweep because y2 had barely moved; the idiomatic one kept going")
    print("     until y1 settled too. Same code, same inputs, different answer.")

    # ==========================================================================
    # CASE 3: what it looks like when a translation IS faithful
    # ==========================================================================
    print("\n=== CASE 3: the same file compared with itself ===")
    same = compare_runs(str(faithful), str(faithful))
    print(f"  match              : {same['match']}")
    print(f"  identical variables: {same['identical']}")
    print(f"  tolerance          : {same['tolerance']}")
    print()
    print("  -> This is what you want to see after a refactor. Numbers compare")
    print("     within a tolerance, so a different iteration count is absorbed;")
    print("     anything non-numeric compares exactly, because there is no")
    print("     'nearly' for a frozenset of decisions.")

    # ==========================================================================
    # CASE 4: a different destination is a difference too
    # ==========================================================================
    print("\n=== CASE 4: converged is part of the answer ===")
    starved = workspace / "starved.py"
    starved.write_text(
        MODEL.format(label="Same rule, not enough iterations.", target="")
        .replace("max_iterations=300", "max_iterations=2")
    )

    result = compare_runs(str(idiomatic), str(starved))
    print(f"  convergence_differs : {result['convergence_differs']}")
    print(f"  statuses            : a={result['runs']['a']['statuses']} "
          f"b={result['runs']['b']['statuses']}")
    print(f"  match               : {result['match']}")
    print()
    print("  -> Even where the numbers happen to land close, one side never")
    print("     converged. That is a difference, and it is reported as one.")

    print("\n" + "=" * 70)
    print("The point: every other tool in this connector reads structure.")
    print("This one is the only thing that can tell you a translation changed")
    print("the answer - because both versions are perfectly well-formed.")


if __name__ == "__main__":
    run_translation_drift_demo()
