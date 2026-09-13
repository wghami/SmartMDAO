import asyncio

import pytest

from smartmdao.mcp import (
    PipelineLoadError,
    analyze_pipeline,
    explain_pipeline,
    load_pipeline,
    render_pipeline_diagram,
    render_xdsm,
    validate_pipeline,
)
from smartmdao.mcp import handlers
from smartmdao.mcp.rendering import force_headless_backend

SELLAR = '''
import math
from smartmdao import Pipeline, HybridSolver

pipeline = Pipeline(solver=HybridSolver())

@pipeline.step(outputs=["y1"])
def discipline_1(z1: float, x1: float, y2: float) -> float:
    return z1 + x1 - 0.2 * y2

@pipeline.step(outputs=["y2"])
def discipline_2(z1: float, y1: float) -> float:
    return math.sqrt(abs(y1)) + z1
'''


@pytest.fixture
def sellar_file(tmp_path):
    path = tmp_path / "sellar.py"
    path.write_text(SELLAR)
    return path


# --- loader ------------------------------------------------------------------

def test_loads_the_only_pipeline_in_a_file(sellar_file):
    loaded = load_pipeline(sellar_file)
    assert loaded.variable == "pipeline"
    assert loaded.path == sellar_file.resolve()
    assert len(loaded.pipeline.steps) == 2


def test_loads_a_named_pipeline(tmp_path):
    path = tmp_path / "two.py"
    path.write_text(
        "from smartmdao import Pipeline\n"
        "first = Pipeline()\n"
        "second = Pipeline()\n"
        "second.add(lambda a: a, outputs=['b'])\n"
    )
    assert len(load_pipeline(path, variable="second").pipeline.steps) == 1


def test_ambiguous_file_lists_the_candidates(tmp_path):
    path = tmp_path / "two.py"
    path.write_text(
        "from smartmdao import Pipeline\nfirst = Pipeline()\nsecond = Pipeline()\n"
    )
    with pytest.raises(PipelineLoadError, match=r"several pipelines.*first.*second"):
        load_pipeline(path)


def test_file_with_no_pipeline_is_reported(tmp_path):
    path = tmp_path / "empty.py"
    path.write_text("x = 1\n")
    with pytest.raises(PipelineLoadError, match="No Pipeline found"):
        load_pipeline(path)


def test_named_variable_that_is_not_a_pipeline(sellar_file):
    with pytest.raises(PipelineLoadError, match="is not a Pipeline"):
        load_pipeline(sellar_file, variable="discipline_1")


def test_missing_file(tmp_path):
    with pytest.raises(PipelineLoadError, match="No such file"):
        load_pipeline(tmp_path / "nope.py")


def test_non_python_file(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    with pytest.raises(PipelineLoadError, match="Not a Python file"):
        load_pipeline(path)


def test_import_error_is_wrapped_with_the_cause(tmp_path):
    path = tmp_path / "broken.py"
    path.write_text("raise ValueError('boom')\n")
    with pytest.raises(PipelineLoadError, match="Importing broken.py failed.*boom"):
        load_pipeline(path)


def test_private_pipeline_variables_are_ignored(tmp_path):
    path = tmp_path / "private.py"
    path.write_text(
        "from smartmdao import Pipeline\n_hidden = Pipeline()\nvisible = Pipeline()\n"
    )
    assert load_pipeline(path).variable == "visible"


def test_sibling_imports_resolve(tmp_path):
    (tmp_path / "helper.py").write_text("FACTOR = 3\n")
    path = tmp_path / "uses_helper.py"
    path.write_text(
        "from smartmdao import Pipeline\n"
        "from helper import FACTOR\n"
        "pipeline = Pipeline()\n"
        "pipeline.add(lambda a: a * FACTOR, outputs=['b'])\n"
    )
    assert len(load_pipeline(path).pipeline.steps) == 1


def test_reloading_the_same_path_gets_a_fresh_object(sellar_file):
    first = load_pipeline(sellar_file)
    second = load_pipeline(sellar_file)
    assert first.pipeline is not second.pipeline


# --- analyze_pipeline --------------------------------------------------------

def test_analyze_reports_the_cycle_and_the_seed(sellar_file):
    result = analyze_pipeline(str(sellar_file), inputs=["z1", "x1"])

    assert result["ok"] is True
    assert result["pipeline"] == "pipeline"
    assert result["recommended_solver"] == "HybridSolver"
    assert result["cycles"][0]["feedback_variables"] == ["y1", "y2"]
    assert result["initial_guesses_required"] == [
        {"variable": "y2", "consumed_by": "discipline_1"}
    ]


def test_analyze_reports_load_failures_as_data_not_exceptions(tmp_path):
    result = analyze_pipeline(str(tmp_path / "nope.py"))
    assert result["ok"] is False
    assert "No such file" in result["error"]


def test_analyze_accepts_an_explicit_variable(sellar_file):
    assert analyze_pipeline(str(sellar_file), variable="pipeline")["ok"] is True


# --- validate_pipeline -------------------------------------------------------

def test_validate_flags_the_unseeded_feedback_variable(sellar_file):
    result = validate_pipeline(str(sellar_file), inputs=["z1", "x1"])

    assert result["valid"] is False
    assert result["counts"] == {"error": 1}
    assert result["findings"][0]["code"] == "initial-guess-required"


def test_validate_passes_a_correct_pipeline(sellar_file):
    result = validate_pipeline(str(sellar_file), inputs=["z1", "x1", "y2"])
    assert result["valid"] is True
    assert result["findings"] == []


def test_validate_reports_load_failures(tmp_path):
    assert validate_pipeline(str(tmp_path / "nope.py"))["ok"] is False


def test_validate_stays_valid_when_only_warnings_are_present(tmp_path):
    path = tmp_path / "warn.py"
    path.write_text(
        "from smartmdao import Pipeline, IterativeSolver\n"
        "pipeline = Pipeline(solver=IterativeSolver())\n"
        "@pipeline.step(outputs=['c'])\n"
        "def increment(b: float) -> float: return b + 1\n"
        "@pipeline.step(outputs=['b'])\n"
        "def double(a: float) -> float: return a * 2\n"
    )
    result = validate_pipeline(str(path), inputs=["a", "b"])
    assert result["valid"] is True                       # warnings only
    assert "warning" in result["counts"]


# --- explain_pipeline --------------------------------------------------------

def test_explain_returns_prose(sellar_file):
    result = explain_pipeline(str(sellar_file), inputs=["z1", "x1", "y2"])
    assert result["ok"] is True
    assert "Recommended solver: HybridSolver" in result["explanation"]


def test_explain_reports_load_failures(tmp_path):
    assert explain_pipeline(str(tmp_path / "nope.py"))["ok"] is False


# --- rendering ---------------------------------------------------------------

def test_render_writes_a_diagram(sellar_file, tmp_path):
    destination = tmp_path / "out" / "diagram.png"
    result = render_pipeline_diagram(
        str(sellar_file), str(destination), inputs=["z1", "x1", "y2"]
    )

    assert result["ok"] is True
    assert destination.exists()
    assert destination.stat().st_size > 0


def test_render_defaults_to_pdf_without_an_extension(sellar_file, tmp_path):
    result = render_pipeline_diagram(str(sellar_file), str(tmp_path / "diagram"))
    assert result["output_path"].endswith(".pdf")
    assert (tmp_path / "diagram.pdf").exists()


def test_render_creates_missing_parent_directories(sellar_file, tmp_path):
    destination = tmp_path / "deeply" / "nested" / "d.png"
    assert render_pipeline_diagram(str(sellar_file), str(destination))["ok"] is True
    assert destination.exists()


def test_render_rejects_a_bad_orientation(sellar_file, tmp_path):
    result = render_pipeline_diagram(
        str(sellar_file), str(tmp_path / "d.png"), orientation="sideways"
    )
    assert result["ok"] is False
    assert "orientation must be one of" in result["error"]


def test_render_rejects_a_bad_graph_type(sellar_file, tmp_path):
    result = render_pipeline_diagram(
        str(sellar_file), str(tmp_path / "d.png"), graph_type="spaghetti"
    )
    assert result["ok"] is False
    assert "graph_type must be one of" in result["error"]


def test_render_reports_load_failures(tmp_path):
    assert render_pipeline_diagram(str(tmp_path / "nope.py"), str(tmp_path / "d.png"))["ok"] is False


def test_render_xdsm_accepts_the_bipartite_graph_type(sellar_file, tmp_path):
    loaded = load_pipeline(sellar_file)
    written = render_xdsm(
        loaded.pipeline, tmp_path / "b.png", graph_type="bipartite", orientation="LR"
    )
    assert written.exists()


def test_force_headless_backend_selects_agg():
    import matplotlib

    force_headless_backend()
    assert matplotlib.get_backend().lower() == "agg"


# --- truncation --------------------------------------------------------------

def test_long_lists_are_truncated(monkeypatch):
    monkeypatch.setattr(handlers, "MAX_ITEMS", 3)
    truncated = handlers._truncate(["a", "b", "c", "d", "e"])
    assert truncated == ["a", "b", "c", "... 2 more omitted"]


def test_short_lists_are_untouched():
    assert handlers._truncate(["a", "b"]) == ["a", "b"]


def test_analyze_truncates_a_large_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(handlers, "MAX_ITEMS", 2)
    path = tmp_path / "big.py"
    path.write_text(
        "from smartmdao import Pipeline\n"
        "pipeline = Pipeline()\n"
        + "".join(
            f"pipeline.add(lambda a: a, outputs=['v{i}'])\n" for i in range(5)
        )
    )
    steps = analyze_pipeline(str(path), inputs=["a"])["steps"]
    assert steps[-1] == "... 3 more omitted"


# --- server wiring -----------------------------------------------------------

def test_server_registers_every_tool_resource_and_prompt():
    from smartmdao.mcp import create_server

    server = create_server()

    async def collect():
        return (
            [t.name for t in await server.list_tools()],
            [str(r.uri) for r in await server.list_resources()],
            [p.name for p in await server.list_prompts()],
        )

    tools, resources, prompts = asyncio.run(collect())

    assert set(tools) == {
        "analyze_pipeline",
        "validate_pipeline",
        "explain_pipeline",
        "render_pipeline_diagram",
    }
    assert "smartmdao://docs/architecture" in resources
    assert "smartmdao://examples/sellar" in resources
    assert set(prompts) == {"pipeline_from_prose", "review_pipeline"}


def test_server_tools_delegate_to_the_handlers(sellar_file):
    """The protocol layer must stay a pass-through, not grow its own logic."""
    import json

    from smartmdao.mcp import create_server

    server = create_server()

    async def call(name, arguments):
        return await server.call_tool(name, arguments)

    arguments = {"path": str(sellar_file), "inputs": ["z1", "x1", "y2"]}
    result = asyncio.run(call("analyze_pipeline", arguments))

    assert result.is_error is False
    through_protocol = json.loads(result.content[0].text)
    assert through_protocol == analyze_pipeline(**arguments)


def test_server_surfaces_handler_errors_as_normal_results(tmp_path):
    """A missing file is data the agent can act on, not a protocol failure."""
    import json

    from smartmdao.mcp import create_server

    server = create_server()
    result = asyncio.run(
        server.call_tool("analyze_pipeline", {"path": str(tmp_path / "nope.py")})
    )

    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload["ok"] is False
    assert "No such file" in payload["error"]


@pytest.mark.parametrize(
    "tool,extra",
    [
        ("analyze_pipeline", {}),
        ("validate_pipeline", {}),
        ("explain_pipeline", {}),
        ("render_pipeline_diagram", {"output_path": "diagram.png"}),
    ],
)
def test_every_tool_is_callable_through_the_protocol(sellar_file, tmp_path, tool, extra):
    import json

    from smartmdao.mcp import create_server

    if "output_path" in extra:
        extra["output_path"] = str(tmp_path / extra["output_path"])

    arguments = {"path": str(sellar_file), "inputs": ["z1", "x1", "y2"], **extra}
    result = asyncio.run(create_server().call_tool(tool, arguments))

    assert result.is_error is False
    assert json.loads(result.content[0].text)["ok"] is True


@pytest.mark.parametrize(
    "uri,expected",
    [
        ("smartmdao://docs/architecture", "# Architecture"),
        ("smartmdao://docs/known-issues", "Known issues"),
        ("smartmdao://examples/sellar", "smartmdao"),
        ("smartmdao://examples/agent-as-discipline", "Architecture"),
    ],
)
def test_every_resource_is_readable(uri, expected):
    from smartmdao.mcp import create_server

    contents = asyncio.run(create_server().read_resource(uri))
    text = "".join(item.content for item in contents)
    assert expected in text


def test_prose_prompt_includes_the_description_and_the_verify_loop():
    from smartmdao.mcp import create_server

    result = asyncio.run(
        create_server().get_prompt(
            "pipeline_from_prose", {"description": "size a wing spar"}
        )
    )
    text = "".join(
        message.content.text
        for message in result.messages
        if hasattr(message.content, "text")
    )
    assert "size a wing spar" in text
    assert "validate_pipeline" in text


def test_review_prompt_references_the_target_file():
    from smartmdao.mcp import create_server

    result = asyncio.run(
        create_server().get_prompt("review_pipeline", {"path": "/tmp/model.py"})
    )
    text = "".join(
        message.content.text
        for message in result.messages
        if hasattr(message.content, "text")
    )
    assert "/tmp/model.py" in text
    assert "known-issues" in text


def test_resources_return_real_document_content():
    from smartmdao.mcp import server as server_module

    text = server_module._read(server_module._DOCS / "architecture.md")
    assert "# Architecture" in text


def test_unknown_attribute_still_raises():
    import smartmdao.mcp as mcp_package

    with pytest.raises(AttributeError, match="no attribute 'nonsense'"):
        mcp_package.nonsense
