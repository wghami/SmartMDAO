"""
Headless XDSM rendering.

`Pipeline.visualize()` defaults to `view=True`, which calls `plt.show()` and
blocks forever in a process with no display - fatal for a server. Everything
here forces the Agg backend and never asks for a window.
"""
import logging
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)



def force_headless_backend() -> None:
    """
    Switches matplotlib to Agg.

    `force=True` matters: `smartmdao.visualization` imports `pyplot` at module
    import time, and `smartmdao/__init__` imports that module, so by the time
    anything here runs a backend has already been selected.
    """
    import matplotlib

    matplotlib.use("Agg", force=True)


def render_xdsm(
    pipeline,
    output_path,
    inputs: Sequence[str] = (),
) -> Path:
    """
    Writes an XDSM diagram of `pipeline` to `output_path` and returns the path.

    Format is inferred from the extension, defaulting to PDF. Nothing is
    displayed and no discipline is executed - the diagram is built entirely
    from the dependency graph.
    """
    force_headless_backend()

    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    pipeline.visualize(
        inputs=list(inputs),
        output_path=str(destination),
        view=False,
    )

    # `render()` appends .pdf when the path had no extension.
    if not destination.suffix:
        destination = destination.with_suffix(".pdf")

    logger.debug(f"Rendered XDSM to {destination}.")
    return destination
