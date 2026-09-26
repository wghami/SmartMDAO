"""
Declared inputs: `Pipeline(inputs=[...])`, roadmap 6.1.

Which inputs are external is a property of the pipeline, not of each call.
Before this, a contract-first pipeline - signatures only, no `run()` call in the
file - had to be told its 91 inputs on every analyze / validate / render call,
and a name missed once came back as a false `missing-input`.
"""
import pytest

from smartmdao import HybridSolver, Pipeline, analyze, explain, validate
from smartmdao.mcp.handlers import (
    analyze_pipeline,
    explain_pipeline,
    render_pipeline_diagram,
    run_pipeline,
    validate_pipeline,
)


def contract(inputs=("z1", "x1", "y2")):
    """Sellar as a contract: every discipline a stub, so nothing may call one."""
    pipeline = Pipeline(solver=HybridSolver(), inputs=list(inputs))

    @pipeline.step(outputs=["y1"])
    def discipline_1(z1: float, x1: float, y2: float) -> float:
        raise NotImplementedError

    @pipeline.step(outputs=["y2"])
    def discipline_2(z1: float, y1: float) -> float:
        raise NotImplementedError

    return pipeline


def codes(findings):
    return [finding.code for finding in findings]


# ==============================================================================
# The declaration itself
# ==============================================================================

def test_the_declaration_is_kept_in_order_without_repeats():
    assert Pipeline(inputs=["b", "a", "b"]).inputs == ("b", "a")


def test_no_declaration_is_an_empty_tuple():
    assert Pipeline().inputs == ()


def test_a_bare_string_is_refused_rather_than_split_into_letters():
    with pytest.raises(TypeError, match=r"did you mean inputs=\['span'\]"):
        Pipeline(inputs="span")


def test_a_name_that_is_not_a_string_is_refused():
    with pytest.raises(TypeError, match="must be strings"):
        Pipeline(inputs=["span", 3])


# ==============================================================================
# The library uses it when a call passes none
# ==============================================================================

def test_validate_uses_the_declaration():
    """The false positive that motivated this: 35 missing-inputs on a clean file."""
    assert codes(validate(contract())) == ["stub-step", "stub-step"]     # and nothing else


def test_analyze_uses_the_declaration():
    """y2 is declared, so the loop is seeded and nothing is asked for."""
    assert analyze(contract()).initial_guesses_required == ()
    assert analyze(contract(inputs=("z1", "x1"))).initial_guesses_required


def test_an_explicit_list_wins_even_when_empty():
    """Asking "what if nothing were passed?" must still be possible."""
    assert "missing-input" in codes(validate(contract(), inputs=[]))


def test_explain_uses_the_declaration():
    assert "missing-input" not in explain(contract())
    assert "is required by" in explain(contract(), inputs=["x1"])


def test_visualize_draws_the_declared_inputs(monkeypatch):
    import smartmdao.core as core

    seen = {}
    monkeypatch.setattr(core, "visualize_pipeline", lambda **kwargs: seen.update(kwargs))

    contract().visualize(view=False)
    assert seen["inputs"] == {"z1", "x1", "y2"}

    contract().visualize(inputs=["z1"], view=False)
    assert seen["inputs"] == {"z1"}


# ==============================================================================
# A name no step reads
# ==============================================================================

def test_a_declared_name_no_step_reads_is_reported():
    findings = validate(contract(inputs=("z1", "x1", "y2", "x_1")))

    unconsumed = [f for f in findings if f.code == "unconsumed-input"]
    assert [f.variable for f in unconsumed] == ["x_1"]
    assert unconsumed[0].severity == "warning"
    assert "typo" in unconsumed[0].message


def test_it_is_the_only_signal_when_the_typo_hits_a_default():
    """
    Why it is a warning: misspell a parameter that has a default, and nothing
    else fails - the default is used and the value passed in is thrown away.
    """
    pipeline = Pipeline(inputs=["speed", "spn"])

    @pipeline.step(outputs=["lift"])
    def lift(speed: float, span: float = 10.0) -> float:
        return speed * span

    assert codes(validate(pipeline)) == ["unconsumed-input"]


def test_an_explicit_list_is_checked_the_same_way():
    assert "unconsumed-input" in codes(validate(contract(inputs=()), inputs=["z1", "x1", "y2", "q"]))


# ==============================================================================
# Over MCP
# ==============================================================================

CONTRACT_FILE = '''
from smartmdao import Pipeline, HybridSolver

pipeline = Pipeline(solver=HybridSolver(), inputs=["z1", "x1", "y2"])

@pipeline.step(outputs=["y1"])
def discipline_1(z1: float, x1: float, y2: float) -> float:
    raise NotImplementedError

@pipeline.step(outputs=["y2"])
def discipline_2(z1: float, y1: float) -> float:
    raise NotImplementedError
'''


@pytest.fixture
def contract_file(tmp_path):
    path = tmp_path / "contract.py"
    path.write_text(CONTRACT_FILE)
    return path


def test_validate_pipeline_needs_no_input_list(contract_file):
    report = validate_pipeline(str(contract_file))

    assert report["valid"] is True
    assert {f["code"] for f in report["findings"]} == {"stub-step"}
    assert report["inputs_used"] == {
        "requested": [],
        "declared": ["z1", "x1", "y2"],
        "found_in_source": [],
    }


def test_analyze_and_explain_report_where_the_list_came_from(contract_file):
    assert analyze_pipeline(str(contract_file))["inputs_used"]["declared"] == ["z1", "x1", "y2"]
    assert explain_pipeline(str(contract_file))["inputs_used"]["declared"] == ["z1", "x1", "y2"]


def test_a_requested_list_replaces_the_declaration(contract_file):
    report = validate_pipeline(str(contract_file), inputs=["z1"])

    assert report["valid"] is False
    assert report["inputs_used"]["requested"] == ["z1"]
    assert report["inputs_used"]["declared"] == []


def test_the_files_own_run_call_is_still_added(tmp_path):
    path = tmp_path / "partial.py"
    path.write_text(
        CONTRACT_FILE.replace('inputs=["z1", "x1", "y2"]', 'inputs=["z1", "x1"]')
        + '\nif __name__ == "__main__":\n    pipeline.run(z1=1.0, x1=0.0, y2=1.0)\n'
    )
    report = validate_pipeline(str(path))

    assert report["valid"] is True
    assert report["inputs_used"]["declared"] == ["z1", "x1"]
    assert report["inputs_used"]["found_in_source"] == ["y2"]


def test_render_uses_the_declaration_and_says_so(contract_file, tmp_path):
    report = render_pipeline_diagram(str(contract_file), str(tmp_path / "xdsm.pdf"))

    assert report["ok"] is True
    assert report["inputs_used"]["declared"] == ["z1", "x1", "y2"]


def test_run_pipeline_names_declared_inputs_nobody_supplied(tmp_path):
    path = tmp_path / "doubling.py"
    path.write_text(
        "from smartmdao import Pipeline\n"
        "pipeline = Pipeline(inputs=['a', 'b'])\n"
        "@pipeline.step(outputs=['c'])\n"
        "def add(a: float, b: float) -> float: return a + b\n"
    )
    result = run_pipeline(str(path), inputs={"a": 1.0})

    assert result["ok"] is False
    assert result["inputs_used"]["declared_not_supplied"] == ["b"]


def test_run_pipeline_is_quiet_when_everything_declared_was_supplied(tmp_path):
    path = tmp_path / "doubling.py"
    path.write_text(
        "from smartmdao import Pipeline\n"
        "pipeline = Pipeline(inputs=['a'])\n"
        "@pipeline.step(outputs=['b'])\n"
        "def double(a: float) -> float: return a * 2\n"
    )
    result = run_pipeline(str(path), inputs={"a": 1.0})

    assert result["ok"] is True
    assert "declared_not_supplied" not in result["inputs_used"]


# ==============================================================================
# Found on the way: a named factory crashed every handler
# ==============================================================================

def test_an_explicitly_named_factory_can_be_analysed(tmp_path):
    """
    Up to 1.23.0 the loader reused a variable name on this path, so the inputs
    read from source were replaced by the factory's descriptor and every
    handler crashed iterating it: `'Factory' object is not iterable`.
    """
    path = tmp_path / "factory.py"
    path.write_text(
        "from smartmdao import Pipeline\n"
        "def build() -> Pipeline:\n"
        "    pipeline = Pipeline(inputs=['x'])\n"
        "    @pipeline.step(outputs=['y'])\n"
        "    def f(x: float) -> float: return x\n"
        "    return pipeline\n"
        "if __name__ == '__main__':\n"
        "    build().run(x=1.0)\n"
    )
    report = analyze_pipeline(str(path), variable="build")

    assert report["ok"] is True
    assert report["source"] == "factory"
    assert report["inputs_used"]["declared"] == ["x"]
    assert report["inputs_used"]["found_in_source"] == []
