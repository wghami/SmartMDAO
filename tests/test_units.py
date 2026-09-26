"""
Units, checked for consistency and never converted. Roadmap 6.8, record 008.

What is guarded here:
  - a `Unit` marker is read from parameters, returns, dataclass fields and
    tuple elements; bare strings in `Annotated` are ignored;
  - a declared mismatch is an error, on a connection or between two consumers
    of one external input; an undeclared end is unchecked (invariant 2);
  - no value is ever changed - a run gives the same numbers with or without
    units;
  - the external-input TYPE gap found while writing 008.
"""
import matplotlib

matplotlib.use("Agg")

from dataclasses import dataclass
from typing import Annotated, Any, Optional, Tuple

import pytest

from smartmdao import (
    Pipeline, StandardTypeChecker, StandardUnitChecker, Unit, UnitChecker, explain, validate,
)
from smartmdao.models import Step
from smartmdao.units import input_units, mismatch_hint, output_units, unit_of
from smartmdao.visualization import PipelineVisualizer

dB = Annotated[float, Unit("dB")]
linear = Annotated[float, Unit("linear")]


def codes(findings):
    return [finding.code for finding in findings]


# ==============================================================================
# The marker, and where units are read
# ==============================================================================

@pytest.mark.parametrize("bad", ["", "   ", 3])
def test_a_unit_must_be_a_non_empty_string(bad):
    with pytest.raises(TypeError, match="non-empty string"):
        Unit(bad)


def test_a_unit_prints_as_its_symbol():
    assert str(Unit("dB")) == "dB"


def test_a_bare_string_is_not_a_unit():
    """That space is shared with pydantic, Typer and descriptions."""
    assert unit_of(Annotated[float, "wing span"]) is None
    assert unit_of(Annotated[float, "dB", Unit("dBm")]) == "dBm"
    assert unit_of(float) is None


def test_units_are_read_from_parameters_and_a_single_return():
    def antenna(power: Annotated[float, Unit("W")], label: str) -> dB:
        return 1.0

    step = Step(antenna, ["gain"])
    assert input_units(step) == {"power": "W"}
    assert output_units(step) == {"gain": "dB"}


def test_units_are_read_from_dataclass_fields():
    @dataclass
    class Link:
        snr: Annotated[float, Unit("dB")]
        rate: Annotated[float, Unit("Mbps")]
        name: str

    def link(x: float) -> Link:
        return Link(1.0, 2.0, "a")

    assert output_units(Step(link)) == {"snr": "dB", "rate": "Mbps"}


def test_a_dataclass_can_carry_a_unit_marker_itself_without_confusion():
    @dataclass
    class Link:
        snr: Annotated[float, Unit("dB")]

    def link(x: float) -> Annotated[Link, "a link"]:
        return Link(1.0)

    assert output_units(Step(link)) == {"snr": "dB"}


def test_units_are_read_from_tuple_elements():
    def split(x: float) -> Tuple[Annotated[float, Unit("m")], Annotated[float, Unit("s")], float]:
        return 1.0, 2.0, 3.0

    assert output_units(Step(split, ["length", "time", "other"])) == {"length": "m", "time": "s"}
    assert output_units(Step(split, ["length", "time"])) == {}          # shapes disagree: unchecked


def test_an_unannotated_step_has_no_units():
    def plain(x):
        return x

    assert input_units(Step(plain, ["y"])) == {} and output_units(Step(plain, ["y"])) == {}


def test_unresolvable_annotations_are_unchecked_not_an_error():
    def broken(x: "NoSuchType") -> "AlsoMissing":  # noqa: F821
        return x

    assert input_units(Step(broken, ["y"])) == {} and output_units(Step(broken, ["y"])) == {}


def test_units_are_seen_through_decorators():
    from smartmdao import MemoryBackend, cached

    @cached(MemoryBackend())
    def antenna(power: Annotated[float, Unit("W")]) -> dB:
        return 1.0

    assert input_units(Step(antenna, ["gain"])) == {"power": "W"}


# ==============================================================================
# Consistency
# ==============================================================================

def test_the_standard_checker_demands_the_same_unit_exactly():
    checker = StandardUnitChecker()
    assert checker.consistent("dB", " dB ")
    assert not checker.consistent("km", "m")
    assert not checker.consistent("meter", "m")        # spellings are not guessed
    assert not checker.consistent("dB", "dBm")
    assert isinstance(checker, UnitChecker)


def test_a_logarithmic_mismatch_says_so():
    assert "logarithm" in mismatch_hint("dB", "linear")
    assert mismatch_hint("km", "m") == ""
    assert mismatch_hint("dB", "dBm") == ""            # both logarithmic: a reference, not a log


def link_budget(gain_unit, expected_unit):
    pipeline = Pipeline(inputs=["power"])

    @pipeline.step(outputs=["gain"])
    def antenna(power: Annotated[float, Unit("W")]) -> Annotated[float, Unit(gain_unit)]:
        return power * 10

    @pipeline.step(outputs=["margin"])
    def budget(gain: Annotated[float, Unit(expected_unit)]) -> float:
        return gain - 3

    return pipeline


def test_a_connection_with_different_units_is_an_error():
    [finding] = [f for f in validate(link_budget("dB", "linear")) if f.code == "unit-mismatch"]

    assert finding.severity == "error"
    assert finding.step == "budget" and finding.variable == "gain"
    assert "'antenna' produces gain in dB, but 'budget' expects it in linear" in finding.message
    assert "logarithm" in finding.message


def test_a_connection_with_the_same_unit_is_clean():
    assert validate(link_budget("dB", "dB")) == ()


def test_an_undeclared_end_is_unchecked():
    pipeline = Pipeline(inputs=["power"])

    @pipeline.step(outputs=["gain"])
    def antenna(power: float) -> float:
        return power

    @pipeline.step(outputs=["margin"])
    def budget(gain: linear) -> float:
        return gain

    assert "unit-mismatch" not in codes(validate(pipeline))


def test_consumers_of_one_external_input_must_agree():
    pipeline = Pipeline(inputs=["distance"])

    @pipeline.step(outputs=["delay"])
    def latency(distance: Annotated[float, Unit("m")]) -> float:
        return distance

    @pipeline.step(outputs=["cost"])
    def price(distance: Annotated[float, Unit("km")]) -> float:
        return distance

    @pipeline.step(outputs=["other"])
    def more(distance: Annotated[float, Unit("mi")]) -> float:
        return distance

    [finding] = [f for f in validate(pipeline) if f.code == "unit-mismatch"]
    assert "expected in m by 'latency' and in km by 'price'" in finding.message
    assert finding.variable == "distance"


def test_a_project_can_supply_its_own_checker():
    """Yes or no only: aliases, or a unit library's dimensional analysis."""
    class Aliases:
        SAME = {frozenset({"m", "meter"})}

        def consistent(self, produced, expected):
            return produced == expected or frozenset({produced, expected}) in self.SAME

    pipeline = link_budget("meter", "m")
    assert "unit-mismatch" in codes(validate(pipeline))

    pipeline.unit_checker = Aliases()
    assert "unit-mismatch" not in codes(validate(pipeline))


def test_explain_says_how_much_is_checked():
    text = explain(link_budget("dB", "dB"))
    assert "Units: 1 of 1 connections between steps checked." in text


def test_explain_says_nothing_about_units_when_none_are_declared():
    pipeline = Pipeline(inputs=["a"])

    @pipeline.step(outputs=["b"])
    def f(a: float) -> float:
        return a

    @pipeline.step(outputs=["c"])
    def g(b: float) -> float:
        return b

    assert "Units:" not in explain(pipeline)


def test_units_never_change_a_value():
    """The rule 008 starts from: the same numbers, with or without units."""
    plain = Pipeline(inputs=["power"])

    @plain.step(outputs=["gain"])
    def antenna(power: float) -> float:
        return power * 10

    @plain.step(outputs=["margin"])
    def budget(gain: float) -> float:
        return gain - 3

    assert link_budget("dB", "dB").run(power=2.0) == plain.run(power=2.0)
    assert link_budget("dB", "linear").run(power=2.0) == plain.run(power=2.0)   # reported, not fixed


# ==============================================================================
# The external-input TYPE gap found while writing 008
# ==============================================================================

def shared(first, second):
    pipeline = Pipeline(inputs=["distance"])

    def a(distance):
        return 1.0
    a.__annotations__ = {"distance": first, "return": float}

    def b(distance):
        return 2.0
    b.__annotations__ = {"distance": second, "return": float}

    pipeline.add(a, outputs=["x"])
    pipeline.add(b, outputs=["y"])
    return pipeline


def test_two_consumers_declaring_incompatible_types_are_reported():
    """Until 1.27.0 this passed validate() and failed only at run()."""
    [finding] = [f for f in validate(shared(float, str)) if f.code == "type-mismatch"]
    assert "declared float by 'a' and str by 'b'" in finding.message
    assert finding.severity == "error"


@pytest.mark.parametrize("first, second", [
    (float, Optional[float]),            # a float satisfies both
    (Optional[str], Optional[int]),      # None satisfies both
    (float, Any),                        # Any is unchecked
    (int, bool),                         # bool is an int
])
def test_types_one_value_could_satisfy_are_not_reported(first, second):
    assert "type-mismatch" not in codes(validate(shared(first, second)))


def test_a_custom_type_checker_is_respected():
    class Loose(StandardTypeChecker):
        def check_types(self, produced, expected):
            return {produced, expected} <= {int, float} or super().check_types(produced, expected)

    assert "type-mismatch" in codes(validate(shared(int, float)))             # strict by default
    assert "type-mismatch" not in codes(validate(shared(int, float), type_checker=Loose()))


# ==============================================================================
# The diagram
# ==============================================================================

def labels(pipeline):
    visual = PipelineVisualizer(pipeline.steps, set(pipeline.inputs), pipeline.unit_checker).build()
    return {text.get_text() for text in visual.ax.texts}


def test_the_diagram_shows_units():
    assert {"power [W]", "gain [dB]"} <= labels(link_budget("dB", "dB"))


def test_a_mismatched_connection_is_drawn_like_a_missing_input():
    good = PipelineVisualizer(link_budget("dB", "dB").steps, {"power"}).build()
    bad = PipelineVisualizer(link_budget("dB", "linear").steps, {"power"}).build()

    def fills(visual):
        return {tuple(round(c, 3) for c in patch.get_facecolor()) for patch in visual.ax.patches}

    missing = tuple(round(c, 3) for c in matplotlib.colors.to_rgba(PipelineVisualizer.STYLE_MISSING["facecolor"]))
    assert missing in fills(bad)
    assert missing not in fills(good)


def test_visualize_uses_the_pipelines_checker(tmp_path, monkeypatch):
    import smartmdao.core as core

    seen = {}
    monkeypatch.setattr(core, "visualize_pipeline", lambda **kwargs: seen.update(kwargs))
    pipeline = link_budget("dB", "dB")
    pipeline.visualize(view=False)
    assert seen["unit_checker"] is pipeline.unit_checker
