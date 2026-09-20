"""
Why a set of facts has no answer.

docs/design/003 promises that UNSAT gives a machine-checkable "why not" an
engineer can take into a design review. docs/design/004 then recorded the
mechanism as a *risk*, having found that clingo's own core does not map cleanly
back onto the facts. It was right, and for a sharper reason than it gave: the
core is also not minimal, so it names facts that had nothing to do with the
contradiction.

What is tested here is the replacement - dropping one fact at a time - and
specifically that it is **minimal**, since an explanation that implicates an
innocent constraint is worse than no explanation at all.
"""
import pytest

from smartmdao import (
    INFEASIBLE,
    Conflict,
    RuleBudgetExceeded,
    RuleDiscipline,
    RuleProgramError,
)

# Aluminium is out when heavy, CFRP is out under Part 25 - so heavy + Part 25
# has no spar at all. `site` is along for the ride and part of nothing.
TWO_WAY = """
material(aluminium; cfrp).
1 { spar(M) : material(M) } 1.
:- spar(aluminium), mass_band(heavy).
:- spar(cfrp), certification(part25).
rank(aluminium,1). rank(cfrp,2).
#minimize { R,M : spar(M), rank(M,R) }.
#show spar/1.
"""

SELF_CONTRADICTORY = """
:- not spar(x).
:- spar(x).
seen(B) :- mass_band(B).
#show spar/1.
"""

HARD_SEARCH = """
#const n=13.
pigeon(1..n+1). hole(1..n).
1 { in(P,H) : hole(H) } 1 :- pigeon(P).
:- in(P1,H), in(P2,H), P1 < P2.
seen(B) :- mass_band(B).
#show in/2.
"""


@pytest.fixture
def program(tmp_path):
    def write(source, name="wing.lp"):
        path = tmp_path / name
        path.write_text(source)
        return path

    return write


def three_fact_discipline(path, **kwargs):
    return RuleDiscipline(
        path,
        facts=["mass_band", "certification", "site"],
        produces="decisions",
        **kwargs,
    )


# ==============================================================================
# The conflict itself
# ==============================================================================

def test_the_conflict_names_only_the_facts_that_cause_it(program):
    """
    The property that matters. clingo's own core returned all three facts on
    exactly this program, including `site`, which no rule mentions.
    """
    rules = three_fact_discipline(program(TWO_WAY))

    conflict = rules.explain_infeasible(
        mass_band="heavy", certification="part25", site="toulouse"
    )

    assert conflict.facts == {"mass_band": "heavy", "certification": "part25"}
    assert "site" not in conflict.facts
    assert conflict.rules_alone is False


def test_the_conflict_is_minimal_in_the_strong_sense(program):
    """Remove any one of the named facts and the rules become satisfiable."""
    rules = three_fact_discipline(program(TWO_WAY))
    conflict = rules.explain_infeasible(
        mass_band="heavy", certification="part25", site="toulouse"
    )

    # Dropping either half of the conflict leaves an answer, which is what
    # makes "these two cannot hold together" a defensible claim.
    assert rules.solve(
        mass_band="light", certification="part25", site="toulouse"
    ) == frozenset({"spar(aluminium)"})
    assert rules.solve(
        mass_band="heavy", certification="part23", site="toulouse"
    ) == frozenset({"spar(cfrp)"})
    assert len(conflict.facts) == 2


def test_rules_that_contradict_themselves_say_so(program):
    """
    A different answer, and a more useful one: no input could have worked, so
    the engineer should be reading the program rather than their requirements.
    """
    rules = RuleDiscipline(
        program(SELF_CONTRADICTORY), facts=["mass_band"], produces="decisions"
    )

    conflict = rules.explain_infeasible(mass_band="light")

    assert conflict.rules_alone is True
    assert conflict.facts == {}
    assert "unsatisfiable on their own" in str(conflict)


def test_the_explanation_reads_as_a_sentence(program):
    rules = three_fact_discipline(program(TWO_WAY))
    conflict = rules.explain_infeasible(
        mass_band="heavy", certification="part25", site="toulouse"
    )

    rendered = str(conflict)
    assert "mass_band(heavy)" in rendered
    assert "certification(part25)" in rendered
    assert "Removing any one of them" in rendered


def test_it_reports_what_the_explanation_cost(program):
    """One solve to confirm, then one per fact."""
    rules = three_fact_discipline(program(TWO_WAY))

    conflict = rules.explain_infeasible(
        mass_band="heavy", certification="part25", site="toulouse"
    )
    assert conflict.solves == 4
    assert isinstance(conflict, Conflict)


# ==============================================================================
# Misuse, and cost
# ==============================================================================

def test_explaining_a_satisfiable_case_is_refused(program):
    rules = three_fact_discipline(program(TWO_WAY))

    with pytest.raises(RuleProgramError, match="no conflict to explain"):
        rules.explain_infeasible(
            mass_band="light", certification="part23", site="toulouse"
        )


def test_a_missing_fact_is_named(program):
    rules = three_fact_discipline(program(TWO_WAY))

    with pytest.raises(RuleProgramError, match=r"missing fact\(s\) \['site'\]"):
        rules.explain_infeasible(mass_band="heavy", certification="part25")


def test_explaining_is_not_done_automatically(program):
    """
    `solve` stays cheap. Charging every sweep of a convergence loop for an
    explanation nobody asked for is exactly what the cost ladder exists to
    prevent.
    """
    rules = three_fact_discipline(program(TWO_WAY))

    assert rules.solve(
        mass_band="heavy", certification="part25", site="toulouse"
    ) is INFEASIBLE
    assert rules.cost.grounds == 1          # one, not four


def test_the_explanation_respects_the_budget(program):
    rules = RuleDiscipline(
        program(HARD_SEARCH),
        facts=["mass_band"],
        produces="decisions",
        budget_seconds=0.5,
    )

    with pytest.raises(RuleBudgetExceeded, match="one solve per fact"):
        rules.explain_infeasible(mass_band="light")


def test_explaining_adds_to_the_measured_cost(program):
    rules = three_fact_discipline(program(TWO_WAY))
    rules.explain_infeasible(
        mass_band="heavy", certification="part25", site="toulouse"
    )

    assert rules.cost.grounds == 4
    assert rules.cost.total_seconds > 0.0


def test_clingo_diagnostics_do_not_reach_stderr(program, capfd):
    """
    An injected fact never occurs in a rule head, so clingo warns about every
    one of them. Left on stderr, a correct program looks alarming once per
    sweep.
    """
    rules = three_fact_discipline(program(TWO_WAY))
    rules.solve(mass_band="light", certification="part23", site="toulouse")

    captured = capfd.readouterr()
    assert "does not occur in any rule head" not in captured.err
    assert captured.err == ""
