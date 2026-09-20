"""
What solving costs, and what the budget does and does not protect you from.

The honest boundary is the point of most of this file. Grounding is the
worst-case-exponential half, and it cannot be preempted in-process - verified
against clingo 5.8.2, where `interrupt()` during `ground()` is ignored and
grounding runs to completion. So the budget bounds *search*, and the docs say
so rather than letting the word "budget" imply more.
"""
import pytest

from smartmdao import (
    AmbiguousProgramError,
    Pipeline,
    RuleBudgetExceeded,
    RuleCost,
    RuleDiscipline,
    RuleProgramError,
    validate,
)
from smartmdao.rules import pinning_concern

PINNED = """
material(aluminium; cfrp).
1 { spar(M) : material(M) } 1.
:- spar(aluminium), mass_band(heavy).
cost(aluminium, 1). cost(cfrp, 3).
#minimize { C@1,M : spar(M), cost(M,C) }.
rank(aluminium,1). rank(cfrp,2).
#minimize { R@0,M : spar(M), rank(M,R) }.
#show spar/1.
"""

# Cheap to ground, expensive to search: the pigeonhole principle.
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


def discipline(path, **kwargs):
    kwargs.setdefault("facts", ["mass_band"])
    kwargs.setdefault("produces", "decisions")
    return RuleDiscipline(path, **kwargs)


# ==============================================================================
# Cost
# ==============================================================================

def test_cost_starts_at_zero(program):
    rules = discipline(program(PINNED))

    assert rules.cost == RuleCost()
    assert rules.cost.total_seconds == 0.0
    assert rules.cost.projected_seconds(100) == 0.0


def test_solving_records_what_it_cost(program):
    rules = discipline(program(PINNED))
    rules.solve(mass_band="light")

    assert rules.cost.calls == 1
    assert rules.cost.grounds == 1
    assert rules.cost.cache_hits == 0
    assert rules.cost.ground_seconds > 0.0
    assert rules.cost.total_seconds >= rules.cost.ground_seconds


def test_a_projection_is_the_measured_unit_times_the_sweeps(program):
    rules = discipline(program(PINNED))
    rules.solve(mass_band="light")

    unit = rules.cost.total_seconds
    assert rules.cost.projected_seconds(10) == pytest.approx(unit * 10)


def test_cost_renders_for_a_human(program):
    rules = discipline(program(PINNED))
    rules.solve(mass_band="light")
    rules.solve(mass_band="light")

    rendered = str(rules.cost)
    assert "2 call(s), 1 from cache" in rendered
    assert "grounding" in rendered


# ==============================================================================
# Memoisation
# ==============================================================================

def test_repeating_the_facts_does_not_reground(program):
    """
    Exact, not approximate: the program is fixed and clingo is deterministic,
    so the same facts cannot give a different answer. This is what makes
    re-grounding once per sweep survivable inside a converging loop.
    """
    rules = discipline(program(PINNED))

    first = rules.solve(mass_band="light")
    second = rules.solve(mass_band="light")

    assert first == second
    assert rules.cost.calls == 2
    assert rules.cost.grounds == 1
    assert rules.cost.cache_hits == 1


def test_different_facts_are_solved_separately(program):
    rules = discipline(program(PINNED))

    assert rules.solve(mass_band="light") != rules.solve(mass_band="heavy")
    assert rules.cost.grounds == 2
    assert rules.cost.cache_hits == 0


def test_memoisation_can_be_turned_off(program):
    rules = discipline(program(PINNED), memoise=False)

    rules.solve(mass_band="light")
    rules.solve(mass_band="light")

    assert rules.cost.grounds == 2
    assert rules.cost.cache_hits == 0


def test_an_infeasible_answer_is_cached_too(program):
    source = PINNED + "\n:- spar(cfrp), mass_band(heavy).\n"
    rules = discipline(program(source))

    from smartmdao import INFEASIBLE

    assert rules.solve(mass_band="heavy") is INFEASIBLE
    assert rules.solve(mass_band="heavy") is INFEASIBLE
    assert rules.cost.grounds == 1
    assert rules.cost.cache_hits == 1


# ==============================================================================
# The budget
# ==============================================================================

def test_a_budget_must_be_positive(program):
    with pytest.raises(RuleProgramError, match="must be positive"):
        discipline(program(PINNED), budget_seconds=0)


def test_no_budget_is_allowed_but_must_be_asked_for(program):
    """The default is a number, because unbounded should never be accidental."""
    assert discipline(program(PINNED)).budget_seconds == 10.0
    assert discipline(program(PINNED), budget_seconds=None).budget_seconds is None


def test_a_hard_search_is_stopped_by_the_budget(program):
    rules = discipline(program(HARD_SEARCH), budget_seconds=0.5)

    with pytest.raises(RuleBudgetExceeded) as caught:
        rules.solve(mass_band="light")

    assert "NOT the same as UNSAT" in str(caught.value)
    assert "0.5s budget" in str(caught.value)


def test_exceeding_the_budget_is_not_infeasible(program):
    """
    The distinction that matters. UNSAT is a proof that no model exists; a
    timeout proves nothing at all. Returning INFEASIBLE here would turn "we gave
    up" into "your architecture is impossible".
    """
    from smartmdao import INFEASIBLE

    rules = discipline(program(HARD_SEARCH), budget_seconds=0.5)

    with pytest.raises(RuleBudgetExceeded) as caught:
        rules.solve(mass_band="light")

    assert caught.value is not INFEASIBLE
    assert isinstance(caught.value, RuleProgramError)


def test_a_timed_out_solve_still_records_its_cost(program):
    """Reporting what actually happened, including the failure."""
    rules = discipline(program(HARD_SEARCH), budget_seconds=0.5)

    with pytest.raises(RuleBudgetExceeded):
        rules.solve(mass_band="light")

    assert rules.cost.calls == 1
    assert rules.cost.solve_seconds >= 0.4


def test_an_easy_program_finishes_well_inside_its_budget(program):
    rules = discipline(program(PINNED), budget_seconds=30.0)
    assert rules.solve(mass_band="light") == frozenset({"spar(aluminium)"})


# ==============================================================================
# unpinned-program
# ==============================================================================

@pytest.mark.parametrize(
    "source, fragment",
    [
        ("1 { a; b } 1.", "states no #minimize"),
        ("1 { a; b } 1.\n#minimize { 1@0,X : sel(X) }.", "weight in it is a constant"),
        ("{ a }.\n#maximize { 2,X : sel(X) }.", "weight in it is a constant"),
    ],
)
def test_a_program_that_cannot_pin_itself_is_described(source, fragment):
    assert fragment in pinning_concern(source)


@pytest.mark.parametrize(
    "source",
    [
        PINNED,
        "spar(aluminium) :- mass_band(light).",          # no choice rule at all
        "1 { a; b } 1.\n#minimize { R@0,X : rank(X,R) }.",
        "% 1 { a; b } 1.\nspar(al) :- band(light).",     # the choice is a comment
    ],
)
def test_a_pinned_or_deterministic_program_is_clean(source):
    assert pinning_concern(source) is None


def test_an_empty_optimisation_element_is_skipped_not_misread():
    """A stray `;` leaves an empty element; it is not a constant weight."""
    assert pinning_concern("1 { a; b } 1.\n#minimize { ; R,X : rank(X,R) }.") is None
    assert "constant" in pinning_concern("1 { a; b } 1.\n#minimize { ; 1,X : sel(X) }.")


def test_solving_without_a_budget_arms_no_watchdog(program):
    """`budget_seconds=None` is allowed; it just has to be asked for."""
    rules = discipline(program(PINNED), budget_seconds=None)

    assert rules.solve(mass_band="heavy") == frozenset({"spar(cfrp)"})
    assert rules.cost.grounds == 1


def test_the_shipped_example_program_is_pinned():
    """The file this repository tells people to read had better pass."""
    import pathlib

    program = (
        pathlib.Path(__file__).resolve().parents[1]
        / "scripts"
        / "programs"
        / "wing_architecture.lp"
    )
    assert pinning_concern(program.read_text()) is None


def test_validate_reports_an_unpinned_program(program):
    pipeline = Pipeline()
    pipeline.add(
        discipline(program("1 { spar(a); spar(b) } 1.\nseen(B) :- mass_band(B).\n"))
    )

    @pipeline.step(outputs=["x"])
    def use(decisions: frozenset) -> float:
        return 1.0

    findings = [f for f in validate(pipeline, inputs=["mass_band"])
                if f.code == "unpinned-program"]

    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert "syntactic check rather than a proof" in findings[0].message


def test_validate_is_quiet_about_a_pinned_program(program):
    pipeline = Pipeline()
    pipeline.add(discipline(program(PINNED)))

    @pipeline.step(outputs=["x"])
    def use(decisions: frozenset) -> float:
        return 1.0

    codes = [f.code for f in validate(pipeline, inputs=["mass_band"])]
    assert "unpinned-program" not in codes


def test_the_static_check_agrees_with_what_solving_does(program):
    """
    The two halves should not contradict each other: a program the heuristic
    calls unpinned is one solving refuses to answer.
    """
    loose = program("1 { spar(a); spar(b) } 1.\nseen(B) :- mass_band(B).\n#show spar/1.\n")
    rules = discipline(loose)

    assert rules.pinning_concern is not None
    with pytest.raises(AmbiguousProgramError):
        rules.solve(mass_band="light")
