import pytest
from dataclasses import dataclass
from smartmdao.models import Step
from smartmdao.executor import StepExecutor

@dataclass
class MyData:
    x: int
    y: int

def test_executor_missing_inputs():
    step = Step(fn=lambda a, b: a + b)
    memory = {"a": 1} # Missing 'b'
    with pytest.raises(KeyError, match="Missing inputs: \\['b'\\]"):
        StepExecutor.run_step(step, memory)

def test_executor_returns_none():
    step = Step(fn=lambda a: None)
    memory = {"a": 1}
    StepExecutor.run_step(step, memory)
    assert len(memory) == 1 # Memory unchanged

def test_executor_manual_outputs_dict():
    step = Step(fn=lambda: {"a": 1, "b": 2}, manual_outputs=["a", "b"])
    memory = {}
    StepExecutor.run_step(step, memory)
    assert memory["a"] == 1 and memory["b"] == 2

def test_executor_manual_outputs_tuple():
    step = Step(fn=lambda: (1, 2), manual_outputs=["a", "b"])
    memory = {}
    StepExecutor.run_step(step, memory)
    assert memory["a"] == 1 and memory["b"] == 2
    
def test_executor_manual_outputs_tuple_mismatch():
    step = Step(fn=lambda: (1,), manual_outputs=["a", "b"])
    with pytest.raises(ValueError, match="expected 2"):
        StepExecutor.run_step(step, {})

def test_executor_dataclass_return():
    def my_fn() -> MyData:
        return MyData(x=10, y=20)
        
    step = Step(fn=my_fn)
    memory = {}
    StepExecutor.run_step(step, memory)
    assert memory["x"] == 10 and memory["y"] == 20