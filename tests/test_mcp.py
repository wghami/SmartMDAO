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


# --- loader: factories -------------------------------------------------------

FACTORY = '''
from smartmdao import Pipeline, HybridSolver

def build() -> Pipeline:
    pipeline = Pipeline(solver=HybridSolver())
    pipeline.add(lambda b: b + 1, outputs=["a"])
    pipeline.add(lambda a: a * 2, outputs=["b"])
    return pipeline
'''


def write(tmp_path, name, source):
    path = tmp_path / name
    path.write_text(source)
    return path


def test_discovers_an_annotated_factory(tmp_path):
    loaded = load_pipeline(write(tmp_path, "factory.py", FACTORY))
    assert loaded.variable == "build"
    assert loaded.source == "factory"
    assert len(loaded.pipeline.steps) == 2


def test_a_module_level_instance_still_wins_and_is_labelled(sellar_file):
    loaded = load_pipeline(sellar_file)
    assert loaded.source == "variable"


def test_string_annotations_are_resolved(tmp_path):
    """`from __future__ import annotations` leaves the annotation a string."""
    source = "from __future__ import annotations\n" + FACTORY
    assert load_pipeline(write(tmp_path, "future.py", source)).variable == "build"


def test_annotations_that_do_not_resolve_at_runtime_still_work(tmp_path):
    """A TYPE_CHECKING-only import leaves an annotation get_type_hints cannot
    resolve. Falling back to the raw string keeps the factory discoverable."""
    source = '''
from __future__ import annotations
from typing import TYPE_CHECKING

import smartmdao

if TYPE_CHECKING:                      # not imported at runtime
    from smartmdao import Pipeline

def build() -> Pipeline:
    pipeline = smartmdao.Pipeline()
    pipeline.add(lambda a: a * 2, outputs=["b"])
    return pipeline
'''
    loaded = load_pipeline(write(tmp_path, "type_checking.py", source))
    assert loaded.variable == "build"
    assert loaded.source == "factory"


def test_a_factory_needing_arguments_names_them(tmp_path):
    source = FACTORY.replace("def build() -> Pipeline:", "def build(solver, tol) -> Pipeline:")
    with pytest.raises(PipelineLoadError, match=r"none can be called without arguments"):
        load_pipeline(write(tmp_path, "needs_args.py", source))

    # ...and the names are in the message, which is the whole point.
    try:
        load_pipeline(write(tmp_path, "needs_args.py", source))
    except PipelineLoadError as error:
        assert "solver" in str(error) and "tol" in str(error)


def test_naming_a_factory_that_needs_arguments_explains_why_not(tmp_path):
    source = FACTORY.replace("def build() -> Pipeline:", "def build(solver) -> Pipeline:")
    with pytest.raises(PipelineLoadError, match=r"needs argument\(s\) \['solver'\]"):
        load_pipeline(write(tmp_path, "named_args.py", source), variable="build")


def test_defaulted_arguments_do_not_block_a_factory(tmp_path):
    source = FACTORY.replace("def build() -> Pipeline:", "def build(tol: float = 1e-6) -> Pipeline:")
    assert load_pipeline(write(tmp_path, "defaulted.py", source)).source == "factory"


def test_varargs_do_not_count_as_required(tmp_path):
    source = FACTORY.replace("def build() -> Pipeline:", "def build(*args, **kwargs) -> Pipeline:")
    assert load_pipeline(write(tmp_path, "varargs.py", source)).source == "factory"


def test_several_factories_are_reported_not_guessed(tmp_path):
    source = FACTORY + FACTORY.replace("def build()", "def build_other()")
    with pytest.raises(PipelineLoadError, match="several pipelines"):
        load_pipeline(write(tmp_path, "two.py", source))


def test_choosing_among_several_factories_by_name(tmp_path):
    source = FACTORY + FACTORY.replace("def build()", "def build_other()")
    loaded = load_pipeline(write(tmp_path, "two.py", source), variable="build_other")
    assert loaded.variable == "build_other"
    assert loaded.source == "factory"


def test_an_instance_and_a_factory_together_are_ambiguous(tmp_path):
    source = FACTORY + "\npipeline = build()\n"
    with pytest.raises(PipelineLoadError, match="several pipelines"):
        load_pipeline(write(tmp_path, "both.py", source))


def test_an_unannotated_function_is_never_called(tmp_path):
    """The promise this layer rests on: we do not call things speculatively."""
    source = '''
from smartmdao import Pipeline

CALLED = []

def run_the_whole_study():          # no -> Pipeline annotation
    CALLED.append(True)
    pipeline = Pipeline()
    pipeline.add(lambda a: a, outputs=["b"])
    return pipeline
'''
    path = write(tmp_path, "unannotated.py", source)
    with pytest.raises(PipelineLoadError, match="No Pipeline found"):
        load_pipeline(path)


def test_an_unannotated_function_can_still_be_named_explicitly(tmp_path):
    source = FACTORY.replace(" -> Pipeline:", ":")
    loaded = load_pipeline(write(tmp_path, "explicit.py", source), variable="build")
    assert loaded.source == "factory"


def test_a_factory_that_raises_is_reported_with_its_cause(tmp_path):
    source = '''
from smartmdao import Pipeline

def build() -> Pipeline:
    raise ValueError("bad config")
'''
    with pytest.raises(PipelineLoadError, match=r"Calling build\(\) .* failed.*bad config"):
        load_pipeline(write(tmp_path, "raises.py", source))


def test_a_factory_returning_the_wrong_type_is_reported(tmp_path):
    source = '''
from smartmdao import Pipeline

def build() -> Pipeline:
    return "not a pipeline"
'''
    with pytest.raises(PipelineLoadError, match="returned str"):
        load_pipeline(write(tmp_path, "wrong.py", source))


def test_imported_pipeline_factories_are_ignored(tmp_path):
    """A factory imported from elsewhere is not this file's API."""
    write(tmp_path, "library.py", FACTORY)
    source = "from library import build\n"
    with pytest.raises(PipelineLoadError, match="No Pipeline found"):
        load_pipeline(write(tmp_path, "importer.py", source))


def test_naming_something_that_is_neither_lists_what_is_available(tmp_path):
    source = FACTORY + "\nNOT_A_PIPELINE = 42\n"
    with pytest.raises(PipelineLoadError, match="not a Pipeline or a Pipeline factory"):
        load_pipeline(write(tmp_path, "neither.py", source), variable="NOT_A_PIPELINE")


def test_a_pipeline_trapped_in_a_function_body_says_so(tmp_path):
    source = '''
from smartmdao import Pipeline

def run_demo():
    pipeline = Pipeline()       # local, never returned - unreachable
    pipeline.add(lambda a: a, outputs=["b"])
    print(pipeline.run(a=1))
'''
    with pytest.raises(PipelineLoadError, match="cannot be reached without running"):
        load_pipeline(write(tmp_path, "trapped.py", source))


def test_handlers_report_how_the_pipeline_was_obtained(tmp_path):
    path = write(tmp_path, "factory.py", FACTORY)
    assert analyze_pipeline(str(path), inputs=["b"])["source"] == "factory"


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
        "smartmdao_cookbook",
        "run_pipeline",
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
        ("smartmdao_cookbook", {"_no_path": True}),
        ("run_pipeline", {"_no_inputs": True}),
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

    if extra.pop("_no_path", False):
        arguments = {}          # the cookbook takes no pipeline
    elif extra.pop("_no_inputs", False):
        # run_pipeline takes a dict of VALUES, not a list of names - and this
        # fixture never calls run(), so nothing is recoverable from its source.
        arguments = {
            "path": str(sellar_file),
            "inputs": {"z1": 1.0, "x1": 0.0, "y2": 1.0},
        }
    else:
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


# --- declared inputs, recovered from the source ------------------------------

def test_declared_inputs_from_direct_keywords(tmp_path):
    from smartmdao.mcp.loader import declared_inputs

    path = write(tmp_path, "direct.py", SELLAR + "\npipeline.run(z1=1.0, y2=2.0)\n")
    assert declared_inputs(path) == ("y2", "z1")


def test_declared_inputs_from_a_splatted_dict_literal(tmp_path):
    """The shape the repository's own benchmarks use."""
    from smartmdao.mcp.loader import declared_inputs

    source = SELLAR + '\ninputs = {"z1": 1.0, "x1": 0.0, "y2": 1.0}\npipeline.run(**inputs)\n'
    assert declared_inputs(write(tmp_path, "splat.py", source)) == ("x1", "y2", "z1")


def test_declared_inputs_ignores_what_it_cannot_see(tmp_path):
    """Dynamic construction narrows the guessing; it does not eliminate it."""
    from smartmdao.mcp.loader import declared_inputs

    source = SELLAR + "\ninputs = dict(z1=1.0)\npipeline.run(**inputs)\n"
    assert declared_inputs(write(tmp_path, "dynamic.py", source)) == ()


def test_a_loaded_pipeline_carries_its_declared_inputs(sellar_file):
    loaded = load_pipeline(sellar_file)
    assert loaded.declared_inputs == ()          # this fixture never calls run()


def test_the_seed_a_file_already_passes_is_not_reported_as_missing(tmp_path):
    """The false positive that prompted this.

    An agent called validate with the design variables and forgot the cycle's
    seed. The tool then flagged as missing precisely what the agent forgot to
    mention - and reported a working file as broken.
    """
    source = SELLAR + '\ninputs = {"z1": 1.0, "x1": 0.0, "y2": 1.0}\npipeline.run(**inputs)\n'
    path = write(tmp_path, "seeded.py", source)

    report = validate_pipeline(str(path), inputs=["z1", "x1"])

    assert report["valid"] is True
    assert report["findings"] == []
    assert report["inputs_used"] == {
        "requested": ["z1", "x1"],
        "found_in_source": ["y2"],
    }


def test_inputs_found_in_source_are_reported_separately(tmp_path):
    """Provenance, so the agent can say where a value came from."""
    source = SELLAR + '\npipeline.run(z1=1.0, x1=0.0, y2=1.0)\n'
    path = write(tmp_path, "provenance.py", source)

    result = analyze_pipeline(str(path))
    assert result["inputs_used"]["requested"] == []
    assert set(result["inputs_used"]["found_in_source"]) == {"z1", "x1", "y2"}


def test_explain_reports_provenance_too(tmp_path):
    source = SELLAR + '\npipeline.run(z1=1.0, x1=0.0, y2=1.0)\n'
    result = explain_pipeline(str(write(tmp_path, "explained.py", source)))
    assert "y2" in result["inputs_used"]["found_in_source"]


def test_collection_and_negative_literals_are_recovered(tmp_path):
    """Inputs are not always scalars; bounds and flags are written as literals."""
    from smartmdao.mcp.loader import declared_input_map

    source = (
        SELLAR
        + "\ninputs = {'bounds': [1.0, 2.0], 'pair': (3, 4), 'tags': {'a', 'b'},"
          " 'offset': -1.5, 'on': True}\npipeline.run(**inputs)\n"
    )
    recovered = declared_input_map(write(tmp_path, "literals.py", source))

    assert recovered["bounds"] == [1.0, 2.0]
    assert recovered["pair"] == (3, 4)
    assert recovered["tags"] == {"a", "b"}
    assert recovered["offset"] == -1.5
    assert recovered["on"] is True


def test_a_computed_value_is_marked_unresolved_not_guessed(tmp_path):
    from smartmdao.mcp.loader import _UNRESOLVED, declared_input_map

    source = SELLAR + "\nimport math\npipeline.run(z1=math.pi, y2=1.0)\n"
    recovered = declared_input_map(write(tmp_path, "computed.py", source))

    assert recovered["z1"] is _UNRESOLVED
    assert recovered["y2"] == 1.0


def test_a_collection_containing_something_computed_is_unresolved(tmp_path):
    from smartmdao.mcp.loader import _UNRESOLVED, declared_input_map

    source = SELLAR + "\nimport math\npipeline.run(bounds=[1.0, math.pi])\n"
    assert declared_input_map(write(tmp_path, "mixed.py", source))["bounds"] is _UNRESOLVED
