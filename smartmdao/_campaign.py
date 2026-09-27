"""
The detached campaign coordinator: `python -m smartmdao._campaign <store> ...`.

Started by `Campaign.start()` in a session of its own, so it outlives the call
- and the MCP session - that started it. It reads the campaign from its store,
runs it with the budget given, and leaves everything it learns in the store.
Nothing imports this module, so `python -m` never finds it already loaded.
"""
import argparse
import json
import sys
from typing import Optional, Sequence


def main(argv: Optional[Sequence[str]] = None) -> int:
    from .sweep import Campaign, CampaignError

    parser = argparse.ArgumentParser(description="Run a stored campaign.")
    parser.add_argument("store")
    parser.add_argument("--budget", type=float, required=True)
    parser.add_argument("--settings", default="{}")
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args(argv)

    campaign = Campaign.open(args.store, **json.loads(args.settings))
    try:
        campaign.run(args.budget, retry_failed=args.retry_failed)
    except CampaignError as error:
        print(f"campaign refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
