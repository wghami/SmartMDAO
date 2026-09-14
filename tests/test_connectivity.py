"""The disconnected-graph check, and the real bug that prompted it.

A coding agent asked to size a wing from span, chord and speed produced a
pipeline that validated clean, converged, and whose arithmetic was correct —
and whose answer did not depend on span, chord or speed at all, because the
discipline consuming them fed nothing. `tests/fixtures/wing_mda_disconnected.py`
is that file, kept verbatim.

Static analysis cannot tell you whether your physics is right. It *can* tell
you a discipline is connected to nothing, and this is the check that does.
"""
import importlib.util
import pathlib

import pytest

from smartmdao import DAGSolver, HybridSolver, Pipeline
from smartmdao.analysis import WARNING, validate
from smartmdao.graph import weakly_connected_components

REPO = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "wing_mda_disconnected.py"


def codes(findings):
    return [finding.code for finding in findings]


# --- weakly_connected_components ---------------------------------------------

def test_a_chain_is_one_component():
    pipeline = Pipeline()
    pipeline.add(lambda a: a * 2, outputs=["b"])
    pipeline.add(lambda b: b + 1, outputs=["c"])

    assert len(weakly_connected_components(pipeline.steps)) == 1


def test_direction_is_ignored_so_shared_inputs_join_steps():
    """Two steps reading the same variable are connected, though neither feeds
    the other."""
    pipeline = Pipeline()
    pipeline.add(lambda shared: shared * 2, outputs=["left"])
    pipeline.add(lambda shared: shared + 1, outputs=["right"])

    assert len(weakly_connected_components(pipeline.steps)) == 1


def test_unrelated_steps_are_separate_components():
    pipeline = Pipeline()
    pipeline.add(lambda a: a * 2, outputs=["b"])
    pipeline.add(lambda x: x + 1, outputs=["y"])

    groups = weakly_connected_components(pipeline.steps)
    assert len(groups) == 2


def test_components_are_returned_largest_first():
    pipeline = Pipeline()

    @pipeline.step(outputs=["b"])
    def first(a: float) -> float:
        return a

    @pipeline.step(outputs=["c"])
    def second(b: float) -> float:
        return b

    @pipeline.step(outputs=["y"])
    def lonely(x: float) -> float:
        return x

    groups = weakly_connected_components(pipeline.steps)
    assert [len(group) for group in groups] == [2, 1]
    assert groups[1][0].name == "lonely"


def test_an_empty_pipeline_has_no_components():
    assert weakly_connected_components(Pipeline().steps) == []


# --- the finding --------------------------------------------------------------

def test_a_connected_pipeline_is_not_flagged():
    pipeline = Pipeline()
    pipeline.add(lambda a: a * 2, outputs=["b"])
    pipeline.add(lambda b: b + 1, outputs=["c"])

    assert "disconnected-graph" not in codes(validate(pipeline, inputs=["a"]))


def test_a_stray_discipline_is_flagged_with_its_dangling_output():
    pipeline = Pipeline(solver=DAGSolver())

    @pipeline.step(outputs=["sized"])
    def size(load: float) -> float:
        return load * 2

    @pipeline.step(outputs=["drag"])
    def aero(speed: float) -> float:
        return speed ** 2

    finding = next(
        f for f in validate(pipeline, inputs=["load", "speed"])
        if f.code == "disconnected-graph"
    )
    assert finding.severity == WARNING
    assert "aero" in finding.message
    assert "['drag'] consumed by nothing" in finding.message
    assert "2 disconnected pieces" in finding.message


def test_every_piece_is_described_not_just_the_odd_one_out():
    pipeline = Pipeline(solver=DAGSolver())

    @pipeline.step(outputs=["b"])
    def main_one(a: float) -> float:
        return a

    @pipeline.step(outputs=["c"])
    def main_two(b: float) -> float:
        return b

    @pipeline.step(outputs=["y"])
    def stray_one(x: float) -> float:
        return x

    @pipeline.step(outputs=["z"])
    def stray_two(y: float) -> float:
        return y

    finding = next(
        f for f in validate(pipeline, inputs=["a", "x"])
        if f.code == "disconnected-graph"
    )
    # No piece is designated "the real pipeline" - choosing by size is
    # arbitrary as soon as the pieces are comparable, and the engineer knows
    # which was intended.
    for name in ("main_one", "main_two", "stray_one", "stray_two"):
        assert name in finding.message
    assert finding.step is None


def test_a_piece_whose_outputs_are_all_consumed_internally():
    """Covers the branch where a piece has nothing dangling to report."""
    pipeline = Pipeline(solver=DAGSolver())

    @pipeline.step(outputs=["b"])
    def main_step(a: float) -> float:
        return a

    @pipeline.step(outputs=["y"])
    def stray_producer(x: float) -> float:
        return x

    @pipeline.step(outputs=["x"])
    def stray_consumer(y: float) -> float:
        return y

    finding = next(
        f for f in validate(pipeline, inputs=["a", "x"])
        if f.code == "disconnected-graph"
    )
    # The self-contained pair is listed with no "consumed by nothing" note.
    assert "['stray_producer', 'stray_consumer']" in finding.message or (
        "['stray_consumer', 'stray_producer']" in finding.message
    )
    assert finding.message.count("consumed by nothing") == 1


# --- the real thing -----------------------------------------------------------

@pytest.fixture(scope="module")
def wing():
    spec = importlib.util.spec_from_file_location("wing_fixture", FIXTURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.pipeline


def test_the_agent_written_wing_model_is_caught(wing):
    findings = validate(wing, inputs=["span", "chord", "speed", "payload_mass", "total_mass"])

    finding = next(f for f in findings if f.code == "disconnected-graph")
    assert "compute_lift" in finding.message
    assert "['lift']" in finding.message


def test_the_wing_model_is_otherwise_structurally_valid(wing):
    """The point of the whole exercise: it passes everything else.

    No type mismatch, no missing input, no unseeded cycle, correct solver. It
    converges and the arithmetic is right. Only connectivity reveals that half
    the inputs cannot influence the answer.
    """
    findings = validate(wing, inputs=["span", "chord", "speed", "payload_mass", "total_mass"])

    assert [f.code for f in findings] == ["disconnected-graph"]
    assert not any(f.severity == "error" for f in findings)


def test_the_wing_models_answer_ignores_the_variables_it_was_sized_from(wing):
    """Demonstrates the consequence, so the finding is not taken as pedantry."""
    base = dict(span=12.0, chord=1.5, speed=60.0, payload_mass=400.0, total_mass=1000.0)
    wide = dict(base, span=48.0, chord=6.0, speed=5.0)

    assert wing.run(**base)["total_mass"] == pytest.approx(wing.run(**wide)["total_mass"])
