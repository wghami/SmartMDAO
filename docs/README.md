# SmartMDAO documentation

Design records and internals reference. User-facing documentation — installation, quick start,
examples — lives in the [top-level README](../README.md) and the runnable scripts in
[`scripts/`](../scripts).

## Reading order

**To understand how the library works:**
[architecture.md](architecture.md) — the execution path from `Pipeline.run()` down through
validation, the graph layer, the solvers, and `StepExecutor`. Written to be read top to bottom.

**To understand where it is going:**
[roadmap.md](roadmap.md) — phased plan with exit criteria. The living document; checkboxes move as
work lands.

**Before filing a bug:**
[known-issues.md](known-issues.md) — sharp edges that are already known, each with a severity and
a fix direction. Several are deliberate.

## Design records

Numbered, dated, and immutable in intent: a record says why a decision was made at a point in
time. Superseding one means writing a new record, not editing the old.

| # | Record | Status |
|---|---|---|
| 001 | [MCP connector](design/001-mcp-connector.md) — why the server verifies rather than authors | accepted, not implemented |
| 002 | [An agent as an MDA discipline](design/002-agent-as-discipline.md) — a model inside an MDA feedback loop | mechanics proven, no live model yet |

## Conventions

- Links between docs are relative, so they resolve on GitHub and on disk.
- References into source use `file:line` form and should be checked when the code moves.
- Claims about behaviour are verified against the running code before being written down, not
  inferred from reading. Where something is a hypothesis, it says so.
