"""
The bridge between a number and a symbolic fact.

The threshold is where the answer is actually decided, so most of what is
tested here is not "does classify work" but "is the hypothesis visible" - that
a boundary value has a declared side, that a band nothing reads is reported,
and that declaring bands does not make the existing checks lie.
"""
import pytest

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
from smartmdao.discretisation import effective_steps


# ==============================================================================
# Declaration: what is rejected outright, and what is only reported
# ==============================================================================

def test_edges_cut_the_line_into_one_more_band_than_there_are_edges():
    bands = Bands("mass_kg", edges=[800], names=["light", "heavy"])
    assert bands.names == ("light", "heavy")
    assert bands.edges == (800,)


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        (dict(edges=[800], names=["light", "medium", "heavy"]), "one more name"),
        (dict(edges=[800, 1200], names=["light", "heavy"]), "one more name"),
        (dict(edges=[], names=["everything"]), "no edges"),
        (dict(edges=[800], names=["same", "same"]), "repeats a band name"),
        (dict(edges=[1200, 800], names=["a", "b", "c"]), "do not strictly ascend"),
        (dict(edges=[800, 800], names=["a", "b", "c"]), "do not strictly ascend"),
        (dict(edges=["800"], names=["a", "b"]), "non-numeric edge"),
        (dict(edges=[True], names=["a", "b"]), "non-numeric edge"),
        (dict(edges=[float("nan")], names=["a", "b"]), "NaN edge"),
        (dict(edges=[800], names=["a", "b"], closed="middle"), "closed must be"),
    ],
)
def test_incoherent_declarations_are_rejected_at_construction(kwargs, fragment):
    """
    Only incoherence raises. Two names for three intervals has no defensible
    reading, so there is no informed decision to hand back - unlike a threshold
    in a questionable place, which validate() reports instead.
    """
    with pytest.raises(DiscretisationError, match=fragment):
        Bands("mass_kg", **kwargs)


def test_equal_edges_are_rejected_because_the_band_between_them_is_unreachable():
    with pytest.raises(DiscretisationError, match="no value can reach"):
        Bands("mass_kg", edges=[800, 800], names=["light", "nothing", "heavy"])


def test_discretisation_rejects_anything_that_is_not_a_band():
    with pytest.raises(DiscretisationError, match="must be a Bands instance"):
        Discretisation(mass_band={"light": lambda m: m < 800})


# ==============================================================================
# Classification
# ==============================================================================

def test_bands_tile_the_whole_line_so_classification_is_total():
    bands = Bands("mass_kg", edges=[800, 1200], names=["light", "medium", "heavy"])

    assert bands.classify(-1e9) == "light"
    assert bands.classify(0) == "light"
    assert bands.classify(1000) == "medium"
    assert bands.classify(1e9) == "heavy"


def test_an_exact_edge_value_has_a_declared_side():
    """
    Whether 800.0 is light or heavy changes the answer, so it is a field rather
    than a convention someone has to discover by experiment.
    """
    left = Bands("mass_kg", edges=[800], names=["light", "heavy"], closed="left")
    right = Bands("mass_kg", edges=[800], names=["light", "heavy"], closed="right")

    assert left.classify(800) == "heavy"
    assert right.classify(800) == "light"

    # Away from the boundary the two agree, which is what makes the boundary
    # case so easy to miss.
    assert left.classify(799.99) == right.classify(799.99) == "light"
    assert left.classify(800.01) == right.classify(800.01) == "heavy"


def test_classifying_a_nan_is_refused_rather_than_silently_taking_a_band():
    """NaN compares false against every edge, so it would take the last band."""
    bands = Bands("mass_kg", edges=[800], names=["light", "heavy"])
    with pytest.raises(DiscretisationError, match="NaN"):
        bands.classify(float("nan"))


@pytest.mark.parametrize("value", ["880", None, True])
def test_classifying_a_non_number_is_refused(value):
    bands = Bands("mass_kg", edges=[800], names=["light", "heavy"])
    with pytest.raises(DiscretisationError, match="numeric intervals"):
        bands.classify(value)


def test_integers_classify_like_floats():
    bands = Bands("count", edges=[10], names=["few", "many"])
    assert bands.classify(3) == "few"
    assert bands.classify(30) == "many"


# ==============================================================================
# The container
# ==============================================================================

def test_an_empty_discretisation_is_falsey_so_the_checks_can_skip_it():
    assert not Discretisation()
    assert Discretisation(b=Bands("x", edges=[1], names=["lo", "hi"]))


def test_sources_and_produced_report_both_halves_of_the_mapping():
    discretisation = Discretisation(
        mass_band=Bands("mass_kg", edges=[800], names=["light", "heavy"]),
        speed_band=Bands("speed_ms", edges=[100], names=["slow", "fast"]),
    )
    assert discretisation.sources == ("mass_kg", "speed_ms")
    assert discretisation.produced == ("mass_band", "speed_band")


def test_apply_skips_a_band_whose_source_is_absent():
    """
    Structural problems are validate()'s job. Raising here would surface a
    wiring mistake from inside a solve, which is the opposite of the point.
    """
    discretisation = Discretisation(
        mass_band=Bands("mass_kg", edges=[800], names=["light", "heavy"]),
        speed_band=Bands("speed_ms", edges=[100], names=["slow", "fast"]),
    )
    assert discretisation.apply({"mass_kg": 900.0}) == {"mass_band": "heavy"}
    assert discretisation.apply({}) == {}


def test_describe_states_every_interval_with_its_open_side():
    left = Bands("mass_kg", edges=[800, 1200], names=["light", "medium", "heavy"])
    assert left.describe() == (
        "mass_kg -> light=(-inf, 800); medium=[800, 1200); heavy=[1200, +inf)"
    )

    right = Bands("mass_kg", edges=[800], names=["light", "heavy"], closed="right")
    assert right.describe() == "mass_kg -> light=(-inf, 800]; heavy=(800, +inf)"


# ==============================================================================
# A band is an ordinary step
# ==============================================================================

def build_pipeline(**discretisation_kwargs):
    pipeline = Pipeline(discretisation=Discretisation(**discretisation_kwargs))

    @pipeline.step(outputs=["mass_kg"])
    def size_airframe(span: float) -> float:
        return 120.0 * span

    @pipeline.step(outputs=["cost"])
    def price(mass_band: str) -> float:
        return {"light": 1.0, "medium": 2.0, "heavy": 3.0}[mass_band]

    return pipeline


def test_effective_steps_adds_one_step_per_band():
    pipeline = build_pipeline(
        mass_band=Bands("mass_kg", edges=[800], names=["light", "heavy"])
    )
    names = [step.name for step in effective_steps(pipeline)]
    assert names == ["size_airframe", "price", "discretise_mass_band"]


def test_a_pipeline_without_a_discretisation_is_untouched():
    pipeline = Pipeline()

    @pipeline.step(outputs=["y"])
    def f(x: float) -> float:
        return x

    assert [step.name for step in effective_steps(pipeline)] == ["f"]


def test_effective_steps_tolerates_an_object_that_is_not_a_pipeline():
    """The MCP loader hands analysis whatever it found; duck-typing is the rule."""

    class Bare:
        steps = []

    assert effective_steps(Bare()) == []


def test_the_band_is_ordered_by_the_solver_like_any_other_step():
    pipeline = build_pipeline(
        mass_band=Bands("mass_kg", edges=[800], names=["light", "heavy"])
    )
    analysis = analyze(pipeline, inputs=["span"])

    assert analysis.execution_order == (
        "size_airframe",
        "discretise_mass_band",
        "price",
    )
    assert analysis.external_inputs == ("span",)


def test_running_a_pipeline_derives_the_fact_and_uses_it():
    pipeline = build_pipeline(
        mass_band=Bands("mass_kg", edges=[800], names=["light", "heavy"])
    )

    light = pipeline.run(span=5.0)
    assert light["mass_kg"] == 600.0
    assert light["mass_band"] == "light"
    assert light["cost"] == 1.0

    heavy = pipeline.run(span=10.0)
    assert heavy["mass_band"] == "heavy"
    assert heavy["cost"] == 3.0


def bistable_pipeline():
    """
    A mass loop whose structure mass depends on which band the total falls in.

    Both bands are self-consistent: seeded light it settles at 450 (light,
    since 450 < 500), seeded heavy it settles at 510 (heavy, since 510 >= 500).
    """
    pipeline = Pipeline(
        solver=HybridSolver(),
        discretisation=Discretisation(
            mass_band=Bands("total_mass", edges=[500], names=["light", "heavy"])
        ),
    )

    @pipeline.step(outputs=["structure_mass"])
    def size_structure(mass_band: str) -> float:
        return 200.0 if mass_band == "light" else 260.0

    @pipeline.step(outputs=["total_mass"])
    def sum_masses(structure_mass: float, payload: float) -> float:
        return structure_mass + payload

    return pipeline


def test_a_band_inside_a_feedback_loop_converges():
    """
    The symbolic fact couples to the continuous side - the whole point of the
    layer, and it works because non-numeric convergence already exists.
    """
    result = bistable_pipeline().run(payload=250.0, total_mass=400.0)

    assert result["mass_band"] == "light"
    assert result["total_mass"] == 450.0
    assert result["convergence_reports"][0].status == "converged"


def test_the_band_step_participates_in_the_alphabetical_seeding_rule():
    """
    The synthetic step is named `discretise_<band>`, which sorts before most
    discipline names - so it runs first in the cyclic block and *its* unproduced
    input is the one needing a seed. Ask analyze(); do not reason it out.
    """
    analysis = analyze(bistable_pipeline(), inputs=["payload"])

    assert analysis.cycles[0].steps[0] == "discretise_mass_band"
    assert [g.variable for g in analysis.initial_guesses_required] == ["total_mass"]


def test_a_discretised_loop_can_have_more_than_one_fixed_point():
    """
    Both answers are genuinely converged, and the initial guess alone decides
    which one comes back. This is answer-set multiplicity arriving on the
    numeric side: a threshold inside a loop makes the seed load-bearing, and
    nothing in the result says so.
    """
    pipeline = bistable_pipeline()

    light = pipeline.run(payload=250.0, total_mass=400.0)
    heavy = pipeline.run(payload=250.0, total_mass=900.0)

    assert (light["mass_band"], light["total_mass"]) == ("light", 450.0)
    assert (heavy["mass_band"], heavy["total_mass"]) == ("heavy", 510.0)

    # Both report success. Neither mentions that the other exists.
    assert light["convergence_reports"][0].status == "converged"
    assert heavy["convergence_reports"][0].status == "converged"


# ==============================================================================
# What validate() says
# ==============================================================================

def codes(pipeline, inputs=()):
    return [finding.code for finding in validate(pipeline, inputs=inputs)]


def test_a_correctly_wired_discretisation_is_clean():
    pipeline = build_pipeline(
        mass_band=Bands("mass_kg", edges=[800], names=["light", "heavy"])
    )
    assert codes(pipeline, inputs=["span"]) == []


def test_a_band_nothing_consumes_is_reported():
    pipeline = Pipeline(
        discretisation=Discretisation(
            mass_band=Bands("mass_kg", edges=[800], names=["light", "heavy"])
        )
    )

    @pipeline.step(outputs=["mass_kg"])
    def size_airframe(span: float) -> float:
        return 120.0 * span

    findings = validate(pipeline, inputs=["span"])
    unused = [f for f in findings if f.code == "discretisation-unused"]
    assert len(unused) == 1
    assert unused[0].variable == "mass_band"
    assert "cannot influence the answer" in unused[0].message


def test_a_source_nothing_produces_is_reported_by_the_general_check():
    """
    Modelling the band as a real step means `missing-input` already covers it -
    which is why there is no discretisation-specific finding for this.
    """
    pipeline = Pipeline(
        discretisation=Discretisation(
            mass_band=Bands("mass_kgs", edges=[800], names=["light", "heavy"])
        )
    )

    @pipeline.step(outputs=["cost"])
    def price(mass_band: str) -> float:
        return 1.0

    findings = validate(pipeline)
    missing = [f for f in findings if f.code == "missing-input"]
    assert [f.variable for f in missing] == ["mass_kgs"]
    assert "discretise_mass_band" in missing[0].message


def test_a_band_name_a_step_also_produces_is_reported_as_a_duplicate():
    pipeline = Pipeline(
        discretisation=Discretisation(
            mass_band=Bands("mass_kg", edges=[800], names=["light", "heavy"])
        )
    )

    @pipeline.step(outputs=["mass_band"])
    def guess_band(mass_kg: float) -> str:
        return "light"

    @pipeline.step(outputs=["cost"])
    def price(mass_band: str) -> float:
        return 1.0

    assert "duplicate-output" in codes(pipeline, inputs=["mass_kg"])


def test_banding_a_non_numeric_variable_is_reported():
    pipeline = Pipeline(
        discretisation=Discretisation(
            mass_band=Bands("label", edges=[800], names=["light", "heavy"])
        )
    )

    @pipeline.step(outputs=["label"])
    def make_label() -> str:
        return "wing"

    @pipeline.step(outputs=["cost"])
    def price(mass_band: str) -> float:
        return 1.0

    findings = validate(pipeline)
    non_numeric = [f for f in findings if f.code == "discretisation-non-numeric"]
    assert len(non_numeric) == 1
    assert non_numeric[0].variable == "label"
    assert "raise at run time" in non_numeric[0].message


def test_a_bool_source_is_reported_even_though_bool_subclasses_int():
    """True would classify as 1.0 and land in a band nobody intended."""
    pipeline = Pipeline(
        discretisation=Discretisation(
            mass_band=Bands("flag", edges=[0.5], names=["low", "high"])
        )
    )

    @pipeline.step(outputs=["flag"])
    def make_flag() -> bool:
        return True

    @pipeline.step(outputs=["cost"])
    def price(mass_band: str) -> float:
        return 1.0

    assert "discretisation-non-numeric" in codes(pipeline)


def test_an_int_source_is_not_reported():
    pipeline = Pipeline(
        discretisation=Discretisation(
            count_band=Bands("count", edges=[10], names=["few", "many"])
        )
    )

    @pipeline.step(outputs=["count"])
    def make_count() -> int:
        return 3

    @pipeline.step(outputs=["cost"])
    def price(count_band: str) -> float:
        return 1.0

    assert codes(pipeline) == []


def test_an_unannotated_source_degrades_to_unchecked_rather_than_failing():
    """Invariant 2: missing type information is never an error."""
    pipeline = Pipeline(
        discretisation=Discretisation(
            mass_band=Bands("mass_kg", edges=[800], names=["light", "heavy"])
        )
    )

    @pipeline.step(outputs=["mass_kg"])
    def size_airframe(span):
        return 120.0 * span

    @pipeline.step(outputs=["cost"])
    def price(mass_band: str) -> float:
        return 1.0

    assert codes(pipeline, inputs=["span"]) == []


# ==============================================================================
# explain()
# ==============================================================================

def test_explain_states_the_thresholds_and_which_side_an_edge_falls_on():
    pipeline = build_pipeline(
        mass_band=Bands("mass_kg", edges=[800], names=["light", "heavy"])
    )
    text = explain(pipeline, inputs=["span"])

    assert "Discretisation (1):" in text
    assert "light=(-inf, 800); heavy=[800, +inf)" in text
    assert "edge values fall in the band above" in text


def test_explain_states_the_other_side_too():
    pipeline = build_pipeline(
        mass_band=Bands(
            "mass_kg", edges=[800], names=["light", "heavy"], closed="right"
        )
    )
    assert "edge values fall in the band below" in explain(pipeline, inputs=["span"])


def test_explain_says_nothing_about_discretisation_when_there_is_none():
    pipeline = Pipeline()

    @pipeline.step(outputs=["y"])
    def f(x: float) -> float:
        return x

    assert "Discretisation" not in explain(pipeline, inputs=["x"])
