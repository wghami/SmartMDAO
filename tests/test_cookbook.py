"""The cookbook is executable documentation, and this is what makes it true.

Three guarantees:

  1. Every Python block in docs/cookbook.md runs, and its assertions hold.
  2. Every script it points at exists.
  3. Every name exported from `smartmdao` is mentioned somewhere in it.

Guarantee 1 is the important one. Two demo scripts in this repository shipped
claims that were not true until someone ran them; a cookbook handed to a coding
agent gets copied verbatim, so a snippet nobody executed is worse than no
snippet at all.
"""
import pathlib
import re

import matplotlib
import pytest

import smartmdao

matplotlib.use("Agg", force=True)

REPO = pathlib.Path(__file__).resolve().parents[1]
COOKBOOK = REPO / "docs" / "cookbook.md"


def python_blocks():
    """(section heading, code) for every ```python fence in the cookbook."""
    text = COOKBOOK.read_text()
    section = "(preamble)"
    blocks = []

    for chunk in re.split(r"^(## .+)$", text, flags=re.M):
        if chunk.startswith("## "):
            section = chunk[3:].strip()
            continue
        for code in re.findall(r"```python\n(.*?)```", chunk, flags=re.S):
            blocks.append((section, code))

    return blocks


BLOCKS = python_blocks()


def test_the_cookbook_actually_contains_snippets():
    """Guards against the extractor silently matching nothing."""
    assert len(BLOCKS) >= 10


@pytest.mark.parametrize(
    "section,code",
    BLOCKS,
    ids=[f"{index}-{section}" for index, (section, _) in enumerate(BLOCKS)],
)
def test_every_snippet_runs(section, code, tmp_path, monkeypatch):
    """Execute each block in a fresh namespace, from a scratch directory.

    The visualization snippet writes to `results/`, so run everything somewhere
    disposable rather than littering the repository.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "results").mkdir(exist_ok=True)

    namespace = {"__name__": "__cookbook__"}
    try:
        exec(compile(code, f"cookbook::{section}", "exec"), namespace)
    except Exception as error:      # pragma: no cover - only on a broken snippet
        pytest.fail(
            f"Snippet in '{section}' failed: {type(error).__name__}: {error}\n\n{code}"
        )


def test_every_referenced_script_exists():
    referenced = set(re.findall(r"\.\./scripts/([a-z_0-9]+\.py)", COOKBOOK.read_text()))
    assert referenced, "the cookbook should point at real examples"

    missing = [name for name in referenced if not (REPO / "scripts" / name).is_file()]
    assert not missing, f"cookbook references scripts that do not exist: {missing}"


def test_every_public_name_is_covered():
    """A new export with no guidance is a gap the agent will fall into."""
    text = COOKBOOK.read_text()
    uncovered = sorted(name for name in smartmdao.__all__ if name not in text)
    assert not uncovered, (
        "these are exported from smartmdao but never mentioned in the cookbook: "
        f"{uncovered}"
    )


def test_every_topic_in_the_index_has_a_section():
    text = COOKBOOK.read_text()
    indexed = set(re.findall(r"\| \[`([a-z-]+)`\]\(#[a-z-]+\) \|", text))
    headings = set(re.findall(r"^## ([a-z-]+)$", text, flags=re.M))

    assert indexed, "the topic index should not be empty"
    assert indexed == headings, (
        f"index and sections disagree: only in index {sorted(indexed - headings)}, "
        f"only as sections {sorted(headings - indexed)}"
    )


# --- the cookbook served as a tool -------------------------------------------

def test_cookbook_tool_returns_essentials_and_the_topic_list():
    from smartmdao.mcp import cookbook as cookbook_tool

    result = cookbook_tool()
    assert result["ok"] is True
    assert "quickstart" in result["guidance"]
    assert "pitfalls" in result["guidance"]
    assert "solvers" in result["topics"]


def test_cookbook_tool_returns_a_named_topic_in_full():
    from smartmdao.mcp import cookbook as cookbook_tool

    result = cookbook_tool("optimization")
    assert result["topic"] == "optimization"
    assert "PipelineEvaluator" in result["guidance"]


def test_cookbook_tool_is_forgiving_about_case_and_space():
    from smartmdao.mcp import cookbook as cookbook_tool

    assert cookbook_tool("  SOLVERS ")["topic"] == "solvers"


def test_an_unknown_topic_lists_the_real_ones():
    from smartmdao.mcp import cookbook as cookbook_tool

    result = cookbook_tool("nonsense")
    assert result["ok"] is False
    assert "solvers" in result["topics"]


def test_the_api_surface_is_generated_from_the_package():
    """Written-down API lists drift; a generated one cannot."""
    from smartmdao.mcp.authoring import api_surface

    surface = {entry["name"]: entry for entry in api_surface()}
    assert set(surface) == set(smartmdao.__all__)

    # Constants report their value, not str's unhelpful docstring.
    assert surface["ABANDONED"]["kind"] == "constant"
    assert surface["ABANDONED"]["summary"] == "= 'abandoned'"

    assert surface["Pipeline"]["kind"] == "class"
    assert surface["analyze"]["summary"].startswith("Describes what running")


def test_the_server_instructions_tell_the_agent_to_read_it():
    """The instructions are always in context; this is what drives the call."""
    from smartmdao.mcp.server import INSTRUCTIONS

    assert "smartmdao_cookbook" in INSTRUCTIONS
    assert "BEFORE WRITING" in INSTRUCTIONS


def test_agents_md_exists_and_points_at_the_cookbook():
    agents = REPO / "AGENTS.md"
    assert agents.is_file()
    text = agents.read_text()
    assert "docs/cookbook.md" in text
    assert "docs/handoff.md" in text


def test_a_missing_cookbook_file_says_so_rather_than_serving_nothing(monkeypatch, tmp_path):
    """Installed without docs/, the tool must explain itself, not return {}."""
    from smartmdao.mcp import authoring as cookbook_module

    monkeypatch.setattr(cookbook_module, "COOKBOOK_PATH", tmp_path / "absent.md")

    result = cookbook_module.cookbook()
    assert result["ok"] is False
    assert "not available" in result["error"]
    assert "github.com" in result["error"]
    assert cookbook_module.topics() == []


def test_the_cookbook_resolves_wherever_it_is_installed():
    """It must travel in the wheel, not just live in the repository.

    `pip install smartmdao[mcp]` ships no `docs/`, so the file is
    force-included beside the module. Without that, every installed user gets
    the tool and none of the content.
    """
    from smartmdao.mcp import authoring

    assert authoring.COOKBOOK_PATH.is_file(), (
        f"cookbook not found at {authoring.COOKBOOK_PATH}; check the "
        "force-include in pyproject.toml"
    )


# --- documentation counts, which go stale silently ---------------------------

def test_the_documented_counts_match_reality():
    """Three PRs in a row left `roadmap.md`'s baseline saying v1.12.0.

    Prose goes stale without anything failing, so the numbers people read are
    asserted here: a stale count now breaks the build instead of misleading a
    reader.
    """
    import re
    from importlib.metadata import version

    installed = version("smartmdao")
    script_count = len(list((REPO / "scripts").glob("*.py")))

    for name in ("roadmap.md", "handoff.md"):
        text = (REPO / "docs" / name).read_text()
        claimed = re.findall(r"v(\d+\.\d+\.\d+)", text)
        stale = {v for v in claimed if v != installed}
        # Historical references ("moved in 1.7.0") are prose, not claims about
        # now; only the `v`-prefixed baseline/state lines are checked.
        assert not stale, f"{name} claims version(s) {sorted(stale)}, installed is {installed}"

        for count in re.findall(r"(\d+)/(\d+) scripts", text):
            assert int(count[0]) == script_count, (
                f"{name} says {count[0]}/{count[1]} scripts; there are {script_count}"
            )
