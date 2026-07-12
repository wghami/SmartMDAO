import matplotlib.pyplot as plt
from matplotlib.colors import to_rgba
import pytest

from smartmdao.models import Step
from smartmdao.visualization import PipelineVisualizer, compute_diagonal_order


def test_diagonal_order_matches_dependencies_not_alphabetical():
    # 'zeta' produces what 'alpha' consumes - the old alphabetical sort would
    # have placed alpha (the consumer) before zeta (the producer).
    def zeta(): return 1
    def alpha(zeta): return zeta

    zeta_step = Step(fn=zeta, manual_outputs=["zeta"])
    alpha_step = Step(fn=alpha)

    order = compute_diagonal_order([alpha_step, zeta_step], input_keys=set())
    assert order.index(zeta_step) < order.index(alpha_step)


def test_diagonal_order_handles_cycles_deterministically():
    # A feedback loop (mutual dependency) must not raise, and must be
    # ordered deterministically (alphabetical tie-break within the SCC).
    def discipline_1(z, x2): return z + x2
    def discipline_2(z, x1): return z + x1

    step1 = Step(fn=discipline_1, manual_outputs=["x1"])
    step2 = Step(fn=discipline_2, manual_outputs=["x2"])

    order = compute_diagonal_order([step2, step1], input_keys={"z"})
    assert [s.name for s in order] == ["discipline_1", "discipline_2"]


@pytest.mark.parametrize("ext", ["pdf", "png", "svg"])
def test_render_supports_multiple_formats(tmp_path, ext):
    step = Step(fn=lambda x: x)
    viz = PipelineVisualizer([step], input_keys={"x"})
    out = tmp_path / f"diagram.{ext}"
    viz.build().render(output_path=str(out), view=False)
    assert out.exists()
    assert out.stat().st_size > 0


def test_missing_input_is_styled_distinctly(tmp_path):
    step = Step(fn=lambda missing_var: missing_var)
    viz = PipelineVisualizer([step], input_keys=set())
    viz.build().render(output_path=str(tmp_path / "diagram.pdf"), view=False)

    texts = [t.get_text() for t in viz.ax.texts]
    assert any("missing_var (?)" in t for t in texts)

    missing_rgba = to_rgba(PipelineVisualizer.STYLE_MISSING["facecolor"])
    missing_patches = [p for p in viz.ax.patches if tuple(p.get_facecolor()) == tuple(missing_rgba)]
    assert missing_patches


def test_orientation_and_graph_type_are_accepted_but_non_fatal(tmp_path):
    step = Step(fn=lambda x: x)
    for orientation in ("TB", "LR"):
        for graph_type in ("flow", "bipartite", "unrecognized"):
            viz = PipelineVisualizer([step], input_keys={"x"}, orientation=orientation)
            viz.build(graph_type=graph_type).render(
                output_path=str(tmp_path / f"{orientation}_{graph_type}.pdf"), view=False
            )


def test_render_closes_figure_to_avoid_leaks(tmp_path):
    step = Step(fn=lambda x: x)
    before = len(plt.get_fignums())
    for i in range(5):
        viz = PipelineVisualizer([step], input_keys={"x"})
        viz.build().render(output_path=str(tmp_path / f"loop_{i}.pdf"), view=False)
    assert len(plt.get_fignums()) == before


def test_render_before_build_raises():
    step = Step(fn=lambda x: x)
    viz = PipelineVisualizer([step], input_keys={"x"})
    with pytest.raises(RuntimeError, match="Call build\\(\\) before render\\(\\)"):
        viz.render()


def test_render_with_output_path_and_view_shows_and_saves(tmp_path):
    # Agg backend makes plt.show() a no-op, so this just exercises the
    # view=True branch after a file save without popping a window.
    step = Step(fn=lambda x: x)
    out = tmp_path / "diagram.pdf"
    viz = PipelineVisualizer([step], input_keys={"x"})
    viz.build().render(output_path=str(out), view=True)
    assert out.exists()


def test_empty_pipeline_renders_placeholder_text(tmp_path):
    viz = PipelineVisualizer([], input_keys=set())
    viz.build().render(output_path=str(tmp_path / "empty.pdf"), view=False)
    texts = [t.get_text() for t in viz.ax.texts]
    assert "No steps in pipeline." in texts


def test_self_loop_step_draws_offset_feedback_edges(tmp_path):
    # A step that consumes and produces the same variable name creates a
    # self-loop data cell, which must be drawn as two offset parallel edges
    # instead of a single indistinguishable line.
    def accumulate(total): return total

    step = Step(fn=accumulate, manual_outputs=["total"])
    viz = PipelineVisualizer([step], input_keys=set())
    viz.build().render(output_path=str(tmp_path / "self_loop.pdf"), view=False)
    texts = [t.get_text() for t in viz.ax.texts]
    assert "total" in texts


def test_long_step_name_shrinks_fontsize_to_fit_box():
    def extremely_long_discipline_name_used_to_trigger_the_box_width_cap(x):
        return x

    step = Step(fn=extremely_long_discipline_name_used_to_trigger_the_box_width_cap)
    viz = PipelineVisualizer([step], input_keys={"x"})
    width, height, fontsize = viz._step_layout(step.name)
    assert fontsize < PipelineVisualizer.STEP_FONTSIZE
    assert fontsize >= PipelineVisualizer.STEP_MIN_FONTSIZE
