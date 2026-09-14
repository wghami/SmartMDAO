# How to test SmartMDAO yourself

A step-by-step walkthrough for checking that everything works — and, more usefully, for seeing
*what* it does. Each step says what to run, what you should see, and **what it proves**. Nothing
here assumes you have read the rest of the documentation.

Roughly 20 minutes end to end. Steps 1–4 need nothing but the repository. Steps 5–7 connect the
MCP server to a coding agent.

> Every command is written to be copy-pasted from the repository root.

---

## Step 0 — Set up

``` bash
uv sync
```

This creates `.venv/` and installs the project plus its development dependencies, including both
optional extras (`openturns`, `mcp`) so the full suite can run.

**You should see** a list of installed packages ending with `smartmdao==1.10.0`.

**What it proves:** nothing yet — but note what is *not* there. A base install pulls only h5py,
matplotlib, numpy and scipy. No Jupyter kernel, no OpenTURNS, no MCP SDK.

---

## Step 1 — Run the test suite

``` bash
uv run pytest
```

**You should see** `273 passed` and a coverage table ending in `TOTAL ... 100%`.

**What it proves:** every behavioural claim in this repository is executable. The 100% figure is
load-bearing rather than decorative — it has already caught genuinely dead code, and the rule is
that a line is either tested or deleted.

If you want to see what is being asserted rather than just the count:

``` bash
uv run pytest -v tests/test_analysis.py
```

The test names are written to read as sentences —
`test_which_variable_needs_seeding_follows_alphabetical_step_order` is a claim about behaviour, not
a label.

---

## Step 2 — Run every example

``` bash
uv run python run_all.py
```

**You should see** `24 scripts`, all `✅ Pass`, and a final status report.

**What it proves:** the examples are not decoration. They run in CI, and a change that breaks one
fails the build. It also means any of them can be read *and executed* to check a claim.

---

## Step 3 — See the analysis work

This is the core capability: reading a pipeline's structure without running it.

``` bash
uv run python scripts/pipeline_analysis_demo.py
```

**You should see** four cases. The ones to read carefully:

**Case 1** analyses a Sellar pipeline and prints

```
  NEEDS A GUESS   : y2 (read by discipline_1)
```

Nothing in the source says `y2` needs an initial value. It falls out of the loop structure — and it
is exactly the `y2=1.0` the README passes to `run()`.

**Case 3** is the one worth pausing on:

```
  HybridSolver     runs controller -> sensor     needs: reading
  IterativeSolver  runs sensor -> controller     needs: control
```

Identical steps, identical registration order, **different answer** — because `HybridSolver` orders
a cyclic block alphabetically while `IterativeSolver` sweeps in registration order. Seed the wrong
one and you get a `KeyError` from deep inside the solve.

**What it proves:** the analysis is solver-aware, and it derives facts a human would have to trace
the whole graph by hand to find.

### Try it on your own code

``` bash
uv run python -c "
from smartmdao import analyze, validate, explain
import importlib.util, sys
spec = importlib.util.spec_from_file_location('m', 'scripts/sellar_benchmark_mda.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(explain(m.pipeline, inputs=['z1','z2','x1']))
"
```

Swap in a path to your own model. If nothing prints, jump to Step 6 — your pipeline is probably
not reachable, and that is a known and documented limitation.

---

## Step 4 — See a solve explain itself

``` bash
uv run python scripts/convergence_report_demo.py
```

**You should see** three different outcomes, distinguishable at a glance:

```
converged      after 20 iteration(s), residual 9.537e-07  [damp, offset]
max_iterations after  6 iteration(s), residual 1.000e+00  [never_settles]
abandoned      after  4 iteration(s), residual inf - coupling variable is
               oscillating with period 2, cycling between ['B', 'A'] [flip_flop]
```

**What it proves:** a run tells you what happened to it. The third line is the interesting one — a
system that will *never* converge is detected after 4 sweeps instead of burning all 100, and the
run still returns normally with its residual history intact.

Case 3 of that script shows how to write your own checker that gives up, in about 30 lines and with
no inheritance.

---

## Step 5 — Check the MCP server starts

The connector is an optional extra. It is already installed by `uv sync`, so:

``` bash
uv run smartmdao-mcp
```

**You should see** nothing at all, and the command should hang. **That is correct.** An MCP server
speaks JSON-RPC over stdin/stdout and is waiting for a client. Press `Ctrl-C`.

To prove it really is alive, talk to it by hand:

``` bash
uv run python -c "
import json, subprocess
p = subprocess.Popen(['smartmdao-mcp'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
p.stdin.write(json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2026-07-28','capabilities':{},'clientInfo':{'name':'manual','version':'0'}}})+'\n'); p.stdin.flush()
print(json.loads(p.stdout.readline())['result']['serverInfo'])
p.terminate()
"
```

**You should see** exactly `{'name': 'smartmdao', 'version': '1.10.0'}`.

**What it proves:** the console entry point works. This exact handshake runs in the test suite
(`tests/test_mcp_stdio.py`), so it cannot silently rot.

---

## Step 6 — Connect it to a coding agent

### Claude Code

``` bash
claude mcp add smartmdao -- uv run --directory /absolute/path/to/SmartMDAO smartmdao-mcp
```

Then in a session, `/mcp` should list `smartmdao` as connected with 4 tools.

### Any client that takes a JSON config

``` json
{
  "mcpServers": {
    "smartmdao": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/SmartMDAO", "smartmdao-mcp"]
    }
  }
}
```

If SmartMDAO is installed into an environment already on your `PATH`, the command is simply
`smartmdao-mcp` with no arguments.

**What it proves:** the agent can now read pipeline structure. It could always *write* SmartMDAO
code; what it could not do is see that the code it wrote closed a feedback loop.

---

## Step 7 — Use it, and see it catch something real

In your agent session, try this in order. The point is to watch it find a bug you did not tell it
about.

**7a. Ask it to analyse a file that is fine:**

> Use the smartmdao tools to analyse `scripts/sellar_benchmark_mda.py` with inputs z1, z2 and x1.
> What does it tell you?

Expect: one feedback loop between `discipline_1` and `discipline_2`, coupling on `y1` and `y2`,
`HybridSolver` recommended, and `y2` named as needing an initial guess.

**7b. Ask it to write something new:**

> Write me a SmartMDAO pipeline for a wing: lift from span, chord and speed; structural mass from
> the required lift; total mass from structure plus payload; required lift from total mass. Save it,
> then verify it with the smartmdao tools before you tell me it works.

Expect the agent to discover, *after writing it*, that those four disciplines close a mass-growth
loop — heavier wing needs more lift, more lift needs more structure, more structure is heavier —
and that `total_mass` needs seeding. Nothing in the prose said "feedback loop".

The same thing non-interactively, if you would rather see it without an agent:

``` bash
uv run python scripts/mcp_connector_demo.py
```

**7c. Ask it to review something broken:**

> Run validate_pipeline on `scripts/pipeline_analysis_demo.py` with variable `build_broken`.

Expect seven findings from four deliberate mistakes — because one wrong return annotation breaks the
edge to every consumer of that variable.

**What it proves:** the loop that matters. The agent writes, the tools verify, the agent fixes —
and the verification is arithmetic on a graph, not the model's opinion.

---

## Step 8 — Understand what it cannot do

Being clear about limits is the point, not an apology. Run:

``` bash
uv run python scripts/pipeline_discovery_demo.py
```

**You should see** seven ways of defining a pipeline and what the loader makes of each, ending with
a measurement against this repository:

```
auto-discovered      : 9 of 24   (was 7 before factories)
reachable by name    : 1
genuinely unreachable: 14
```

**Read this honestly.** The tools find a pipeline in two shapes:

``` python
pipeline = Pipeline(...)              # a module-level instance
def build() -> Pipeline: ...          # a factory, which gets CALLED
```

They will **not** call a function speculatively to see what it returns — a module's `run_demo()`
would execute your entire study. So a pipeline built inside a function body and never returned is
unreachable, and that is deliberate.

The 14 unreachable files are demo *scripts*, which naturally build everything inside
`run_*_demo()`. A *model* file usually does not. But if your own model is not being found, this is
why, and the fix is to expose it as a module-level instance or a zero-argument factory.

**Two more things the tools deliberately do not do:**

- **They never execute a discipline.** `analyze` and `validate` read signatures, annotations and the
  dependency graph. This is why they are safe and fast — and why they cannot tell you whether your
  physics is right.
- **They do not run your pipeline.** Not yet; that is Phase 3, and the design is written up in
  [001](design/001-mcp-connector.md). Your agent will happily run it with a shell instead, which is
  exactly the gap that phase closes.

---

## If something fails

| Symptom | Likely cause |
|---|---|
| `pytest` reports fewer than 273 tests, with skips | `uv sync` did not install the extras — check for `openturns` and `mcp` |
| A script fails in `run_all.py` | Run it directly to see the traceback; `openturns` ones skip cleanly with a message if the extra is missing |
| `smartmdao-mcp: command not found` | Use `uv run smartmdao-mcp`, or install with `pip install smartmdao[mcp]` |
| The agent says it cannot find a pipeline | Step 8 — your pipeline is probably local to a function |
| A diagram command hangs | You called `visualize()` directly in a headless shell; pass `view=False`. The MCP path forces this already |

---

## Where to go next

- [handoff.md](handoff.md) — what "done" means here, and the traps that have already cost time
- [003](design/003-determinism-and-the-engineer-in-the-loop.md) — *why* the project is built this
  way: determinism, traceability, and giving the engineer enough information to decide
- [known-issues.md](known-issues.md) — the sharp edges, with severities. Several are deliberate
- [roadmap.md](roadmap.md) — what is planned, and what is blocked on a decision
