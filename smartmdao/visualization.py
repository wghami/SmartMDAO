import os
import logging
from collections import defaultdict, deque
from typing import List, Set, Dict, Literal, Optional, Tuple

import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.path import Path
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, PathPatch
from matplotlib.textpath import TextPath

from .models import Step
from .graph import map_producers, build_dependency_graph, tarjan_scc

# Initialize module-level logger
logger = logging.getLogger(__name__)


def compute_diagonal_order(steps: List[Step], input_keys: Set[str]) -> List[Step]:
    """
    Orders steps for the XDSM diagonal to match real execution order rather
    than alphabetical order. Mirrors HybridSolver.solve exactly: the
    condensation graph of strongly connected components is topologically
    sorted, and steps within a cyclic SCC (an MDA feedback loop) are
    tie-broken alphabetically - so diagrams reflect what actually runs,
    including which steps form a feedback block.
    """
    producers_map = map_producers(steps)
    adj_list, _ = build_dependency_graph(steps, input_keys, producers_map)

    sccs = tarjan_scc(steps, adj_list)
    scc_map = {step: i for i, cluster in enumerate(sccs) for step in cluster}
    scc_adj = defaultdict(set)
    scc_indegree = defaultdict(int)

    for u in steps:
        u_scc = scc_map[u]
        for v in adj_list[u]:
            v_scc = scc_map[v]
            if u_scc != v_scc and v_scc not in scc_adj[u_scc]:
                scc_adj[u_scc].add(v_scc)
                scc_indegree[v_scc] += 1

    for i in range(len(sccs)):
        scc_indegree.setdefault(i, 0)

    queue = deque([i for i, deg in scc_indegree.items() if deg == 0])
    order: List[Step] = []
    while queue:
        current = queue.popleft()
        group = sccs[current]
        if len(group) == 1 and group[0] not in adj_list[group[0]]:
            order.append(group[0])
        else:
            order.extend(sorted(group, key=lambda s: s.name))

        for neighbor in scc_adj[current]:
            scc_indegree[neighbor] -= 1
            if scc_indegree[neighbor] == 0:
                queue.append(neighbor)

    return order


class PipelineVisualizer:
    """
    Renders the Pipeline as an XDSM (eXtended Design Structure Matrix)
    diagram using matplotlib only - no system binaries required.

    Steps sit on the diagonal in execution order; off-diagonal cells show
    data transferred between steps; a left column shows external inputs
    (including unresolved/missing ones); a bottom row shows final outputs.
    """

    # --- Standard Palette (Material Design Pastels) ---
    STYLE_INPUT = {"facecolor": "#E3F2FD", "edgecolor": "#1565C0", "linewidth": 1.5}
    STYLE_STEP = {"facecolor": "#FFF3E0", "edgecolor": "#EF6C00", "linewidth": 1.5}
    STYLE_FINAL = {"facecolor": "#E8F5E9", "edgecolor": "#2E7D32", "linewidth": 2.5}
    STYLE_MISSING = {"facecolor": "#FFEBEE", "edgecolor": "#C62828", "linewidth": 2.0}

    STYLE_FORWARD_EDGE = {"color": "#616161", "linewidth": 1.2, "linestyle": "solid"}
    STYLE_FEEDBACK_EDGE = {"color": "#D32F2F", "linewidth": 1.6, "linestyle": "solid"}
    STYLE_MISSING_EDGE = {"color": "#C62828", "linewidth": 1.2, "linestyle": (0, (1, 2))}

    GUTTER = 0.3
    PAD = 0.3
    INPUT_COL = -1
    MISSING_COL = -2

    # Two edges of a self-loop connect the exact same pair of points, so they'd
    # otherwise draw as one indistinguishable line; this offsets them vertically
    # so both directions are visible as parallel straight arrows.
    SELF_LOOP_OFFSET = 0.07

    # Inches-per-data-unit used both to size the figure (_compute_figsize) and to
    # convert measured text extents (points) into data units (_text_width_units) -
    # keeping both in the same ratio is what guarantees text fits inside its box.
    INCHES_PER_UNIT = 1.15

    STEP_FONTSIZE = 11.0
    STEP_MIN_FONTSIZE = 7.5
    STEP_BOX_PAD = 0.32
    STEP_BOX_MAX_WIDTH = 1.9

    DATA_FONTSIZE = 9.0
    DATA_BOX_PAD = 0.32

    def __init__(
        self,
        steps: List[Step],
        input_keys: Set[str],
        orientation: Literal['TB', 'LR'] = 'TB'
    ):
        self.input_keys = input_keys
        self.orientation = orientation
        if orientation != 'TB':
            logger.debug(
                f"orientation={orientation!r} is accepted for backward compatibility "
                "but ignored - XDSM diagrams always read top-left to bottom-right."
            )

        self.steps = compute_diagonal_order(steps, input_keys)
        self.fig = None
        self.ax = None

    def build(self, graph_type: Literal["flow", "bipartite"] = "flow") -> "PipelineVisualizer":
        """
        Builds the XDSM diagram. `graph_type` is accepted for backward
        compatibility but no longer changes the layout - the XDSM view
        already combines what "flow" and "bipartite" used to show separately.
        """
        if graph_type not in ("flow", "bipartite"):
            logger.debug(f"Unrecognized graph_type={graph_type!r}; rendering the standard XDSM view.")
        self._build_xdsm()
        return self

    def render(self, output_path: Optional[str] = None, view: bool = True):
        """
        Renders the diagram to a file (format inferred from extension,
        defaulting to PDF) and/or displays it.
        """
        if self.fig is None:
            raise RuntimeError("Call build() before render().")
        try:
            if output_path:
                filename, ext = os.path.splitext(output_path)
                fmt = ext.lstrip('.').lower() if ext else 'pdf'
                final_path = output_path if ext else f"{filename}.pdf"
                self.fig.savefig(final_path, format=fmt, bbox_inches='tight', dpi=150)
                logger.info(f"Pipeline diagram saved to: {final_path}")
                if view:
                    plt.show()
            else:
                plt.show()
                logger.info("Pipeline diagram displayed.")
        except Exception as e:
            logger.error(f"Diagram rendering encountered an issue: {e}")
            if output_path:
                logger.info(f"File saved at: {output_path}")
        finally:
            plt.close(self.fig)

    # --- Classification Logic ---

    def _analyze_variables(self) -> Tuple[Set[str], Set[str], Set[str], Set[str], Dict[str, Step]]:
        """
        Categorizes all variables in the pipeline.
        Returns: (inputs, intermediates, finals, missing, producer_map)
        """
        producers_map = {}
        consumed = set()
        produced = set()

        for step in self.steps:
            for out in step.resolve_output_names():
                producers_map[out] = step
                produced.add(out)

            sig = step.get_signature()
            for param in sig.parameters:
                consumed.add(param)

        real_inputs = {v for v in consumed if v not in produced}
        missing = {v for v in real_inputs if v not in self.input_keys}
        valid_inputs = real_inputs.intersection(self.input_keys)
        intermediates = produced.intersection(consumed)
        finals = produced - consumed

        return valid_inputs, intermediates, finals, missing, producers_map

    # --- XDSM construction ---

    def _build_xdsm(self):
        n = len(self.steps)
        step_indices = {step: i for i, step in enumerate(self.steps)}
        _, _, finals, missing, producers = self._analyze_variables()

        self.fig, self.ax = plt.subplots()
        # plt.subplots() reserves default margins around the axes (for tick
        # labels etc.) even with axis('off') - without removing them, the axes
        # only fills a fraction of the figure, so the actual inches-per-data-unit
        # ratio ends up smaller than INCHES_PER_UNIT and text (sized against the
        # intended ratio) overflows its box. Filling the figure exactly makes
        # that assumption hold precisely.
        self.fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        self.ax.set_aspect('equal')
        self.ax.axis('off')

        if n == 0:
            self.ax.text(0.5, 0.5, "No steps in pipeline.", ha='center', va='center')
            return

        # ---- Pass 1: gather every cell's logical (col, row) slot, size, style, label ----
        # Cells are sized to fit their own content; column/row placement (pass 2) then
        # spaces columns/rows by the widest/tallest cell they contain, so cells never
        # overlap regardless of how long a step name or variable list is.
        cells: Dict[str, dict] = {}

        for i, step in enumerate(self.steps):
            w, h, fontsize = self._step_layout(step.name)
            cells[f"step_{i}"] = dict(
                col=i, row=i, w=w, h=h, shape="step", style=self.STYLE_STEP,
                label=step.name, fontsize=fontsize, bold=True,
            )

        for i, step in enumerate(self.steps):
            sig = step.get_signature()
            valid_params = sorted(p for p in sig.parameters if p not in missing and p not in producers)
            missing_params = sorted(p for p in sig.parameters if p in missing)

            if valid_params:
                label = "\n".join(valid_params)
                w, h = self._label_size(label)
                cells[f"input_{i}"] = dict(
                    col=self.INPUT_COL, row=i, w=w, h=h, shape="rect", style=self.STYLE_INPUT,
                    label=label, fontsize=9, bold=False,
                )

            if missing_params:
                label = "\n".join(f"{p} (?)" for p in missing_params)
                w, h = self._label_size(label)
                cells[f"missing_{i}"] = dict(
                    col=self.MISSING_COL, row=i, w=w, h=h, shape="hexagon", style=self.STYLE_MISSING,
                    label=label, fontsize=9, bold=False,
                )

        for j, step in enumerate(self.steps):
            final_outputs = sorted(o for o in step.resolve_output_names() if o in finals)
            if not final_outputs:
                continue
            label = "\n".join(final_outputs)
            w, h = self._label_size(label)
            cells[f"output_{j}"] = dict(
                col=j, row=n, w=w, h=h, shape="rect", style=self.STYLE_FINAL,
                label=label, fontsize=9, bold=False,
            )

        # Off-diagonal data cells: one per (consumer, producer) pair
        data_vars: Dict[Tuple[int, int], List[str]] = defaultdict(list)
        for i, step in enumerate(self.steps):
            sig = step.get_signature()
            for param in sig.parameters:
                if param in producers:
                    j = step_indices[producers[param]]
                    data_vars[(i, j)].append(param)

        for (i, j), params in data_vars.items():
            label = "\n".join(sorted(params))
            w, h = self._label_size(label)
            # A self-loop (a step feeding its own output back into itself, e.g. an
            # iterative fixed-point seed) would otherwise land on col=j, row=i = the
            # diagonal box's own slot. Nudge it to a dedicated half-column instead.
            col = j + 0.5 if i == j else j
            cells[f"data_{i}_{j}"] = dict(
                col=col, row=i, w=w, h=h, shape="rect", style=self._data_style(),
                label=label, fontsize=9, bold=False,
            )

        # ---- Pass 2: size each column/row to its widest/tallest cell, then place
        # column and row centers by cumulative offset (+ gutter) so nothing overlaps ----
        col_width: Dict[int, float] = defaultdict(float)
        row_height: Dict[int, float] = defaultdict(float)
        for spec in cells.values():
            col_width[spec["col"]] = max(col_width[spec["col"]], spec["w"])
            row_height[spec["row"]] = max(row_height[spec["row"]], spec["h"])

        col_center: Dict[int, float] = {}
        cursor = 0.0
        for col in sorted(col_width):
            col_center[col] = cursor + col_width[col] / 2
            cursor += col_width[col] + self.GUTTER
        total_width = cursor - self.GUTTER

        row_center: Dict[int, float] = {}
        cursor = 0.0
        for row in sorted(row_height):
            row_center[row] = -(cursor + row_height[row] / 2)
            cursor += row_height[row] + self.GUTTER
        total_height = cursor - self.GUTTER

        # Figure size and axis limits must scale by the exact same factor
        # (INCHES_PER_UNIT) - any mismatch (e.g. a fractional ax.margins() call)
        # changes the effective inches-per-data-unit ratio that box sizing in
        # _step_layout/_label_size assumed, which is what caused text to overflow
        # its box previously.
        padded_width = total_width + 2 * self.PAD
        padded_height = total_height + 2 * self.PAD
        self.fig.set_size_inches(*self._compute_figsize(padded_width, padded_height))

        # ---- Pass 3: build patches at their placed positions (each keeps its own size) ----
        patches: Dict[str, object] = {}
        positions: Dict[str, Tuple[float, float]] = {}
        for key, spec in cells.items():
            cx, cy = col_center[spec["col"]], row_center[spec["row"]]
            w, h = spec["w"], spec["h"]
            if spec["shape"] == "step":
                patch = FancyBboxPatch(
                    (cx - w / 2, cy - h / 2), w, h,
                    boxstyle="round,pad=0.02,rounding_size=0.08", **spec["style"],
                )
            elif spec["shape"] == "hexagon":
                patch = self._make_hexagon(cx, cy, w, h, spec["style"])
            else:
                patch = self._make_rect(cx, cy, w, h, spec["style"])
            patches[key] = patch
            positions[key] = (cx, cy)

        # ---- Draw edges first (so boxes sit visually on top of their endpoints) ----
        for i, step in enumerate(self.steps):
            if f"input_{i}" in patches:
                self._add_edge(f"input_{i}", f"step_{i}", positions, patches, self.STYLE_FORWARD_EDGE)
            if f"missing_{i}" in patches:
                self._add_edge(f"missing_{i}", f"step_{i}", positions, patches, self.STYLE_MISSING_EDGE)

        for j, step in enumerate(self.steps):
            if f"output_{j}" in patches:
                self._add_edge(f"step_{j}", f"output_{j}", positions, patches, self.STYLE_FORWARD_EDGE)

        for (i, j) in data_vars:
            is_feedback = j >= i
            style = self.STYLE_FEEDBACK_EDGE if is_feedback else self.STYLE_FORWARD_EDGE
            data_key = f"data_{i}_{j}"
            if i == j:
                # A self-loop's two segments (step->cell, cell->step) share both
                # endpoints, so drawing them on the same line would make them
                # indistinguishable - offset them vertically instead.
                offset_out, offset_in = (0, self.SELF_LOOP_OFFSET), (0, -self.SELF_LOOP_OFFSET)
            else:
                offset_out = offset_in = (0.0, 0.0)
            self._add_edge(f"step_{j}", data_key, positions, patches, style, offset=offset_out)
            self._add_edge(data_key, f"step_{i}", positions, patches, style, offset=offset_in)

        # ---- Draw boxes on top, then labels ----
        for patch in patches.values():
            self.ax.add_patch(patch)

        for key, spec in cells.items():
            cx, cy = positions[key]
            self.ax.text(
                cx, cy, spec["label"], ha='center', va='center',
                fontsize=spec["fontsize"], fontweight='bold' if spec["bold"] else 'normal', zorder=5,
            )

        self.ax.set_xlim(-self.PAD, total_width + self.PAD)
        self.ax.set_ylim(-total_height - self.PAD, self.PAD)

    def _data_style(self) -> Dict[str, str]:
        return {"facecolor": "#F5F5F5", "edgecolor": "#757575", "linewidth": 1.0}

    # --- Geometry helpers ---

    def _compute_figsize(self, total_width: float, total_height: float) -> Tuple[float, float]:
        # Only a floor, no ceiling: capping the max would shrink the effective
        # inches-per-data-unit ratio below INCHES_PER_UNIT for large pipelines,
        # reintroducing the text/box scale mismatch this method exists to avoid.
        # A floor is safe in the other direction (text only ends up relatively
        # smaller, never overflowing).
        w = max(total_width * self.INCHES_PER_UNIT, 4.0)
        h = max(total_height * self.INCHES_PER_UNIT, 4.0)
        return w, h

    def _text_width_units(self, text: str, fontsize: float, bold: bool) -> float:
        # Measures the actual rendered glyph width (via TextPath, which needs no
        # live renderer) instead of approximating from character count - the
        # character-count approximation was the root cause of text overflowing
        # its box for certain name lengths, since it didn't match real font metrics.
        prop = FontProperties(weight='bold' if bold else 'normal')
        width_pts = max(
            (TextPath((0, 0), line, size=fontsize, prop=prop).get_extents().width
             for line in text.split("\n")),
            default=0.0,
        )
        return width_pts / 72.0 / self.INCHES_PER_UNIT

    def _label_size(self, label: str) -> Tuple[float, float]:
        lines = label.split("\n") if label else [""]
        width = self._text_width_units(label, self.DATA_FONTSIZE, bold=False) + self.DATA_BOX_PAD
        line_height = self.DATA_FONTSIZE * 1.35 / 72.0 / self.INCHES_PER_UNIT
        height = max(len(lines) * line_height + 0.15, 0.4)
        return width, height

    def _step_layout(self, name: str) -> Tuple[float, float, float]:
        """Returns (box_width, box_height, fontsize) sized to fit `name` exactly."""
        fontsize = self.STEP_FONTSIZE
        text_w = self._text_width_units(name, fontsize, bold=True)
        width = text_w + self.STEP_BOX_PAD
        if width > self.STEP_BOX_MAX_WIDTH:
            # Use the linear width/fontsize relationship to jump close to a
            # fontsize that fits the cap, then re-measure at that exact fontsize
            # rather than trusting the linear estimate - font metrics aren't
            # perfectly linear with point size (hinting/rounding), so the box is
            # always sized to the real measured text, never an assumption.
            target_text_w = self.STEP_BOX_MAX_WIDTH - self.STEP_BOX_PAD
            fontsize = max(self.STEP_MIN_FONTSIZE, fontsize * target_text_w / text_w)
            text_w = self._text_width_units(name, fontsize, bold=True)
            width = text_w + self.STEP_BOX_PAD
        return width, 0.5, fontsize

    def _make_rect(self, cx: float, cy: float, w: float, h: float, style: Dict[str, str]) -> PathPatch:
        verts = [
            (cx - w / 2, cy + h / 2),
            (cx + w / 2, cy + h / 2),
            (cx + w / 2, cy - h / 2),
            (cx - w / 2, cy - h / 2),
            (cx - w / 2, cy + h / 2),
        ]
        codes = [Path.MOVETO, Path.LINETO, Path.LINETO, Path.LINETO, Path.CLOSEPOLY]
        return PathPatch(Path(verts, codes), **style)

    def _make_hexagon(self, cx: float, cy: float, w: float, h: float, style: Dict[str, str]) -> PathPatch:
        hw, hh = w / 2, h / 2
        cut = hw * 0.35
        verts = [
            (cx - hw + cut, cy + hh),
            (cx + hw - cut, cy + hh),
            (cx + hw, cy),
            (cx + hw - cut, cy - hh),
            (cx - hw + cut, cy - hh),
            (cx - hw, cy),
            (cx - hw + cut, cy + hh),
        ]
        codes = [Path.MOVETO] + [Path.LINETO] * 5 + [Path.CLOSEPOLY]
        return PathPatch(Path(verts, codes), **style)

    def _add_edge(
        self,
        key_a: str,
        key_b: str,
        positions: Dict[str, Tuple[float, float]],
        patches: Dict[str, object],
        style: Dict[str, str],
        offset: Tuple[float, float] = (0.0, 0.0),
    ):
        pos_a = positions[key_a]
        pos_b = positions[key_b]
        if offset != (0.0, 0.0):
            pos_a = (pos_a[0] + offset[0], pos_a[1] + offset[1])
            pos_b = (pos_b[0] + offset[0], pos_b[1] + offset[1])
        arrow = FancyArrowPatch(
            posA=pos_a, posB=pos_b,
            patchA=patches[key_a], patchB=patches[key_b],
            shrinkA=4, shrinkB=4,
            arrowstyle='-|>', mutation_scale=12,
            zorder=1,
            **style,
        )
        self.ax.add_patch(arrow)


# API adapter
def visualize_pipeline(
    steps: List[Step],
    inputs: Set[str],
    output_path: Optional[str] = None,
    orientation: str = "TD",
    graph_type: Literal["flow", "bipartite"] = "flow",
    view: bool = True
):
    viz = PipelineVisualizer(steps, inputs, orientation)
    viz.build(graph_type).render(output_path, view=view)
