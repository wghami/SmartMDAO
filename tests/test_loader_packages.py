"""
The loader and the user's own modules. Roadmap 6.3 (R5), and a staleness bug
found while doing it.

- A pipeline file inside a package is imported as `pkg.module`, so its relative
  imports work. Before 1.24.0 every such file failed with "attempted relative
  import with no known parent package".
- The modules a load imports from the user's project are dropped afterwards, so
  an edit to a sibling discipline is seen by the next analysis. Before 1.24.0
  the long-lived MCP server kept the first version it imported.
"""
import sys
import textwrap

import pytest

from smartmdao.mcp.handlers import analyze_pipeline, run_pipeline
from smartmdao.mcp.loader import PipelineLoadError, load_pipeline

ENTRY = """
from smartmdao import Pipeline
from {source} import lift

pipeline = Pipeline(inputs=["speed"])
pipeline.add(lift, outputs=["lift"])
"""

PHYSICS = """
def lift(speed: float) -> float:
    return speed * 2
"""


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))
    return path


@pytest.fixture
def package(tmp_path):
    """project/aircraft/{__init__, physics, pipeline}.py"""
    root = tmp_path / "project" / "aircraft"
    write(root / "__init__.py", "")
    write(root / "physics.py", PHYSICS)
    return root


# ==============================================================================
# Relative imports
# ==============================================================================

def test_a_relative_import_in_a_package_works(package):
    entry = write(package / "pipeline.py", ENTRY.format(source=".physics"))

    report = analyze_pipeline(str(entry))

    assert report["ok"] is True, report.get("error")
    assert report["external_inputs"] == ["speed"]


def test_a_nested_package_resolves_two_levels_up(package):
    entry = write(package / "studies" / "pipeline.py", ENTRY.format(source="..physics"))
    write(package / "studies" / "__init__.py", "")

    assert load_pipeline(entry).pipeline.inputs == ("speed",)


def test_a_package_file_runs_too(package):
    """run_pipeline loads in a child process, through the same loader."""
    entry = write(package / "pipeline.py", ENTRY.format(source=".physics"))

    result = run_pipeline(str(entry), inputs={"speed": 3.0}, rung="full")

    assert result["ok"] is True, result.get("error")
    assert result["state"]["lift"] == 6.0


def test_a_package_init_can_hold_the_pipeline(package):
    write(package / "__init__.py", ENTRY.format(source=".physics"))

    assert load_pipeline(package / "__init__.py").variable == "pipeline"


def test_a_standalone_file_still_cannot_import_relatively(tmp_path):
    """Nothing is guessed: outside a package there is no parent to be relative to."""
    entry = write(tmp_path / "loose.py", ENTRY.format(source=".physics"))

    with pytest.raises(PipelineLoadError, match="no known parent package"):
        load_pipeline(entry)


# ==============================================================================
# The user's modules are read again on every load
# ==============================================================================

def test_an_edit_to_a_sibling_module_is_seen(tmp_path):
    write(tmp_path / "physics_edit.py", PHYSICS)
    entry = write(tmp_path / "entry.py", ENTRY.format(source="physics_edit"))
    assert analyze_pipeline(str(entry))["external_inputs"] == ["speed"]

    write(tmp_path / "physics_edit.py", """
        def lift(speed: float, span: float) -> float:
            return speed * span
    """)

    assert analyze_pipeline(str(entry))["external_inputs"] == ["span", "speed"]


def test_an_edit_inside_a_package_is_seen(package):
    entry = write(package / "pipeline.py", ENTRY.format(source=".physics"))
    analyze_pipeline(str(entry))

    write(package / "physics.py", """
        def lift(speed: float, chord: float) -> float:
            return speed * chord
    """)

    assert analyze_pipeline(str(entry))["external_inputs"] == ["chord", "speed"]


def test_nothing_of_the_users_is_left_behind(package):
    entry = write(package / "pipeline.py", ENTRY.format(source=".physics"))
    load_pipeline(entry)

    assert not [name for name in sys.modules if name.startswith("aircraft")]


def test_installed_libraries_and_fileless_modules_are_kept(tmp_path):
    """
    Re-importing an installed library is slow at best, and some extension
    modules cannot be imported twice - so only the user's own code is dropped.
    """
    write(tmp_path / "site-packages" / "vendored_lib.py", "VALUE = 1\n")
    entry = write(tmp_path / "entry.py", """
        import pathlib, sys, types
        sys.path.insert(0, str(pathlib.Path(__file__).parent / "site-packages"))
        import vendored_lib
        sys.modules["made_in_memory"] = types.ModuleType("made_in_memory")

        from smartmdao import Pipeline
        pipeline = Pipeline()
    """)
    try:
        load_pipeline(entry)

        assert "vendored_lib" in sys.modules
        assert "made_in_memory" in sys.modules
    finally:
        sys.modules.pop("vendored_lib", None)
        sys.modules.pop("made_in_memory", None)
        if str(tmp_path / "site-packages") in sys.path:
            sys.path.remove(str(tmp_path / "site-packages"))
