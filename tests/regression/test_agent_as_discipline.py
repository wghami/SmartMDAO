"""Regression tests for scripts/agent_as_discipline_demo.py.

Unlike the benchmark regressions in this directory, which mirror their scripts,
this one loads the demo module directly. The demo's whole purpose is to
substantiate a claim made in docs/design/002-agent-as-discipline.md, so the
test has to pin the actual file rather than a copy of it that could drift.

`scripts/` is not a package, hence the explicit path load.
"""
import importlib.util
import pathlib

import pytest

from smartmdao.solvers import OscillationDetectedError

DEMO_PATH = (
    pathlib.Path(__file__).resolve().parents[2] / "scripts" / "agent_as_discipline_demo.py"
)


def load_demo():
    spec = importlib.util.spec_from_file_location("agent_as_discipline_demo", DEMO_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def demo():
    return load_demo()


def test_converges_on_a_feasible_architecture(demo):
    requirements = demo.Requirements(min_range_km=400.0, max_mass_kg=1200.0)
    pipeline = demo.build_pipeline(demo.ask_model)

    result = pipeline.run(
        architecture=demo.Architecture(n_motors=2, battery="li-ion"),
        requirements=requirements,
    )

    # The model trades chemistry, not motor count, to buy range.
    assert result["architecture"] == demo.Architecture(n_motors=2, battery="li-s")
    assert result["violations"] == frozenset()
    assert result["range_km"] >= requirements.min_range_km
    assert result["mass_kg"] <= requirements.max_mass_kg
    assert len(result["residual_history"][-1]) == 2


def test_converges_on_an_explicit_infeasible_sentinel(demo):
    requirements = demo.Requirements(min_range_km=900.0, max_mass_kg=1200.0)
    pipeline = demo.build_pipeline(demo.ask_model)

    result = pipeline.run(
        architecture=demo.Architecture(n_motors=2, battery="li-ion"),
        requirements=requirements,
    )

    # A converged "no" - the loop terminated normally, it did not fail.
    assert result["architecture"] is demo.INFEASIBLE
    assert result["architecture"].infeasible is True
    assert len(result["residual_history"][-1]) == 3


def test_naive_model_oscillates_and_is_caught_early(demo):
    requirements = demo.Requirements(min_range_km=600.0, max_mass_kg=950.0)
    pipeline = demo.build_pipeline(demo.naive_ask_model, max_iterations=100)

    with pytest.raises(OscillationDetectedError) as excinfo:
        pipeline.run(
            architecture=demo.Architecture(n_motors=2, battery="li-s"),
            requirements=requirements,
        )

    error = excinfo.value
    assert error.period == 2
    # Caught after two full repetitions, not after all 100 sweeps.
    assert error.iteration == 4
    assert {architecture.n_motors for architecture in error.cycle} == {2, 4}


def test_registering_the_model_first_converges_prematurely(demo):
    """Pins the trap documented in build_pipeline's docstring.

    If the model discipline runs before the numeric one, sweep 1 hands it an
    empty violation set. It returns the architecture unchanged, and the solver
    reads that as a fixed point - reporting convergence on an architecture that
    was never evaluated and in fact violates its requirements.
    """
    from smartmdao import Pipeline, IterativeSolver, OscillationAwareConvergenceChecker

    requirements = demo.Requirements(min_range_km=400.0, max_mass_kg=1200.0)
    pipeline = Pipeline(
        solver=IterativeSolver(
            target_var="architecture",
            convergence_checker=OscillationAwareConvergenceChecker(),
        )
    )
    pipeline.add(demo.ask_model, outputs=["architecture"])  # wrong order, on purpose
    pipeline.add(
        demo.evaluate_architecture, outputs=["mass_kg", "range_km", "violations"]
    )

    result = pipeline.run(
        architecture=demo.Architecture(n_motors=2, battery="li-ion"),
        violations=frozenset(),
        requirements=requirements,
    )

    assert len(result["residual_history"][-1]) == 1          # "converged" immediately
    assert result["architecture"].battery == "li-ion"        # never revised
    assert result["violations"] == frozenset({"range"})      # and still violating
