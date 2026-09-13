# 001 — MCP connector

**Status:** implemented (Phase 2); execution tools still pending (Phase 3)
**Date:** 2026-09-13 · implementation notes added after Phase 2
**Supersedes:** nothing

---

## Problem

A developer using a coding agent to build a SmartMDAO model gets no feedback until they run the
code. The agent writes disciplines **blind**. It cannot see that:

- the functions it just wrote closed a `y1 → y2 → y1` cycle, so the default `DAGSolver` will raise;
- a producer declares `float` where the consumer expects `dict`;
- the feedback loop hit `max_iterations` without converging;
- a constraint sign is flipped relative to the `h(x) >= 0` convention both backends use.

Every one of those facts is already computable by this library. `graph.py` finds cycles with
Tarjan; `validation.py` checks producer→consumer type edges; `solvers.py` records a residual
history. Crucially, the first two require **no execution at all** — SmartMDAO's introspection is
built entirely on signatures and annotations (see [architecture.md](../architecture.md)).

That knowledge is currently unreachable from outside a Python process.

## The framing we rejected

The obvious pitch is "an MCP server that turns prose into SmartMDAO code."

We reject it as the *primary* framing. The client is already a capable language model; given the
README it can write `@pipeline.step` code today. A server cannot generate better code than the
model already driving the session, and if the server makes its own model call to do the generation
it adds an API key, a second bill, extra latency, and — almost certainly — a weaker model than the
one the user is already talking to.

Generation is not the bottleneck. **Verification is.**

## Decision

The MCP server is a **verifier and an oracle, not an author**.

- **Tools** do things the client provably cannot: structural analysis, type checking, rendering,
  and (later) execution.
- **Resources** expose the docs, the benchmark scripts in `scripts/`, and a capability schema, so
  the client's own generation is grounded in current, accurate context rather than training data.
- **Prompts** supply authoring templates — including a prose→pipeline template. Prose translation
  still happens, but it happens *in the client*, with server-supplied grounding and a server-backed
  verification loop behind it.

The workflow this enables:

```
agent drafts code
  → analyze_pipeline    (order, cycles, feedback vars, recommended solver)
  → validate_pipeline   (producer/consumer type edges)
  → agent fixes
  → run_pipeline        (state + convergence trace)      [Phase 3]
  → render_xdsm         (diagram)
```

Each arrow returns something the agent could not have derived from the docs alone.

## Proposed tool surface

### Phase 2 — analysis only, nothing executes

| Tool | Returns |
|---|---|
| `analyze_pipeline` | Execution order, detected SCCs, which variables carry feedback, whether `DAGSolver` suffices or a cyclic solver is required, and why. |
| `validate_pipeline` | Producer→consumer type mismatches, unsatisfiable inputs, steps whose outputs nothing consumes. |
| `render_xdsm` | An XDSM diagram of the pipeline. |
| `explain_pipeline` | Prose description of an existing pipeline — the reverse direction, for onboarding and docs. |

A note on honesty: "analysis only" is a statement about *our* code paths. Deriving a pipeline's
structure requires importing the module that defines it, and importing Python runs module-level
code. The distinction that holds is narrower but still real — **no discipline function is
invoked** — and it must be documented as such rather than oversold as sandboxing.

### Phase 3 — execution, as a cost ladder

Revised after working through the concept of operations. The original plan was a flat
`run_pipeline` / `optimize` / `sweep`; what an engineer actually needs is to choose a rung *and be
told what it costs* before committing.

| Rung | Cost | Answers |
|---|---|---|
| `analyze` / `validate` | free | Is it wired correctly? *(shipped)* |
| **single sweep** | one call per discipline | Does the code run at all? What does one evaluation cost? |
| **budgeted run** | capped sweeps + wall clock | Does it converge, within a budget the engineer set? |
| full run | unbounded | The answer |
| `optimize` | full run × 10²–10³ | The design |

The single sweep earns its place twice: it is the cheapest possible smoke test, and it *measures
the unit cost*, so the estimate for every rung above it falls out of it. That turns "this might
take a while" into "one sweep is 4.2 s, your loop needs 15–40, so budget 1–3 minutes; the optimizer
will call it ~200 times, about 4 hours." The engineer then consents with a number instead of a
guess. It is already expressible today via `max_iterations=1` — it is simply not exposed.

**`compare_runs`** — run two pipelines on the same inputs and diff the resulting state — is a
better justification for execution tooling than a generic `run_pipeline`. It is what makes a
translation from hand-written code trustworthy, and it is the one thing neither the agent nor
static analysis can do alone. See [known-issues.md](../known-issues.md).

## Execution model

There is no serializable pipeline spec. A `Step` *is* a live Python function
([models.py:6](../../smartmdao/models.py:6)), so any tool that runs a pipeline is executing
arbitrary user code. Three options:

| Option | Assessment |
|---|---|
| In-process `exec` | Trivial. For a local stdio server the marginal risk is near zero — the coding agent already runs code on this machine. Becomes dangerous the moment anyone puts it behind HTTP. |
| **Subprocess + timeout** | **Chosen target.** Isolates the server process from user code, gives a hard kill for a non-converging `IterativeSolver`, and forces a clean JSON boundary that we need anyway. |
| Analysis-only | Where we start. Delivers most of the value above with none of the exposure. |

**Decision:** ship Phase 2 with no execution. Add execution in Phase 3 via subprocess with a
mandatory timeout. Never ship a remote transport without real sandboxing, and document the
execution model prominently wherever the server is installed.

### Correction (post-Phase 2): the safety argument above is wrong for the local case

The table frames subprocess isolation as *protection*. Working through the concept of operations
showed that is not what it buys, because **the agent driving this connector already has a shell**.
When it cannot run a pipeline through us, it runs `python model.py` — unsandboxed, untimed, results
scraped from stdout. Our refusal to execute does not create a boundary; it pushes the work
somewhere with *less* control and a worse result format.

The honest case for subprocess + timeout is therefore **reliability, structure and a kill switch**:

- a hard stop for a non-converging solve, which is otherwise unbounded;
- a clean JSON boundary, so results arrive typed rather than scraped;
- crash isolation, so a segfaulting discipline does not take the server with it.

Safety only becomes the real argument for a hosted server, or for a client whose agent has no shell
access. Both remain out of scope, and the "no remote transport without real sandboxing" constraint
stands unchanged. What changes is that we should stop claiming a protection we do not provide.

## Packaging

Ships as an **optional extra in this repository**: `smartmdao/mcp/`, installed with
`pip install smartmdao[mcp]`.

Rejected alternatives: a separate `smartmdao-mcp` package (cleanest isolation, but two repos to
keep version-compatible for a library this size), and putting it in core (forces the MCP SDK on
every user and roughly doubles the dependency surface).

The extra must use **lazy imports** so that nothing in the base install path touches the MCP SDK.
This mirrors `HDF5Backend`'s existing treatment of `h5py`
([cache.py:68](../../smartmdao/cache.py:68)).

## Constraints the implementation must respect

- **Headless rendering.** `visualize()` defaults to `view=True`, which calls `plt.show()`
  ([visualization.py:151](../../smartmdao/visualization.py:151)) and will hang a server with no
  display. The MCP layer must force the Agg backend and `view=False`, returning a path or encoded
  image. See [known-issues.md](../known-issues.md).
- **Context budget.** A converged `memory` dict can contain large arrays and a long
  `residual_history`. Responses must be summarised and truncated, with full data available on
  request rather than by default.
- **Statelessness.** Phase 2 and 3 tools take source in and return results out. No server-side
  pipeline handles across calls: the agent already holds the source, and handles introduce
  lifetime and leak problems for no real gain.
- **Install weight.** `openturns` and `ipykernel` are currently hard runtime dependencies, which
  makes a `uvx`-style install painful. Tracked in [known-issues.md](../known-issues.md).
- **Test bar.** This repo sits at 100% coverage. Tool handlers must be written as plain functions
  testable directly, with the protocol layer kept thin — testing through a live MCP session is
  slow and brittle.

## Consequences

**Good.** The valuable half of the idea ships first and without security exposure. The server has
a clear identity — it knows things the agent doesn't — instead of competing with the client model
at a task the client is better at. Resources keep the agent grounded in the *current* API rather
than whatever version it memorised.

**Bad.** "Prose → code" as a headline feature is gone; the prose path is real but it runs through
prompts and verification rather than a single generate tool. The `[mcp]` extra means MCP work is
coupled to this repo's release cadence. And the analysis/execution split means two rounds of tool
design rather than one.

---

# Phase 2 implementation notes

## The analysis is not MCP-specific

Building this made it obvious that "what will run, in what order, and what is wrong with it" is
useful to anyone, not only to a coding agent. So it ships as **`smartmdao.analysis`** — `analyze`,
`validate`, `explain`, exported from the package root — and `smartmdao/mcp/` is a thin adapter
over it. A user with no interest in MCP gets the same checks from Python.

This also shrinks the MCP surface to something testable: `handlers.py` holds the behaviour and
imports nothing from the SDK; `server.py` only registers.

## One planner, not two

`HybridSolver.solve` used to do its own SCC decomposition, condensation and topological sort
inline. That logic is now `graph.build_execution_plan`, shared by the solver and the analysis.

The alternative — reimplementing the planner for analysis — would have produced a second source of
truth that drifts, and an analysis that quietly starts describing a pipeline the solver would not
actually run. Worth the refactor.

## Analysis must be solver-aware

The most surprising finding. `IterativeSolver` sweeps **every** step in registration order and
consults the dependency graph not at all; `HybridSolver` follows the graph and runs cyclic blocks
alphabetically. So the same steps, under different solvers, need *different* variables seeded.

The first implementation reported `HybridSolver`'s answer regardless, and was therefore wrong for
every `IterativeSolver` pipeline — it was caught by running the analysis against the Phase 1 demo,
which it incorrectly flagged. Analysis now asks the configured solver for its own ordering.

## What shipped

| Tool | Does |
|---|---|
| `analyze_pipeline` | Execution order, cycles, feedback variables, required seeds, recommended solver |
| `validate_pipeline` | Seven finding types, errors before warnings before info |
| `explain_pipeline` | Prose description |
| `render_pipeline_diagram` | XDSM to a file, Agg forced, never displays |

Four resources (architecture doc, known-issues doc, two worked examples) and two prompts
(`pipeline_from_prose`, `review_pipeline`), both of which route the client through the verify
loop rather than letting it present unchecked code.

Handler errors come back as `{"ok": false, "error": ...}` rather than protocol errors: a missing
file is something the agent should read and act on, not an exception that aborts its turn.

## Note on the SDK

`mcp` 2.x renamed `FastMCP` to `MCPServer` (`from mcp.server.mcpserver import MCPServer`). Code or
documentation written against 1.x will not import. Pinned to `mcp>=2.2.0`.

The extra costs **27 transitive packages** — starlette, uvicorn, cryptography, pydantic — which is
the whole reason it is an extra. Verified that a base install with the SDK absent still imports
`smartmdao` and `smartmdao.mcp.handlers`, failing only at `create_server()` with an actionable
message.

## Related

- [003-determinism-and-the-engineer-in-the-loop.md](003-determinism-and-the-engineer-in-the-loop.md)
  — the governing principle. It is what makes the cost ladder above mandatory rather than a nicety:
  an engineer cannot consent to a run whose cost nobody quoted.
- [002-agent-as-discipline.md](002-agent-as-discipline.md) — the inverse direction, where
  SmartMDAO orchestrates the agent rather than the other way round.
- [../roadmap.md](../roadmap.md) — phasing and exit criteria.
