import pytest
from unittest.mock import MagicMock

from smart_pipeline.optimization import PipelineEvaluator

# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def mock_pipeline():
    """
    Creates a mock Pipeline that returns a fixed dictionary when run() is called.
    This allows us to test the evaluator without executing any real steps.
    """
    pipeline = MagicMock()
    pipeline.run.return_value = {
        "objective_out": 42.0,
        "constraint_out": -5.0,
        "some_intermediate": 100.0
    }
    return pipeline

@pytest.fixture
def evaluator(mock_pipeline):
    """
    Instantiates a standard PipelineEvaluator with predefined design vars and constants.
    """
    return PipelineEvaluator(
        pipeline=mock_pipeline,
        design_vars=["x1", "x2"],
        constants={"fixed_var": 99.9}
    )

# ==============================================================================
# Tests
# ==============================================================================

def test_evaluator_initialization(mock_pipeline):
    """Test that default values (like constants=None) are handled correctly."""
    evaluator_no_constants = PipelineEvaluator(
        pipeline=mock_pipeline,
        design_vars=["x1"]
    )
    
    # Assert constants fallback to empty dict
    assert evaluator_no_constants.constants == {}
    
    # Assert initial state
    assert evaluator_no_constants.last_x is None
    assert evaluator_no_constants.last_results is None
    assert evaluator_no_constants.eval_count == 0

def test_evaluate_calls_pipeline_correctly(evaluator, mock_pipeline):
    """Test that the evaluator properly merges design_vars and constants into kwargs."""
    x = [1.0, 2.0]
    result = evaluator.evaluate(x)
    
    # Verify the pipeline.run() was called with the correctly mapped arguments
    mock_pipeline.run.assert_called_once_with(x1=1.0, x2=2.0, fixed_var=99.9)
    
    # Verify the result matches the mock's return value
    assert result["objective_out"] == 42.0
    assert evaluator.eval_count == 1
    
    # Verify state was saved
    assert evaluator.last_x == tuple(x)
    assert evaluator.last_results == result

def test_evaluate_caching_mechanism(evaluator, mock_pipeline):
    """Test that calling evaluate with the same 'x' does not re-trigger pipeline.run()."""
    x = [1.0, 2.0]
    
    # 1st Call
    evaluator.evaluate(x)
    assert evaluator.eval_count == 1
    assert mock_pipeline.run.call_count == 1
    
    # 2nd Call (Same inputs)
    evaluator.evaluate(x)
    # The eval_count and run.call_count should NOT increase
    assert evaluator.eval_count == 1 
    assert mock_pipeline.run.call_count == 1
    
    # 3rd Call (Different inputs)
    x_new = [3.0, 4.0]
    evaluator.evaluate(x_new)
    # The eval_count SHOULD increase now
    assert evaluator.eval_count == 2
    assert mock_pipeline.run.call_count == 2
    
    # Ensure the new arguments were passed
    mock_pipeline.run.assert_called_with(x1=3.0, x2=4.0, fixed_var=99.9)

def test_get_objective(evaluator):
    """Test the objective function factory."""
    # Generate the callable
    obj_func = evaluator.get_objective("objective_out")
    
    # Verify it is a callable function
    assert callable(obj_func)
    
    # Verify it returns the correct extracted value
    x = [1.0, 2.0]
    assert obj_func(x) == 42.0

def test_get_constraint(evaluator):
    """Test the constraint function factory, including the multiplier logic."""
    # 1. Test default multiplier (1.0)
    cons_func_default = evaluator.get_constraint("constraint_out")
    assert callable(cons_func_default)
    assert cons_func_default([1.0, 2.0]) == -5.0
    
    # 2. Test custom multiplier (e.g., -1.0 to flip inequality direction for SciPy)
    cons_func_flipped = evaluator.get_constraint("constraint_out", multiplier=-1.0)
    assert cons_func_flipped([1.0, 2.0]) == 5.0
    
    # 3. Test arbitrary scalar multiplier
    cons_func_scaled = evaluator.get_constraint("constraint_out", multiplier=10.0)
    assert cons_func_scaled([1.0, 2.0]) == -50.0