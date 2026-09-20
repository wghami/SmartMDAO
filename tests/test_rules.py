"""
Rule-backed disciplines: an `.lp` file wired in as a step.

The behaviour worth protecting is not "clingo works" - it is that the three
things docs/design/003 and 004 insist on actually happen: UNSAT comes back as a
total, explicit value; a program with two equally good answers refuses rather
than picks; and a float cannot be smuggled in as a fact, because converting one
would be an undeclared threshold.
"""
import pytest

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

# A pinned program: one spar material, aluminium unless the airframe is heavy,
# with an optimisation statement and a tie-break that ranks over distinct values.
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

UNSATISFIABLE = """
material(aluminium; cfrp).
1 { spar(M) : material(M) } 1.
:- spar(aluminium), mass_band(heavy).
:- spar(cfrp), mass_band(heavy).
#show spar/1.
"""

# Two materials at equal cost, and no tie-break: two proven-optimal models.
TIED = """
material(aluminium; cfrp).
1 { spar(M) : material(M) } 1.
cost(aluminium, 2). cost(cfrp, 2).
#minimize { C,M : spar(M), cost(M,C) }.
seen(B) :- mass_band(B).
#show spar/1.
"""

# No optimisation statement at all, so nothing is ever *proven* optimal.
UNOPTIMISED_MANY = """
material(aluminium; cfrp).
1 { spar(M) : material(M) } 1.
seen(B) :- mass_band(B).
#show spar/1.
"""

UNOPTIMISED_ONE = """
spar(aluminium) :- mass_band(light).
spar(cfrp) :- mass_band(heavy).
#show spar/1.
"""


@pytest.fixture
def write_program(tmp_path):
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
# Declaration
# ==============================================================================

def test_a_missing_program_fails_at_construction(tmp_path):
    with pytest.raises(RuleProgramError, match="No ASP program at"):
        discipline(tmp_path / "absent.lp")


def test_an_empty_program_is_refused(write_program):
    with pytest.raises(RuleProgramError, match="is empty"):
        discipline(write_program("   \n  "))


def test_a_program_with_no_facts_could_not_respond_to_the_pipeline(write_program):
    with pytest.raises(RuleProgramError, match="no facts"):
        discipline(write_program(PINNED), facts=[])


def test_duplicate_facts_are_refused(write_program):
    with pytest.raises(RuleProgramError, match=r"more than\s+once"):
        discipline(write_program(PINNED), facts=["mass_band", "mass_band"])


def test_a_step_cannot_produce_one_of_its_own_facts(write_program):
    with pytest.raises(RuleProgramError, match="overwrite its own input"):
        discipline(write_program(PINNED), produces="mass_band")


def test_the_step_name_defaults_to_the_program_stem(write_program):
    assert discipline(write_program(PINNED, "spar_choice.lp")).name == "rules_spar_choice"


def test_the_step_name_can_be_set_because_it_decides_seeding(write_program):
    """Inside a cycle, the alphabetically-first step's input needs the seed."""
    rules = discipline(write_program(PINNED), name="zz_choose_spar")
    assert rules.name == "zz_choose_spar"
    assert rules.as_step().name == "zz_choose_spar"


# ==============================================================================
# Solving
# ==============================================================================

def test_the_facts_reach_the_program_and_change_the_answer(write_program):
    rules = discipline(write_program(PINNED))

    assert rules.solve(mass_band="light") == frozenset({"spar(aluminium)"})
    assert rules.solve(mass_band="heavy") == frozenset({"spar(cfrp)"})


def test_a_missing_fact_is_named(write_program):
    rules = discipline(write_program(PINNED), facts=["mass_band", "certification"])
    with pytest.raises(RuleProgramError, match=r"missing fact\(s\) \['certification'\]"):
        rules.solve(mass_band="light")


def test_unsat_is_an_explicit_value_and_never_none(write_program):
    """
    A discipline must be total. `None` would store nothing, leaving the previous
    value in place, which a convergence checker reads as converged.
    """
    rules = discipline(write_program(UNSATISFIABLE))
    answer = rules.solve(mass_band="heavy")

    assert answer is INFEASIBLE
    assert answer is not None
    assert not answer                      # falsey, so `if not answer:` reads well
    assert repr(answer) == "INFEASIBLE"


def test_infeasible_is_a_singleton():
    from smartmdao.rules import _Infeasible

    assert _Infeasible() is INFEASIBLE


def test_two_equally_optimal_models_refuse_rather_than_pick(write_program):
    """
    003 forbids silently taking the first: the engineer would get one
    architecture with nothing indicating the other was equally good.
    """
    rules = discipline(write_program(TIED))

    with pytest.raises(AmbiguousProgramError) as caught:
        rules.solve(mass_band="light")

    assert {frozenset(m) for m in caught.value.models} == {
        frozenset({"spar(aluminium)"}),
        frozenset({"spar(cfrp)"}),
    }
    assert "DISTINCT value per candidate" in str(caught.value)


def test_a_program_with_no_optimisation_is_still_checked_for_ambiguity(write_program):
    """Nothing is ever *proven* optimal, so the models are counted directly."""
    rules = discipline(write_program(UNOPTIMISED_MANY))

    with pytest.raises(AmbiguousProgramError, match="2 equally optimal"):
        rules.solve(mass_band="light")


def test_an_unoptimised_program_with_one_model_is_fine(write_program):
    rules = discipline(write_program(UNOPTIMISED_ONE))
    assert rules.solve(mass_band="heavy") == frozenset({"spar(cfrp)"})


# ==============================================================================
# Facts are symbols, not measurements
# ==============================================================================

def test_a_float_fact_is_refused_and_points_at_the_discretisation(write_program):
    """
    ASP has no floats. Converting one here would be an undeclared threshold,
    which is the hypothesis Bands exists to make visible.
    """
    rules = discipline(write_program(PINNED))

    with pytest.raises(RuleProgramError, match="declare a Bands"):
        rules.solve(mass_band=880.0)


def test_a_bool_fact_is_refused(write_program):
    rules = discipline(write_program(PINNED))
    with pytest.raises(RuleProgramError, match="no booleans as terms"):
        rules.solve(mass_band=True)


def test_an_unrepresentable_fact_is_refused(write_program):
    rules = discipline(write_program(PINNED))
    with pytest.raises(RuleProgramError, match="which has no ASP term"):
        rules.solve(mass_band=[1, 2])


def test_an_integer_fact_becomes_a_number(write_program):
    source = "big :- ribs(R), R > 5.\n#show big/0.\n"
    rules = RuleDiscipline(
        write_program(source), facts=["ribs"], produces="decisions"
    )
    assert rules.solve(ribs=8) == frozenset({"big"})
    assert rules.solve(ribs=3) == frozenset()


def test_a_symbol_needing_quotes_is_quoted(write_program):
    """
    'Part 25' is not a bare ASP constant, so it has to travel as a string
    rather than be silently mangled into one.
    """
    source = 'strict :- certification("Part 25").\n#show strict/0.\n'
    rules = RuleDiscipline(
        write_program(source), facts=["certification"], produces="decisions"
    )
    assert rules.solve(certification="Part 25") == frozenset({"strict"})
    assert rules.solve(certification="part23") == frozenset()


# ==============================================================================
# Wiring into a pipeline
# ==============================================================================

def test_add_accepts_a_discipline_and_registers_its_step(write_program):
    pipeline = Pipeline()
    pipeline.add(discipline(write_program(PINNED)))

    assert [step.name for step in pipeline.steps] == ["rules_wing"]
    assert pipeline.steps[0].resolve_output_names() == ["decisions"]


def test_add_still_accepts_a_plain_function():
    pipeline = Pipeline()

    def f(x: float) -> float:
        return x

    pipeline.add(f, outputs=["y"])
    assert pipeline.steps[0].resolve_output_names() == ["y"]


def test_the_rules_run_as_an_ordinary_step(write_program):
    pipeline = Pipeline(
        discretisation=Discretisation(
            mass_band=Bands("mass_kg", edges=[800], names=["light", "heavy"]),
        ),
    )

    @pipeline.step(outputs=["mass_kg"])
    def size_airframe(span_m: float) -> float:
        return 120.0 * span_m

    pipeline.add(discipline(write_program(PINNED)))

    light = pipeline.run(span_m=5.0)
    assert light["mass_band"] == "light"
    assert light["decisions"] == frozenset({"spar(aluminium)"})

    heavy = pipeline.run(span_m=10.0)
    assert heavy["decisions"] == frozenset({"spar(cfrp)"})


def test_the_analysis_sees_the_rules_without_solving_anything(write_program):
    """
    Registering the discipline as a real step is what makes this free: nothing
    here touches clingo.
    """
    pipeline = Pipeline(
        discretisation=Discretisation(
            mass_band=Bands("mass_kg", edges=[800], names=["light", "heavy"]),
        ),
    )

    @pipeline.step(outputs=["mass_kg"])
    def size_airframe(span_m: float) -> float:
        return 120.0 * span_m

    @pipeline.step(outputs=["report"])
    def summarise(decisions: frozenset) -> str:
        return str(sorted(decisions))

    pipeline.add(discipline(write_program(PINNED)))

    analysis = analyze(pipeline, inputs=["span_m"])
    assert analysis.execution_order == (
        "size_airframe",
        "discretise_mass_band",
        "rules_wing",
        "summarise",
    )
    assert validate(pipeline, inputs=["span_m"]) == ()


def test_a_band_consumed_only_through_rules_is_not_reported_as_unused(write_program):
    """
    Regression guard. `discretisation-unused` asks whether any *step* consumes
    the band; a band consumed only as an ASP fact is consumed in reality, and
    that holds only because the facts appear in the synthetic step's signature.
    If fact declaration ever stops doing that, this check starts warning about
    correctly wired pipelines.
    """
    pipeline = Pipeline(
        discretisation=Discretisation(
            mass_band=Bands("mass_kg", edges=[800], names=["light", "heavy"]),
        ),
    )

    @pipeline.step(outputs=["mass_kg"])
    def size_airframe(span_m: float) -> float:
        return 120.0 * span_m

    @pipeline.step(outputs=["report"])
    def summarise(decisions: frozenset) -> str:
        return str(sorted(decisions))

    pipeline.add(discipline(write_program(PINNED)))

    codes = [f.code for f in validate(pipeline, inputs=["span_m"])]
    assert "discretisation-unused" not in codes
    assert codes == []


def test_atoms_couple_to_a_feedback_loop_with_no_solver_change(write_program):
    """
    The reason the output is a frozenset. Structural equality over a set of
    atoms is what 002 asked for - couple on the decision, never on the
    explanation of it - and it needed nothing new in solvers.py.
    """
    source = """
    spar(aluminium) :- mass_band(light).
    spar(cfrp)      :- mass_band(heavy).
    #show spar/1.
    """
    pipeline = Pipeline(
        solver=HybridSolver(),
        discretisation=Discretisation(
            mass_band=Bands("total_mass_kg", edges=[500], names=["light", "heavy"]),
        ),
    )

    pipeline.add(
        RuleDiscipline(
            write_program(source),
            facts=["mass_band"],
            produces="decisions",
            name="choose_spar",
        )
    )

    @pipeline.step(outputs=["structure_mass_kg"])
    def size_structure(decisions: frozenset) -> float:
        return 200.0 if "spar(aluminium)" in decisions else 260.0

    @pipeline.step(outputs=["total_mass_kg"])
    def sum_masses(structure_mass_kg: float, payload_kg: float) -> float:
        return structure_mass_kg + payload_kg

    # Ask rather than guess. Both `choose_spar` and `discretise_mass_band` sort
    # ahead of the steps producing what they read, so the cycle needs two seeds
    # - one more than the same loop needed before the rules were added.
    analysis = analyze(pipeline, inputs=["payload_kg"])
    assert [g.variable for g in analysis.initial_guesses_required] == [
        "mass_band",
        "total_mass_kg",
    ]

    result = pipeline.run(payload_kg=250.0, mass_band="light", total_mass_kg=400.0)

    assert result["decisions"] == frozenset({"spar(aluminium)"})
    assert result["total_mass_kg"] == 450.0
    assert result["convergence_reports"][0].status == "converged"
