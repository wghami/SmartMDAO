import pytest
from dataclasses import dataclass
from typing import Optional, Union

from smartmdao.models import Step
from smartmdao.core import Pipeline
from smartmdao.executor import StepExecutor
from smartmdao.solvers import IterativeSolver, HybridSolver
from smartmdao.validation import (
    StandardTypeChecker,
    TypeMismatchError,
    validate_structure,
    validate_external_inputs,
)


# --- StandardTypeChecker.check_value ---

def test_check_value_exact_match():
    checker = StandardTypeChecker()
    assert checker.check_value(1.0, float)
    assert not checker.check_value("x", float)

def test_check_value_optional():
    checker = StandardTypeChecker()
    assert checker.check_value(None, Optional[float])
    assert checker.check_value(1.0, Optional[float])
    assert not checker.check_value("x", Optional[float])

def test_check_value_union():
    checker = StandardTypeChecker()
    assert checker.check_value(1, Union[int, str])
    assert checker.check_value("a", Union[int, str])
    assert not checker.check_value(1.0, Union[int, str])

def test_check_value_generic_container():
    checker = StandardTypeChecker()
    assert checker.check_value([1, 2], list[float])
    assert not checker.check_value((1, 2), list[float])

def test_check_value_any_and_unresolvable():
    from typing import Any, TypeVar
    checker = StandardTypeChecker()
    assert checker.check_value("anything", Any)
    T = TypeVar("T")
    assert checker.check_value(123, T)  # unresolvable annotation -> no constraint


# --- StandardTypeChecker.check_types ---

def test_check_types_exact_and_mismatch():
    checker = StandardTypeChecker()
    assert checker.check_types(float, float)
    assert not checker.check_types(str, float)

def test_check_types_subclass():
    class Base: pass
    class Derived(Base): pass
    checker = StandardTypeChecker()
    assert checker.check_types(Derived, Base)
    assert not checker.check_types(Base, Derived)

def test_check_types_union_expected():
    checker = StandardTypeChecker()
    assert checker.check_types(int, Union[int, str])
    assert not checker.check_types(float, Union[int, str])

def test_check_types_unresolvable_expected_or_produced():
    from typing import Any, TypeVar
    checker = StandardTypeChecker()
    T = TypeVar("T")
    assert checker.check_types(float, Any)  # unresolvable expected -> no constraint
    assert checker.check_types(T, float)    # unresolvable produced -> no constraint


# --- Step.resolve_input_types / resolve_output_types ---

def test_resolve_input_types_basic():
    def f(a: float, b: str, c): return None
    step = Step(fn=f)
    assert step.resolve_input_types() == {"a": float, "b": str}

def test_resolve_input_types_unresolvable_annotation():
    def f(a): pass
    f.__annotations__ = {"a": "Invalid[Syntax"}
    step = Step(fn=f)
    assert step.resolve_input_types() == {}

def test_resolve_output_types_single():
    def f(x: float) -> float: return x
    step = Step(fn=f)
    assert step.resolve_output_types() == {"f": float}

def test_resolve_output_types_no_annotation():
    def f(): return 1
    step = Step(fn=f)
    assert step.resolve_output_types() == {}

def test_resolve_output_types_unresolvable_annotation():
    def f(): pass
    f.__annotations__ = {"return": "Invalid[Syntax"}
    step = Step(fn=f)
    assert step.resolve_output_types() == {}

def test_resolve_output_types_dataclass():
    @dataclass
    class Result:
        a: float
        b: str
    def f() -> Result: pass
    step = Step(fn=f)
    assert step.resolve_output_types() == {"a": float, "b": str}

def test_resolve_output_types_manual_tuple_match():
    def f() -> tuple[float, str]: pass
    step = Step(fn=f, manual_outputs=["a", "b"])
    assert step.resolve_output_types() == {"a": float, "b": str}

def test_resolve_output_types_manual_tuple_length_mismatch():
    def f() -> tuple[float, str]: pass
    step = Step(fn=f, manual_outputs=["a", "b", "c"])
    assert step.resolve_output_types() == {}

def test_resolve_output_types_manual_tuple_ellipsis():
    def f() -> tuple[float, ...]: pass
    step = Step(fn=f, manual_outputs=["a", "b"])
    assert step.resolve_output_types() == {}

def test_resolve_output_types_multi_output_no_type_info():
    # Manual outputs, but return annotation isn't a matching tuple -> can't map types.
    def f() -> float: pass
    step = Step(fn=f, manual_outputs=["a", "b"])
    assert step.resolve_output_types() == {}


# --- validate_structure (static graph validation) ---

def test_validate_structure_compatible_passes():
    def producer() -> float: return 1.0
    def consumer(x: float): return x
    steps = [Step(fn=producer, manual_outputs=["x"]), Step(fn=consumer)]
    validate_structure(steps)  # should not raise

def test_validate_structure_mismatch_raises():
    def producer() -> str: return "1.0"
    def consumer(x: float): return x
    steps = [Step(fn=producer, manual_outputs=["x"]), Step(fn=consumer)]
    with pytest.raises(TypeMismatchError, match="Type mismatch on 'x'"):
        validate_structure(steps)

def test_validate_structure_skips_external_inputs():
    def consumer(x: float): return x
    steps = [Step(fn=consumer)]
    validate_structure(steps)  # 'x' isn't produced internally -> nothing to check

def test_validate_structure_skips_untyped_producer():
    def producer(): return 1.0  # no return annotation
    def consumer(x: float): return x
    steps = [Step(fn=producer, manual_outputs=["x"]), Step(fn=consumer)]
    validate_structure(steps)  # producer type unknown -> skip


# --- validate_external_inputs ---

def test_validate_external_inputs_compatible_passes():
    def consumer(x: float): return x
    validate_external_inputs([Step(fn=consumer)], {"x": 1.0})

def test_validate_external_inputs_mismatch_raises():
    def consumer(x: float): return x
    with pytest.raises(TypeMismatchError, match="Input 'x'"):
        validate_external_inputs([Step(fn=consumer)], {"x": "not a float"})

def test_validate_external_inputs_skips_produced_vars():
    def producer() -> float: return 1.0
    def consumer(x: float): return x
    steps = [Step(fn=producer, manual_outputs=["x"]), Step(fn=consumer)]
    # 'x' is provided as an external input too, but since it's produced
    # internally it's validated structurally, not here.
    validate_external_inputs(steps, {"x": "irrelevant"})


# --- End-to-end via Pipeline ---

def test_pipeline_static_validation_runs_once(monkeypatch):
    import smartmdao.core as core_module
    calls = []
    original = core_module.validate_structure
    def spy(steps, checker):
        calls.append(1)
        return original(steps, checker)
    monkeypatch.setattr(core_module, "validate_structure", spy)

    def step_fn(x: float) -> float: return x + 1
    pipeline = Pipeline()
    pipeline.add(step_fn, outputs=["y"])

    pipeline.run(x=1.0)
    pipeline.run(x=2.0)
    assert len(calls) == 1  # cached across calls with the same shape

    pipeline.add(lambda: 1.0, outputs=["z"])
    pipeline.run(x=3.0)
    assert len(calls) == 2  # invalidated after adding a new step

def test_pipeline_rejects_bad_external_input():
    def consumer(x: float) -> float: return x
    pipeline = Pipeline()
    pipeline.add(consumer, outputs=["y"])
    with pytest.raises(TypeMismatchError):
        pipeline.run(x="not a float")

def test_pipeline_rejects_structural_mismatch_before_running():
    calls = []
    def producer() -> str:
        calls.append("producer")
        return "1.0"
    def consumer(x: float):
        calls.append("consumer")
        return x
    pipeline = Pipeline()
    pipeline.add(producer, outputs=["x"])
    pipeline.add(consumer, outputs=["y"])
    with pytest.raises(TypeMismatchError):
        pipeline.run()
    assert calls == []  # nothing executed - caught before running

def test_pipeline_runtime_type_checks_catch_lying_annotation():
    # Annotated to return a float, but actually returns a string - only
    # detectable at runtime, which is why runtime_type_checks is opt-in.
    def buggy_step(x: float) -> float:
        return str(x)

    pipeline = Pipeline(runtime_type_checks=True)
    pipeline.add(buggy_step, outputs=["y"])
    with pytest.raises(TypeMismatchError, match="produced y="):
        pipeline.run(x=1.0)

def test_pipeline_runtime_type_checks_off_by_default_lets_bug_through():
    def buggy_step(x: float) -> float:
        return str(x)

    pipeline = Pipeline()
    pipeline.add(buggy_step, outputs=["y"])
    result = pipeline.run(x=1.0)
    assert result["y"] == "1.0"  # bug not caught: runtime checks are opt-in

def test_pipeline_runtime_type_checks_catch_bad_producer_before_consumer_runs():
    calls = []
    def bad_producer() -> float:
        calls.append("bad_producer")
        return "oops"  # lies about its declared type
    def consumer(x: float) -> float:
        calls.append("consumer")
        return x + 1.0

    pipeline = Pipeline(runtime_type_checks=True)
    pipeline.add(bad_producer, outputs=["x"])
    pipeline.add(consumer, outputs=["y"])
    with pytest.raises(TypeMismatchError):
        pipeline.run()
    assert calls == ["bad_producer"]  # consumer never got a chance to run

def test_runtime_type_checks_propagate_through_iterative_solver():
    def buggy_step(x: float) -> float:
        return str(float(x) + 1) if x < 2 else x  # lies on the first iteration
    solver = IterativeSolver(max_iterations=3)
    pipeline = Pipeline(solver=solver, runtime_type_checks=True)
    pipeline.add(buggy_step, outputs=["x"])
    with pytest.raises(TypeMismatchError):
        pipeline.run(x=0.0)

def test_executor_input_type_mismatch_raises():
    # Exercises StepExecutor directly so a bad value can reach an annotated
    # parameter without first being caught by Pipeline's external-input check.
    def consumer(x: float): return x
    step = Step(fn=consumer)
    memory = {"x": "not a float"}
    with pytest.raises(TypeMismatchError, match="received x="):
        StepExecutor.run_step(step, memory, type_checker=StandardTypeChecker())

def test_pipeline_runtime_type_checks_ignore_unannotated_params():
    # 'label' has no type hint, so it's never checked - only 'x' is.
    def step(x: float, label): return x

    pipeline = Pipeline(runtime_type_checks=True)
    pipeline.add(step, outputs=["y"])
    result = pipeline.run(x=1.0, label="anything goes")
    assert result["y"] == 1.0

def test_runtime_type_checks_propagate_through_hybrid_solver_cycle():
    def a_step(b: float) -> float: return "not a float"
    def b_step(a: float) -> float: return a + 0.1
    pipeline = Pipeline(solver=HybridSolver(max_iterations=3), runtime_type_checks=True)
    pipeline.add(a_step, outputs=["a"])
    pipeline.add(b_step, outputs=["b"])
    with pytest.raises(TypeMismatchError):
        pipeline.run(b=1.0)
