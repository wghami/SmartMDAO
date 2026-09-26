"""
Groups: `group=` on steps. Roadmap 6.4 (R6).

A group changes nothing about what is computed. The shared planner uses it
only to choose between blocks that are independent anyway, so a group's steps
run - and are drawn on the XDSM diagonal - next to each other. Order inside a
feedback loop is never touched: it is alphabetical, and it decides which
variable needs a seed.
"""
import matplotlib

matplotlib.use("Agg")

import pytest

from smartmdao import HybridSolver, Pipeline, analyze, validate
from smartmdao.graph import _condensation, _first_come_order, build_execution_plan, group_conflicts
from smartmdao.mcp.handlers import render_pipeline_diagram
from smartmdao.models import Step
from smartmdao.visualization import PipelineVisualizer, compute_diagonal_order


def two_parts(grouped=True, log=None):
    """Two independent chains that alternate by registration, and a step joining them."""
    log = [] if log is None else log
    g = (lambda name: name) if grouped else (lambda name: None)
    pipeline = Pipeline(solver=HybridSolver(), inputs=["alt", "region"])

    @pipeline.step(outputs=["orbit"], group=g("space"))
    def s1_orbit(alt: float) -> float:
        log.append("s1_orbit"); return alt

    @pipeline.step(outputs=["terrain"], group=g("ground"))
    def g1_terrain(region: float) -> float:
        log.append("g1_terrain"); return region

    @pipeline.step(outputs=["coverage"], group=g("space"))
    def s2_coverage(orbit: float) -> float:
        log.append("s2_coverage"); return orbit * 2

    @pipeline.step(outputs=["sites"], group=g("ground"))
    def g2_sites(terrain: float) -> float:
        log.append("g2_sites"); return terrain + 1

    @pipeline.step(outputs=["score"])
    def combine(coverage: float, sites: float) -> float:
        log.append("combine"); return coverage + sites

    return pipeline


def order(pipeline, inputs=None):
    keys = set(pipeline.inputs if inputs is None else inputs)
    return [step.name for block in build_execution_plan(pipeline.steps, keys) for step in block.steps]


# ==============================================================================
# The declaration
# ==============================================================================

@pytest.mark.parametrize("bad", ["", 3])
def test_a_group_must_be_a_non_empty_string(bad):
    with pytest.raises(TypeError, match="non-empty string"):
        Step(lambda x: x, ["y"], group=bad)


def test_a_step_supplied_by_an_object_takes_the_group_too():
    """RuleDiscipline and friends register through as_step()."""
    class Supplier:
        def as_step(self):
            def made(x: float) -> float:
                return x
            return Step(made, ["y"])

    pipeline = Pipeline()
    pipeline.add(Supplier(), group="rules")
    assert pipeline.steps[0].group == "rules"

    with pytest.raises(TypeError):
        pipeline.add(Supplier(), group="")


# ==============================================================================
# The plan
# ==============================================================================

def test_without_groups_the_plan_is_exactly_first_come():
    """Not merely equivalent: identical, so no existing pipeline moves."""
    pipeline = two_parts(grouped=False)
    _, sccs, _, scc_adj = _condensation(pipeline.steps, set(pipeline.inputs))
    expected = [sccs[i][0].name for i in _first_come_order(len(sccs), scc_adj)]

    assert order(pipeline) == expected == [
        "s1_orbit", "g1_terrain", "s2_coverage", "g2_sites", "combine",
    ]


def test_with_groups_each_part_is_contiguous():
    assert order(two_parts()) == ["s1_orbit", "s2_coverage", "g1_terrain", "g2_sites", "combine"]


def test_the_solver_runs_the_grouped_order_and_the_answer_is_unchanged():
    """One planner: what analyze and the diagram show is what runs."""
    plain_log, grouped_log = [], []
    plain = two_parts(grouped=False, log=plain_log).run(alt=1.0, region=2.0)
    grouped = two_parts(grouped=True, log=grouped_log).run(alt=1.0, region=2.0)

    assert plain == grouped
    assert grouped_log == order(two_parts())
    assert list(analyze(two_parts()).execution_order) == grouped_log


def test_a_loop_is_never_reordered_and_keeps_its_group():
    """Alphabetical inside the loop, whatever the groups; ungrouped members don't count."""
    pipeline = Pipeline(solver=HybridSolver(), inputs=["x", "b_out"])

    @pipeline.step(outputs=["z_out"], group="aero")
    def zeta(x: float, b_out: float) -> float:
        return x + b_out

    @pipeline.step(outputs=["b_out"])
    def beta(z_out: float) -> float:
        return z_out / 2

    [block] = build_execution_plan(pipeline.steps, {"x", "b_out"})
    assert [step.name for step in block.steps] == ["beta", "zeta"]
    assert group_conflicts(pipeline.steps, {"x", "b_out"}) == []


# ==============================================================================
# When groups cannot all stay together
# ==============================================================================

def crossing():
    """space -> ground -> space: 'b' must sit between space's two steps."""
    pipeline = Pipeline(inputs=["x"])

    @pipeline.step(outputs=["a1"], group="space")
    def a(x: float) -> float:
        return x

    @pipeline.step(outputs=["b1"], group="ground")
    def b(a1: float) -> float:
        return a1

    @pipeline.step(outputs=["a2"], group="space")
    def c(b1: float) -> float:
        return b1

    return pipeline


def test_a_crossing_is_reported_with_the_edges_that_force_it():
    [finding] = [f for f in validate(crossing()) if f.code == "groups-interleaved"]

    assert finding.severity == "info"
    assert "Groups 'ground' and 'space' cannot all be kept together" in finding.message
    assert "a -> b, b -> c" in finding.message
    assert order(crossing()) == ["a", "b", "c"]


def test_an_ungrouped_step_in_between_splits_a_single_group():
    pipeline = crossing()
    pipeline.steps[1].group = None

    [finding] = [f for f in validate(pipeline) if f.code == "groups-interleaved"]
    assert "Group 'space' cannot be kept together" in finding.message


def test_a_long_list_of_edges_is_shortened():
    """Six steps alternating space/ground in one chain: five crossing edges, four shown."""
    chain = Pipeline(inputs=["v0"])
    for index in range(6):
        namespace = {}
        exec(f"def n{index}(v{index}: float) -> float:\n    return v{index}\n", namespace)
        chain.add(namespace[f"n{index}"], outputs=[f"v{index + 1}"],
                  group="space" if index % 2 == 0 else "ground")

    [finding] = [f for f in validate(chain) if f.code == "groups-interleaved"]
    assert "and 1 more" in finding.message


def test_groups_sharing_a_loop_are_reported_and_left_alone():
    pipeline = Pipeline(solver=HybridSolver(), inputs=["x", "b_out"])

    @pipeline.step(outputs=["a_out"], group="space")
    def alpha(x: float, b_out: float) -> float:
        return x + b_out

    @pipeline.step(outputs=["b_out"], group="ground")
    def beta(a_out: float) -> float:
        return a_out / 2

    [conflict] = group_conflicts(pipeline.steps, {"x", "b_out"})
    assert conflict.in_loop and conflict.steps == ("alpha", "beta")

    [finding] = [f for f in validate(pipeline) if f.code == "groups-interleaved"]
    assert "share a feedback loop (alpha, beta)" in finding.message
    assert "never reordered" in finding.message


# ==============================================================================
# The diagram
# ==============================================================================

def test_the_diagonal_is_the_plan():
    """compute_diagonal_order was a copy of the planner; now it is the planner."""
    pipeline = two_parts()
    assert [s.name for s in compute_diagonal_order(pipeline.steps, set(pipeline.inputs))] == order(pipeline)


def test_a_contiguous_group_is_one_band():
    pipeline = two_parts()
    visual = PipelineVisualizer(pipeline.steps, set(pipeline.inputs))
    assert visual.group_runs() == [("space", 0, 1), ("ground", 2, 3)]
    assert legend_labels(visual) == ["space", "ground"]


def test_each_run_of_a_group_is_a_band():
    visual = PipelineVisualizer(crossing().steps, {"x"})
    assert visual.group_runs() == [("space", 0, 0), ("ground", 1, 1), ("space", 2, 2)]


def legend_labels(visual):
    legend = visual.build().ax.get_legend()
    return None if legend is None else [text.get_text() for text in legend.get_texts()]


def test_the_legend_marks_a_split_group():
    assert legend_labels(PipelineVisualizer(crossing().steps, {"x"})) == ["space (split in 2)", "ground"]


def test_no_groups_means_no_legend_and_no_bands():
    visual = PipelineVisualizer(two_parts(grouped=False).steps, {"alt", "region"})
    assert legend_labels(visual) is None
    assert visual.group_runs() == []


def test_colours_cycle_past_the_palette():
    pipeline = Pipeline(inputs=["x"])
    for index in range(len(PipelineVisualizer.GROUP_PALETTE) + 1):
        namespace = {}
        exec(f"def s{index}(x: float) -> float:\n    return x\n", namespace)
        pipeline.add(namespace[f"s{index}"], outputs=[f"y{index}"], group=f"g{index}")

    labels = legend_labels(PipelineVisualizer(pipeline.steps, {"x"}))
    assert len(labels) == len(PipelineVisualizer.GROUP_PALETTE) + 1


def test_render_pipeline_diagram_says_why_a_group_is_split(tmp_path):
    source = tmp_path / "crossing.py"
    source.write_text(
        "from smartmdao import Pipeline\n"
        "pipeline = Pipeline(inputs=['x'])\n"
        "@pipeline.step(outputs=['a1'], group='space')\n"
        "def a(x: float) -> float: return x\n"
        "@pipeline.step(outputs=['b1'], group='ground')\n"
        "def b(a1: float) -> float: return a1\n"
        "@pipeline.step(outputs=['a2'], group='space')\n"
        "def c(b1: float) -> float: return b1\n"
    )
    report = render_pipeline_diagram(str(source), str(tmp_path / "xdsm.png"))

    assert report["ok"] is True
    assert "a -> b, b -> c" in report["group_notes"][0]


def test_render_pipeline_diagram_is_quiet_without_conflicts(tmp_path):
    source = tmp_path / "plain.py"
    source.write_text(
        "from smartmdao import Pipeline\n"
        "pipeline = Pipeline(inputs=['x'])\n"
        "@pipeline.step(outputs=['y'], group='only')\n"
        "def f(x: float) -> float: return x\n"
    )
    assert "group_notes" not in render_pipeline_diagram(str(source), str(tmp_path / "xdsm.png"))
