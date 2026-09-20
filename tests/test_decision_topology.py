"""
Where a discrete decision sits relative to a feedback loop.

docs/design/002 concluded that a decision-making step belongs on the **linear
part**, with an explicit outer loop, and that putting it inside the cycle is the
exception. Phase 4.2 showed an ASP discipline inside a cycle *runs*; that is a
statement about plumbing, not about whether it is sound.

What is tested here is that `validate()` says so - statically, with nothing
executed - and that the recommended topology comes back clean.
"""
import pathlib

import pytest

from smartmdao import (
    Bands,
    Discretisation,
    HybridSolver,
    Pipeline,
    RuleDiscipline,
    analyze,
    validate,
)

SPAR_ON_BAND = """
spar(aluminium) :- mass_band(light).
spar(cfrp)      :- mass_band(heavy).
#show spar/1.
"""

SPAR_ON_PRIOR = """
spar(aluminium) :- prior_mass_band(light).
spar(cfrp)      :- prior_mass_band(heavy).
#show spar/1.
"""


@pytest.fixture
def program(tmp_path):
    def write(source, name="spar.lp"):
        path = tmp_path / name
        path.write_text(source)
        return path

    return write


def codes(pipeline, inputs):
    return [finding.code for finding in validate(pipeline, inputs=inputs)]


def topology_a(program_path, fact):
    """The decision inside the cycle: it reads what the loop is still settling."""
    pipeline = Pipeline(
        solver=HybridSolver(),
        discretisation=Discretisation(
            mass_band=Bands("total_mass_kg", edges=[500], names=["light", "heavy"]),
        ),
    )
    pipeline.add(
        RuleDiscipline(
            program_path, facts=[fact], produces="decisions", name="choose_spar"
        )
    )

    @pipeline.step(outputs=["structure_mass_kg"])
    def size_structure(decisions: frozenset) -> float:
        return 200.0 if "spar(aluminium)" in decisions else 260.0

    @pipeline.step(outputs=["total_mass_kg"])
    def sum_masses(structure_mass_kg: float, payload_kg: float) -> float:
        return structure_mass_kg + payload_kg

    return pipeline


# ==============================================================================
# The decision inside the cycle
# ==============================================================================

def test_rules_inside_a_cycle_are_reported(program):
    pipeline = topology_a(program(SPAR_ON_BAND), fact="mass_band")

    findings = validate(pipeline, inputs=["payload_kg"])
    rules = [f for f in findings if f.code == "rules-in-cycle"]

    assert len(rules) == 1
    assert rules[0].step == "choose_spar"
    assert rules[0].severity == "warning"
    assert "have not settled yet" in rules[0].message
    assert "spar.lp" in rules[0].message


def test_a_band_inside_a_cycle_is_reported(program):
    """The 4.1 bistability trap, caught statically this time."""
    pipeline = topology_a(program(SPAR_ON_BAND), fact="mass_band")

    band = [
        f
        for f in validate(pipeline, inputs=["payload_kg"])
        if f.code == "discretisation-in-cycle"
    ]

    assert len(band) == 1
    assert band[0].variable == "mass_band"
    assert "more than one place" in band[0].message


def test_the_findings_are_warnings_not_errors(program):
    """
    003 says inform the engineer, do not decide for them. 002 finds the topology
    defensible when the choice genuinely must react to intermediate state.
    """
    pipeline = topology_a(program(SPAR_ON_BAND), fact="mass_band")

    topology = [
        f
        for f in validate(pipeline, inputs=["payload_kg"])
        if f.code in ("rules-in-cycle", "discretisation-in-cycle")
    ]
    assert len(topology) == 2
    assert all(f.severity == "warning" for f in topology)


def test_nothing_is_executed_to_produce_them(program, monkeypatch):
    """
    The whole point of catching this statically. If `validate` ever grounds a
    program to answer the question, this fails.
    """
    import smartmdao.rules as rules_module

    def explode():  # pragma: no cover - the test passes by never calling it
        raise AssertionError("validate() must not import or invoke clingo")

    monkeypatch.setattr(rules_module, "_load_clingo", explode)

    pipeline = topology_a(program(SPAR_ON_BAND), fact="mass_band")
    assert "rules-in-cycle" in codes(pipeline, inputs=["payload_kg"])


# ==============================================================================
# The recommended topology
# ==============================================================================

def test_the_recommended_topology_has_no_cycle_and_no_warning(program):
    """
    002's B+D: the rules read a differently-named input, so no edge runs back
    into the loop. The decision is taken on a converged result by an outer loop
    the engineer writes.
    """
    pipeline = topology_a(program(SPAR_ON_PRIOR), fact="prior_mass_band")
    inputs = ["payload_kg", "prior_mass_band"]

    analysis = analyze(pipeline, inputs=inputs)
    assert analysis.cycles == ()

    reported = codes(pipeline, inputs=inputs)
    assert "rules-in-cycle" not in reported
    assert "discretisation-in-cycle" not in reported


def test_naming_a_fact_after_the_loops_own_variable_collapses_it_into_the_cycle(program):
    """
    002 pins this mechanic for a model step; it applies verbatim to `facts`.
    One character of difference in a name decides the topology, silently.
    """
    inside = topology_a(program(SPAR_ON_BAND), fact="mass_band")
    outside = topology_a(program(SPAR_ON_PRIOR), fact="prior_mass_band")

    assert analyze(inside, inputs=["payload_kg"]).cycles != ()
    assert analyze(outside, inputs=["payload_kg", "prior_mass_band"]).cycles == ()


def test_a_terminal_band_is_information_not_a_warning(program):
    """
    In the recommended topology the band is read from the result by the caller,
    so nothing inside the pipeline consumes it. An orphaned band looks exactly
    the same from here - so saying "warning" would fire on the pattern we
    recommend, which is how a finding list teaches people to ignore it.
    """
    pipeline = topology_a(program(SPAR_ON_PRIOR), fact="prior_mass_band")

    unused = [
        f
        for f in validate(pipeline, inputs=["payload_kg", "prior_mass_band"])
        if f.code == "discretisation-unused"
    ]
    assert len(unused) == 1
    assert unused[0].severity == "info"
    assert "perfectly correct if you read it from the result" in unused[0].message


# ==============================================================================
# Not every step in a cycle is a decision
# ==============================================================================

def test_an_ordinary_cycle_is_not_reported():
    """A purely numeric loop is what the solver is for; no finding belongs here."""
    pipeline = Pipeline(solver=HybridSolver())

    @pipeline.step(outputs=["y1"])
    def discipline_1(z: float, y2: float) -> float:
        return z**2 - 0.2 * y2

    @pipeline.step(outputs=["y2"])
    def discipline_2(y1: float) -> float:
        return abs(y1) ** 0.5

    reported = codes(pipeline, inputs=["z", "y2"])
    assert "rules-in-cycle" not in reported
    assert "discretisation-in-cycle" not in reported


def test_a_band_outside_any_cycle_is_not_reported():
    pipeline = Pipeline(
        discretisation=Discretisation(
            mass_band=Bands("mass_kg", edges=[800], names=["light", "heavy"]),
        ),
    )

    @pipeline.step(outputs=["mass_kg"])
    def size_airframe(span_m: float) -> float:
        return 120.0 * span_m

    @pipeline.step(outputs=["margin"])
    def pick_margin(mass_band: str) -> float:
        return 0.05 if mass_band == "light" else 0.20

    assert codes(pipeline, inputs=["span_m"]) == []
