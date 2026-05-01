import pytest
from smart_pipeline.core import Pipeline
from smart_pipeline.visualization import PipelineVisualizer

def test_pipeline_decorators_and_run():
    pipeline = Pipeline()
    
    @pipeline.step(outputs=["greeting"])
    def make_greeting(name):
        return f"Hello, {name}"

    result = pipeline.run(name="Alice")
    assert result["greeting"] == "Hello, Alice"

def test_pipeline_visualization(tmp_path):
    pipeline = Pipeline()
    
    @pipeline.step(outputs=["b"])
    def step_one(a): return a
    
    @pipeline.step(outputs=["c"])
    def step_two(b): return b
    
    # Test flow visualization output
    out_path = tmp_path / "flow_graph"
    pipeline.visualize(inputs=["a"], output_path=str(out_path), view=False, graph_type="flow")
    
    # Test bipartite visualization output
    out_path_bi = tmp_path / "bi_graph"
    pipeline.visualize(inputs=["a"], output_path=str(out_path_bi), view=False, graph_type="bipartite")
    
    assert (tmp_path / "flow_graph.pdf").exists()
    assert (tmp_path / "bi_graph.pdf").exists()