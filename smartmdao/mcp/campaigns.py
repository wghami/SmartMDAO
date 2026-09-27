"""
Campaigns over MCP: quote, start detached, ask how it is going, stop.

Thin on purpose - `smartmdao.sweep.Campaign` does the work, and these only
turn its answers and refusals into data. A campaign can take hours; a tool
call cannot, so `start=True` returns at once and the campaign runs on its own
(docs/design/006). It survives the session that started it.
"""
from typing import Any, Dict, List, Optional, Union


def _campaign(path: Optional[str], store: str, seeds: Union[int, List[Any], None], **settings):
    from ..sweep import Campaign

    if isinstance(seeds, int) and not isinstance(seeds, bool):
        seeds = list(range(seeds))                    # "100 realizations" reads better as a number
    return Campaign(path, store, seeds=seeds, **settings)


def sweep_pipeline(
    path: str,
    store: str,
    grid: Optional[Dict[str, List[Any]]] = None,
    points: Optional[List[Dict[str, Any]]] = None,
    seeds: Union[int, List[Any], None] = None,
    seed_input: Optional[str] = None,
    outputs: Optional[List[str]] = None,
    inputs_from: Optional[str] = None,
    inputs_args: Optional[List[Any]] = None,
    inputs_kwargs: Optional[Dict[str, Any]] = None,
    inputs: Optional[Dict[str, Any]] = None,
    variable: Optional[str] = None,
    workers: Optional[int] = None,
    point_timeout: Optional[float] = None,
    allow_effects: bool = False,
    python: Optional[str] = None,
    project: Optional[str] = None,
    start: bool = False,
    budget_seconds: Optional[float] = None,
    retry_failed: bool = False,
) -> Dict[str, Any]:
    """The quote by default; with start=True and a budget, the campaign itself, detached."""
    from ..sweep import CampaignError

    try:
        campaign = _campaign(
            path, store, seeds, grid=grid, points=points, seed_input=seed_input, outputs=outputs,
            inputs_from=inputs_from, inputs_args=inputs_args, inputs_kwargs=inputs_kwargs,
            inputs=inputs, variable=variable, workers=workers, point_timeout=point_timeout,
            allow_effects=allow_effects, python=python, project=project,
        )
        if not start:
            return {"ok": True, "quote": campaign.quote()}
        if not budget_seconds:
            return {
                "ok": False,
                "refused": "budget",
                "error": "start=True needs budget_seconds - quote first, then agree a number. There is no default.",
            }
        return {"ok": True, **campaign.start(budget_seconds, retry_failed=retry_failed)}
    except CampaignError as error:
        return {"ok": False, "error": str(error)}


def _opened(store: str):
    from pathlib import Path

    from ..sweep import Campaign

    if not (Path(store) / "campaign.json").is_file():
        return None, {"ok": False, "error": f"No campaign in {store}."}
    return Campaign.open(store), None


def sweep_status(store: str, aggregate: bool = True) -> Dict[str, Any]:
    """Progress, failures so far, and - with aggregate - the numbers so far."""
    campaign, failure = _opened(store)
    if failure:
        return failure
    result = {"ok": True, **campaign.status()}
    if aggregate:
        result["aggregate"] = campaign.aggregate()
    return result


def sweep_stop(store: str) -> Dict[str, Any]:
    """Asks the campaign to stop handing out points. Resumable with a new start."""
    campaign, failure = _opened(store)
    if failure:
        return failure
    return {"ok": True, **campaign.stop()}
