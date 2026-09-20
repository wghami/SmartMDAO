"""
A discipline whose behaviour is a reviewed file of rules, not a function.

docs/design/002 showed a language model *can* sit inside an MDA feedback loop.
docs/design/003 rejected it as a production path for one reason: you cannot
reproduce the run, and you cannot review a prompt the way you review an
equation. The answer is to move the model out of the loop entirely - it writes
an ASP program once, at authoring time, a human reviews THAT, and the program
becomes the discipline.

This script shows the things that argument turns on:

  1. The rules are a file. You can read it, diff it, and it is what runs.
  2. UNSAT is a proof that no architecture works - the honest INFEASIBLE.
  3. A program that does not pin its own answer REFUSES rather than picks.
  4. No number crosses into the rules; a threshold has to be declared first.
  5. All of it wired end to end, ordered by the solver.
  6. WHERE you put the decision - the one that matters most, and the one a
     working example will not teach you, because the wrong topology works too.

The program is scripts/programs/wing_architecture.lp. Read it alongside this.
"""
import pathlib
import tempfile

from smartmdao import (
    INFEASIBLE,
    AmbiguousProgramError,
    Bands,
    Discretisation,
    HybridSolver,
    Pipeline,
    RuleDiscipline,
    RuleProgramError,
    analyze,
    validate,
)

PROGRAM = pathlib.Path(__file__).parent / "programs" / "wing_architecture.lp"


def rule(text):
    print(f"\n{text}")
    print("=" * len(text))


# ==============================================================================
# 1. The rules are a file, and the pipeline treats them as one step
# ==============================================================================

rule("1. The discipline is a file you can review")

print(f"program: {PROGRAM.relative_to(pathlib.Path(__file__).parents[1])}")
print(f"         {len(PROGRAM.read_text().splitlines())} lines, "
      f"{sum(1 for line in PROGRAM.read_text().splitlines() if line.strip().startswith('%'))} of them comment")

rules = RuleDiscipline(
    PROGRAM,
    facts=["mass_band", "certification"],
    produces="decisions",
)

print(f"\nstep name: {rules.name}")
print(f"facts in:  {', '.join(rules.facts)}")
print(f"produces:  {rules.produces}")

print("\n-> Every combination of facts, solved by clingo. Same inputs, same")
print("   answer, today and next year - there is no model call at run time.\n")

for mass_band in ("light", "heavy"):
    for certification in ("part23", "part25"):
        answer = rules.solve(mass_band=mass_band, certification=certification)
        rendered = "INFEASIBLE" if answer is INFEASIBLE else ", ".join(sorted(answer))
        print(f"   {mass_band:6} {certification:7} -> {rendered}")


# ==============================================================================
# 2. UNSAT is the honest INFEASIBLE
# ==============================================================================

rule("2. 'No feasible architecture' is a proof, not a convention")

infeasible = rules.solve(mass_band="heavy", certification="part25")

print(f"heavy + part25 -> {infeasible!r}")
print(f"is it None?     {infeasible is None}")
print(f"is it falsey?   {not infeasible}")

print("\n-> Phase 1 had to INVENT a sentinel, because a discipline must be total")
print("   and returning None stores nothing - which leaves the previous value in")
print("   place and reads as converged. Here the sentinel is not a convention:")
print("   clingo proved no model satisfies the constraints. Three rules fight -")
print("   aluminium cannot be heavy, CFRP is not Part 25 certified, and exactly")
print("   one spar must be chosen. That is a result an engineer can take into a")
print("   design review.")


# ==============================================================================
# 3. A program that does not pin its answer refuses to guess
# ==============================================================================

rule("3. Two equally good answers is a finding, not a detail")

TIED = """
material(aluminium; cfrp).
1 { spar(M) : material(M) } 1.
cost(aluminium, 2). cost(cfrp, 2).          % equal cost
#minimize { C,M : spar(M), cost(M,C) }.
#minimize { 1@0,M : spar(M) }.              % LOOKS like a tie-break. Is not.
seen(B) :- mass_band(B).
#show spar/1.
"""

with tempfile.TemporaryDirectory() as directory:
    tied_path = pathlib.Path(directory) / "tied.lp"
    tied_path.write_text(TIED)

    tied = RuleDiscipline(tied_path, facts=["mass_band"], produces="decisions")
    try:
        tied.solve(mass_band="light")
    except AmbiguousProgramError as error:
        print(f"raised: {type(error).__name__}")
        for index, model in enumerate(error.models, 1):
            print(f"   model {index}: {', '.join(sorted(model))}")

print("\n-> Both cost 2. Taking the first would have returned one material with")
print("   nothing anywhere saying the other was equally good, and the engineer")
print("   would have had no way to know a choice was made for them.")
print()
print("   Note WHICH tie-break this program has. `#minimize { 1@0,M : spar(M) }`")
print("   reads like a tie-break and separates nothing - the weight is the")
print("   constant 1 for every candidate. Ranking over a DISTINCT value per")
print("   candidate is what actually pins it, as wing_architecture.lp does.")
print("   That mistake was made while writing docs/design/004 and caught by")
print("   running it, which is the only reason it is documented.")


# ==============================================================================
# 4. No number crosses the boundary
# ==============================================================================

rule("4. Facts are symbols; the threshold has to be declared first")

try:
    rules.solve(mass_band=880.0, certification="part23")
except RuleProgramError as error:
    print(f"refused: {error}")

print("\n-> ASP has no floats, so injecting 880.0 would mean choosing a threshold")
print("   somewhere inside this library. That is exactly the hypothesis that")
print("   docs/design/003 calls the sharpest risk in the whole direction, and")
print("   Bands exists to make it visible. The refusal is the feature.")


# ==============================================================================
# 5. Wired together: numbers -> band -> rules -> numbers
# ==============================================================================

rule("5. The whole bridge, in one pipeline")

pipeline = Pipeline(
    discretisation=Discretisation(
        mass_band=Bands("mass_kg", edges=[800], names=["light", "heavy"]),
    ),
)


@pipeline.step(outputs=["mass_kg"])
def size_airframe(span_m: float) -> float:
    return 120.0 * span_m


pipeline.add(
    RuleDiscipline(
        PROGRAM,
        facts=["mass_band", "certification"],
        produces="decisions",
    )
)


@pipeline.step(outputs=["rib_pitch_m"])
def space_ribs(decisions: frozenset, span_m: float) -> float:
    count = 8 if "rib_count(8)" in decisions else 5
    return span_m / count


analysis = analyze(pipeline, inputs=["span_m", "certification"])
print(f"execution order: {' -> '.join(analysis.execution_order)}")
print(f"findings:        {list(validate(pipeline, inputs=['span_m', 'certification'])) or 'none'}")

print()
for span in (5.0, 7.5):
    result = pipeline.run(span_m=span, certification="part23")
    print(
        f"span {span:4.1f} m -> {result['mass_kg']:6.1f} kg "
        f"-> {result['mass_band']:6} -> {', '.join(sorted(result['decisions']))} "
        f"-> rib pitch {result['rib_pitch_m']:.2f} m"
    )

print("\n-> A float became a band, the band became a fact, the fact selected an")
print("   architecture, and the architecture fed a float back out. The solver")
print("   ordered all of it without knowing that one of those steps is a logic")
print("   program - because a rule-backed discipline is registered as an")
print("   ordinary step, exactly like a declared band is.")
print()
print("   analyze() and validate() read all of the above WITHOUT running clingo")
print("   once. Introspection never requires execution.")


# ==============================================================================
# 6. Where you put the decision matters more than whether it works
# ==============================================================================

rule("6. Inside the loop it RUNS. That is not the same as it being right")

LOOP_RULES = """
spar(aluminium) :- {fact}(light).
spar(cfrp)      :- {fact}(heavy).
#show spar/1.
"""

WORKSPACE = tempfile.mkdtemp()


def mass_loop(fact_name):
    """
    The same mass loop twice, differing only in what the rules READ.

    `mass_band`       - the loop's own variable, so the rules join the cycle.
    `prior_mass_band` - a separate input, so they sit on the linear part.
    """
    path = pathlib.Path(WORKSPACE) / f"{fact_name}.lp"
    path.write_text(LOOP_RULES.format(fact=fact_name))

    loop = Pipeline(
        solver=HybridSolver(),
        discretisation=Discretisation(
            mass_band=Bands("total_mass_kg", edges=[500], names=["light", "heavy"]),
        ),
    )
    loop.add(
        RuleDiscipline(
            path, facts=[fact_name], produces="decisions", name="choose_spar"
        )
    )

    @loop.step(outputs=["structure_mass_kg"])
    def size_structure(decisions: frozenset) -> float:
        return 200.0 if "spar(aluminium)" in decisions else 260.0

    @loop.step(outputs=["total_mass_kg"])
    def sum_masses(structure_mass_kg: float, payload_kg: float) -> float:
        return structure_mass_kg + payload_kg

    return loop


inside = mass_loop("mass_band")
outside = mass_loop("prior_mass_band")
outside_inputs = ["payload_kg", "prior_mass_band"]

print("topology A - the rules read `mass_band`, the loop's own variable:")
print(f"   cycle:    {' -> '.join(analyze(inside, inputs=['payload_kg']).cycles[0].steps)}")
for finding in validate(inside, inputs=["payload_kg"]):
    if finding.code.endswith("-in-cycle"):
        print(f"   reported: {finding.code}")

print("\ntopology B+D - the rules read `prior_mass_band`, a separate input:")
print(f"   cycles:   {analyze(outside, inputs=outside_inputs).cycles or 'none'}")
reported = [
    f.code for f in validate(outside, inputs=outside_inputs) if f.code.endswith("-in-cycle")
]
print(f"   reported: {reported or 'nothing'}")

print("\n-> ONE WORD of difference in the fact name decides the topology, and")
print("   nothing about the two rule files looks different. Naming a fact after")
print("   the loop's own variable grows an edge back into the cycle.")
print()
print("   Inside the cycle the rules are applied once per sweep, on values that")
print("   have not settled - so the architecture is chosen from an artifact of")
print("   the iteration path rather than from a result. 'At sweep 2 the mass")
print("   happened to be over 800 kg' is not a reason you can give in a design")
print("   review. And a discrete choice inside a loop can leave it oscillating,")
print("   or give it several stable answers that each report success.")
print()
print("   The version needing no warning is the one with no cycle at all:")
print("   decide, evaluate completely, revise. docs/design/002 reached that")
print("   conclusion for a language model. It holds for rules too, for a")
print("   different reason - determinism is what makes the outer loop provably")
print("   terminating, because a repeated decision set IS a cycle.")
