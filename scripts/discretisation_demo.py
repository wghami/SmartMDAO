"""
Where a number becomes a fact, and why that line belongs in the open.

An architecture rule reasons about `mass_band = "heavy"`. A structural model
computes `mass_kg = 880.0`. Something has to turn one into the other, and that
something is a threshold - a hypothesis that decides the answer and usually
lives in a helper function nobody reviews.

This script shows four things, in the order they hurt:

  1. The threshold does its job, and `explain()` states it.
  2. Moving it by 1% changes the answer, with nothing warning you.
  3. `validate()` catches the declarations that cannot work.
  4. A threshold inside a feedback loop gives the loop TWO answers, both
     converged, chosen by the initial guess alone.

Run it. The fourth one is the reason this layer is declared rather than glued.
"""
from smartmdao import (
    Bands,
    Discretisation,
    DiscretisationError,
    HybridSolver,
    Pipeline,
    analyze,
    explain,
    validate,
)


def rule(text):
    print(f"\n{text}")
    print("=" * len(text))


# ==============================================================================
# 1. A declared threshold
# ==============================================================================

def build_sizing_pipeline(cutover_kg: float) -> Pipeline:
    """A wing whose safety margin depends on which mass band it lands in."""
    pipeline = Pipeline(
        discretisation=Discretisation(
            mass_band=Bands(
                "mass_kg",
                edges=[cutover_kg],
                names=["light", "heavy"],
            ),
        ),
    )

    @pipeline.step(outputs=["mass_kg"])
    def size_airframe(span_m: float) -> float:
        return 120.0 * span_m

    @pipeline.step(outputs=["margin"])
    def pick_margin(mass_band: str) -> float:
        # A rule an engineer wrote: heavier airframes carry more reserve.
        return 0.05 if mass_band == "light" else 0.20

    @pipeline.step(outputs=["certified_mass_kg"])
    def apply_margin(mass_kg: float, margin: float) -> float:
        return mass_kg * (1.0 + margin)

    return pipeline


rule("1. The threshold is part of the pipeline, not hidden in a helper")

pipeline = build_sizing_pipeline(cutover_kg=800.0)
result = pipeline.run(span_m=6.6)

print(f"span 6.6 m -> mass {result['mass_kg']:.1f} kg")
print(f"           -> band {result['mass_band']!r}")
print(f"           -> margin {result['margin']:.0%}")
print(f"           -> certified {result['certified_mass_kg']:.1f} kg")

print("\n-> The band is an ordinary step. The solver ordered it:")
print(f"   {' -> '.join(analyze(pipeline, inputs=['span_m']).execution_order)}")

print("\n-> And explain() states the threshold, so a reviewer sees it:")
for line in explain(pipeline, inputs=["span_m"]).splitlines():
    if "mass_kg ->" in line or "edge values" in line:
        print(f"   {line.strip()}")


# ==============================================================================
# 2. The same model, one threshold moved
# ==============================================================================

rule("2. Move the threshold 1%, get a different answer, hear nothing")

span = 6.7
certified = {}
for cutover in (800.0, 808.0):
    run = build_sizing_pipeline(cutover_kg=cutover).run(span_m=span)
    certified[cutover] = run["certified_mass_kg"]
    print(
        f"cutover {cutover:6.1f} kg -> mass {run['mass_kg']:.1f} kg, "
        f"band {run['mass_band']:>5}, certified {run['certified_mass_kg']:7.1f} kg"
    )

shift = (max(certified.values()) / min(certified.values())) - 1.0
print(
    f"\n-> Same disciplines, same span, same arithmetic. The certified mass "
    f"moved by {shift:.0%}"
)
print(f"   because the threshold moved by 1%, and {120.0 * span:.0f} kg sits")
print("   between the two. Nothing in either run is a warning.")
print("   That is the whole argument for declaring the threshold: you cannot")
print("   review a number you cannot see.")


# ==============================================================================
# 3. What validate() refuses, and what it merely reports
# ==============================================================================

rule("3. Incoherent declarations raise; questionable ones are reported")

for description, kwargs in [
    ("two names for three intervals", dict(edges=[800, 1200], names=["a", "b"])),
    ("edges that do not ascend", dict(edges=[1200, 800], names=["a", "b", "c"])),
    ("a band no value can reach", dict(edges=[800, 800], names=["a", "b", "c"])),
]:
    try:
        Bands("mass_kg", **kwargs)
    except DiscretisationError as error:
        first_sentence = str(error).split(".")[0]
        print(f"raises  {description:32} {first_sentence}.")

print()

orphan = Pipeline(
    discretisation=Discretisation(
        mass_band=Bands("mass_kg", edges=[800], names=["light", "heavy"]),
    ),
)


@orphan.step(outputs=["mass_kg"])
def size_airframe(span_m: float) -> float:
    return 120.0 * span_m


for finding in validate(orphan, inputs=["span_m"]):
    print(f"reports {finding.code:32} {finding.message}")

typo = Pipeline(
    discretisation=Discretisation(
        mass_band=Bands("mass_kgs", edges=[800], names=["light", "heavy"]),
    ),
)


@typo.step(outputs=["margin"])
def pick_margin(mass_band: str) -> float:
    return 0.05


print()
for finding in validate(typo):
    print(f"reports {finding.code:32} {finding.message}")

print("\n-> The typo is caught by `missing-input`, not by a discretisation check.")
print("   A band is modelled as a real step, so the general checks already see")
print("   it - which is why this feature added two findings instead of four.")


# ==============================================================================
# 4. A threshold inside a loop: two answers, both converged
# ==============================================================================

rule("4. The one that should worry you")


def build_mass_loop() -> Pipeline:
    """Structure mass depends on the band; the band depends on total mass."""
    loop = Pipeline(
        solver=HybridSolver(),
        discretisation=Discretisation(
            mass_band=Bands("total_mass_kg", edges=[500], names=["light", "heavy"]),
        ),
    )

    @loop.step(outputs=["structure_mass_kg"])
    def size_structure(mass_band: str) -> float:
        return 200.0 if mass_band == "light" else 260.0

    @loop.step(outputs=["total_mass_kg"])
    def sum_masses(structure_mass_kg: float, payload_kg: float) -> float:
        return structure_mass_kg + payload_kg

    return loop


loop = build_mass_loop()
analysis = analyze(loop, inputs=["payload_kg"])

print(f"cycle: {' -> '.join(analysis.cycles[0].steps)}")
print(
    "needs an initial guess for: "
    f"{', '.join(g.variable for g in analysis.initial_guesses_required)}"
)
print("\n-> Note which step sorts first. `discretise_mass_band` beats both")
print("   disciplines alphabetically, so the synthetic step's input is the one")
print("   needing a seed. Renaming the band would change that. Ask analyze().")

print()
for seed in (400.0, 900.0):
    run = build_mass_loop().run(payload_kg=250.0, total_mass_kg=seed)
    report = run["convergence_reports"][0]
    print(
        f"seed {seed:6.1f} kg -> band {run['mass_band']:>5}, "
        f"total {run['total_mass_kg']:6.1f} kg, "
        f"status {report.status} in {report.iterations} iteration(s)"
    )

print("\n-> Two different answers. Both converged. Both self-consistent:")
print("   450 kg really is light, and 510 kg really is heavy, so neither run")
print("   is wrong. The initial guess alone decided which fixed point you got,")
print("   and nothing in the result says the other one exists.")
print()
print("   This is answer-set multiplicity arriving on the NUMERIC side, before")
print("   any rules engine is involved: a threshold inside a loop makes the")
print("   seed load-bearing. docs/design/003 asks for multiplicity to be a")
print("   finding rather than a detail - this demo is the evidence that the")
print("   requirement is not specific to ASP.")
print()
print("   Not yet detected. Recorded in docs/known-issues.md.")
