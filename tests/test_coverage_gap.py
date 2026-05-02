import pytest
from dataclasses import dataclass
from smartmdao.models import Step
from smartmdao.core import Pipeline
from smartmdao.cache import CacheBackend
from smartmdao.utils import resolve_output_names as utils_resolve
from smartmdao.logging_config import configure_logging, get_logger
from smartmdao.executor import StepExecutor

def test_utils_resolve_output_names():
    # Hits utils.py which is currently at 0%
    def my_func(): pass
    step = Step(fn=my_func)
    assert utils_resolve(step) == ["my_func"]
    
    @dataclass
    class MyDC:
        a: int
    def my_dc_func() -> MyDC: pass
    step2 = Step(fn=my_dc_func)
    assert "a" in utils_resolve(step2)

def test_utils_resolve_manual_outputs():
    # Hits utils.py line 12 (manual outputs override)
    step = Step(fn=lambda: 1, manual_outputs=["custom_out"])
    assert utils_resolve(step) == ["custom_out"]

def test_pipeline_step_no_args():
    # Hits the `if fn is not None` block in Pipeline.step
    pipeline = Pipeline()
    @pipeline.step
    def auto_step(): return 1
    assert len(pipeline.steps) == 1

def test_pipeline_run_exception():
    # Hits the Exception branch in Pipeline.run
    pipeline = Pipeline()
    @pipeline.step
    def crash_step(): raise ValueError("Boom")
    # The StepExecutor wraps inner exceptions in a RuntimeError to add context
    with pytest.raises(RuntimeError, match="Boom"):
        pipeline.run()

def test_abstract_cache_backend():
    # Hits the 'pass' statements in the Abstract Base Class
    class DummyCache(CacheBackend):
        def get(self, f, k): super().get(f, k)
        def set(self, f, k, v): super().set(f, k, v)
        def has(self, f, k): super().has(f, k)
    
    d = DummyCache()
    d.get(1, 2)
    d.set(1, 2, 3)
    d.has(1, 2)

def test_cache_memory_backend():
    # Explicitly hit standard MemoryBackend
    from smartmdao.cache import MemoryBackend
    b = MemoryBackend()
    b.set("f", "k", "v")
    assert b.has("f", "k")
    assert b.get("f", "k") == "v"

def test_cache_hdf5_overwrite(tmp_path):
    # Hits the HDF5 overwrite key branch
    from smartmdao.cache import HDF5Backend
    backend = HDF5Backend(filepath=str(tmp_path / "test.h5"))
    backend.set("func", "key", 1)
    backend.set("func", "key", 2)

def test_logging_and_main():
    # Covers the logging_config module
    logger = configure_logging()
    assert logger is not None
    
    mod_logger = get_logger("test_module")
    assert mod_logger.name == "test_module"

def test_main_execution():
    # Hits main.py line 8 by simulating script execution
    import runpy
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        runpy.run_module("smartmdao.main", run_name="__main__")

def test_executor_tuple_return_not_iterable():
    # Hits TypeError in executor.py when manual_outputs mismatch real returns
    step = Step(fn=lambda: 1, manual_outputs=["a", "b"])
    with pytest.raises(TypeError, match="expected iterable"):
        StepExecutor.run_step(step, {})

def test_executor_dict_missing_key():
    # Hits KeyError in executor.py when a dictionary is missing a promised key
    step = Step(fn=lambda: {"a": 1}, manual_outputs=["a", "b"])
    with pytest.raises(KeyError, match="expected output key 'b'"):
        StepExecutor.run_step(step, {})

def test_executor_single_default_output():
    # Hits executor.py line 90 (single default output without manual configs)
    step = Step(fn=lambda: 42)
    memory = {}
    StepExecutor.run_step(step, memory)
    assert memory["<lambda>"] == 42

def test_models_resolve_exception_fallback():
    # Forces get_type_hints to fail to hit the exception fallback in models.py
    def bad_hints_func(): pass
    bad_hints_func.__annotations__ = {"return": "Invalid[Syntax"}
    step = Step(fn=bad_hints_func)
    assert step.resolve_output_names() == ["bad_hints_func"]

def test_hdf5_missing_file(tmp_path):
    # Hits the HDF5Backend early return when a file doesn't exist
    from smartmdao.cache import HDF5Backend
    backend = HDF5Backend(filepath=str(tmp_path / "does_not_exist.h5"))
    assert not backend.has("func", "key")

def test_visualization_missing_inputs(tmp_path):
    # Covers missing inputs styling & view=False logic in visualization.py
    from smartmdao.visualization import PipelineVisualizer
    step = Step(fn=lambda missing_var: missing_var)
    viz = PipelineVisualizer([step], input_keys=set())
    viz.build(graph_type="flow").render(output_path=str(tmp_path / "f.pdf"), view=False)
    viz.build(graph_type="bipartite").render(output_path=str(tmp_path / "b.pdf"), view=False)

def test_visualization_no_extension(tmp_path):
    # Hits visualization.py line 126 (fallback to 'pdf' when no extension is provided)
    from smartmdao.visualization import PipelineVisualizer
    step = Step(fn=lambda x: x)
    viz = PipelineVisualizer([step], input_keys={"x"})
    viz.build().render(output_path=str(tmp_path / "graph_no_ext"), view=False)

def test_visualization_graphviz_missing(monkeypatch):
    # Simulates what happens if graphviz isn't installed (hits visualization.py top level exception)
    import sys
    import importlib
    import smartmdao.visualization as viz
    
    # Hide graphviz
    monkeypatch.setitem(sys.modules, "graphviz", None)
    
    # Reload the module to trigger the ImportError except block at the top
    importlib.reload(viz)
    assert viz.graphviz is None
    
    with pytest.raises(ImportError, match="library is required"):
        viz.PipelineVisualizer([], set())
        
    # Restore for other tests
    monkeypatch.undo()
    importlib.reload(viz)

def test_visualization_render_exception(monkeypatch, tmp_path):
    # Hits the exception fallback in PipelineVisualizer.render
    from smartmdao.visualization import PipelineVisualizer
    step = Step(fn=lambda x: x)
    viz = PipelineVisualizer([step], input_keys={"x"})
    viz.build()
    
    def mock_render(*args, **kwargs):
        raise RuntimeError("Mock Viewer Failed")
        
    monkeypatch.setattr(viz.dot, "render", mock_render)
    monkeypatch.setattr(viz.dot, "view", mock_render)
    
    # Trigger exception without output_path
    viz.render()
    
    # Trigger exception with output_path
    viz.render(output_path=str(tmp_path / "fail.pdf"))

def test_visualization_render_view_success(monkeypatch):
    # Hits visualization.py line 126 (successful viewer opening log)
    from smartmdao.visualization import PipelineVisualizer
    step = Step(fn=lambda x: x)
    viz = PipelineVisualizer([step], input_keys={"x"})
    viz.build()
    
    # Mock view to prevent actually opening a PDF viewer during tests
    monkeypatch.setattr(viz.dot, "view", lambda cleanup: None)
    
    # Trigger the successful path without an output_path
    viz.render()

def test_iterative_solver_no_numeric():
    # Covers string variable fallbacks in the iterative solver residual check
    from smartmdao.solvers import IterativeSolver
    steps = [Step(fn=lambda s: s + "a", manual_outputs=["s"])]
    solver = IterativeSolver(max_iterations=2)
    result = solver.solve(steps, {"s": ""})
    assert result["s"] == "aa"

def test_iterative_solver_target_var_non_numeric():
    # Covers target_var not numeric fallback (solvers.py 138-139)
    from smartmdao.solvers import IterativeSolver
    steps = [Step(fn=lambda s: s + "a", manual_outputs=["s"])]
    solver = IterativeSolver(max_iterations=2, target_var="s")
    result = solver.solve(steps, {"s": ""})
    assert result["s"] == "aa"
    
def test_iterative_solver_empty_produced():
    # Covers fallback when produced_vars is completely empty
    from smartmdao.solvers import IterativeSolver
    step = Step(fn=lambda x: None, manual_outputs=[])
    solver = IterativeSolver(max_iterations=2)
    solver.solve([step], {"x": 1})

def test_iterative_solver_execution_order():
    # Hits solvers.py lines 138-139 (manual execution order resolution)
    from smartmdao.solvers import IterativeSolver
    def stp1(x): return x+1
    def stp2(x): return x*2
    step1 = Step(fn=stp1, manual_outputs=["x"])
    step2 = Step(fn=stp2, manual_outputs=["x"])
    solver = IterativeSolver(max_iterations=1, execution_order=["stp2", "stp1", "missing_step"])
    solver.solve([step1, step2], {"x": 0})

def test_hybrid_solver_disconnected():
    # Covers disjoint graphs in the hybrid solver's SCC finder
    from smartmdao.solvers import HybridSolver
    steps = [
        Step(fn=lambda a: a + 1, manual_outputs=["b"]),
        Step(fn=lambda c: c + 1, manual_outputs=["d"])
    ]
    solver = HybridSolver()
    res = solver.solve(steps, {"a": 1, "c": 1})
    assert res["b"] == 2 and res["d"] == 2