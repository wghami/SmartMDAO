# How to test SmartMDAO yourself

A step-by-step walkthrough for checking that everything works — and, more usefully, for seeing
*what* it does. Each step says what to run, what you should see, and **what it proves**. Nothing
here assumes you have read the rest of the documentation.

Roughly 25 minutes end to end. Steps 1–4 need nothing but the repository. Steps 5–7 connect the
MCP server to a coding agent.

> Every command is written to be copy-pasted from the repository root.

---

## Step 0 — Set up

``` bash
uv sync
```

This creates `.venv/` and installs the project plus its development dependencies, including both
optional extras (`openturns`, `mcp`) so the full suite can run.

**You should see** a list of installed packages ending with `smartmdao==1.14.0`.

**What it proves:** nothing yet — but note what is *not* there. A base install pulls only h5py,
matplotlib, numpy and scipy. No Jupyter kernel, no OpenTURNS, no MCP SDK.

---

## Step 1 — Run the test suite

``` bash
uv run pytest
```

**You should see** `388 passed` and a coverage table ending in `TOTAL ... 100%`.

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

**You should see** `29 scripts`, all `✅ Pass`, and a final status report.

**What it proves:** the examples are not decoration. They run in CI, and a change that breaks one
fails the build. It also means any of them can be read *and executed* to check a claim.

> **On a headless machine, set `MPLBACKEND=Agg`.** Several scripts render a diagram, and on an
> interactive backend `plt.show()` blocks forever rather than failing. CI sets it for this reason.

---

## Step 2b — Read the notebooks

``` bash
uv run python run_notebooks.py
```

**You should see** 15 notebooks, all `PASS`, in about 45 seconds.

**What it proves:** [`notebooks/`](../notebooks) is one concept per file, committed **with its
outputs** so GitHub renders what each cell printed. Re-executing them is what stops them drifting
from the library — a notebook whose code no longer matches fails the build rather than sitting
there looking authoritative. CI runs this too.

You do not have to run it to read them: open any notebook on GitHub and the outputs are already
there, diagrams included.

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

**You should see** exactly `{'name': 'smartmdao', 'version': '1.14.0'}`.

**What it proves:** the console entry point works. This exact handshake runs in the test suite
(`tests/test_mcp_stdio.py`), so it cannot silently rot.

---

## Step 6 — Connect it to a coding agent

### Claude Code

``` bash
claude mcp add smartmdao -- uv run --directory /absolute/path/to/SmartMDAO smartmdao-mcp
```

Then in a session, `/mcp` should show `smartmdao` connected. To see what it offers — and
why the tools do *not* appear as slash commands — see Step 6a.

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

**What it proves:** the agent can now read pipeline structure, *and* it can look up the API before
writing code. It could always write plausible SmartMDAO code; what it could not do is check that
the code it wrote uses functions that exist and forms the graph it intended.

---

## Step 6a — Seeing what the server offers (tools are not slash commands)

A server exposes three different things, and only one of them shows up when you type `/`:

| | What it is | How you reach it |
|---|---|---|
| **Tools** | Functions the **model** decides to call | Never a slash command |
| **Prompts** | Templates **you** trigger | `/smartmdao:pipeline_from_prose` |
| **Resources** | Documents the model can read | Fetched by the client |

So typing `/` and seeing only:

```
/smartmdao:review_pipeline (MCP)
/smartmdao:pipeline_from_prose (MCP)
```

is **correct and complete** — those are the only two prompts. The seven tools are deliberately absent
from that list, because you do not invoke them; the model does.

**Three ways to see the tools:**

1. **Press Enter on `/mcp` itself.** That opens the MCP panel, where you select `smartmdao` and
   view its tools, resources and prompts. Typing `/mcp` only filters the slash-command menu, which
   is what shows prompts.
2. **Ask the agent** — simplest and most reliable:
   > What smartmdao tools do you have available?
3. **Interrogate the server directly**, which also tells you the version it is running:

``` bash
uv run python -c "
import json, subprocess
p = subprocess.Popen(['smartmdao-mcp'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
def rpc(i, m, params=None):
    p.stdin.write(json.dumps({'jsonrpc':'2.0','id':i,'method':m,'params':params or {}})+'\n'); p.stdin.flush()
    return json.loads(p.stdout.readline())
print(rpc(1,'initialize',{'protocolVersion':'2026-07-28','capabilities':{},'clientInfo':{'name':'c','version':'0'}})['result']['serverInfo'])
p.stdin.write(json.dumps({'jsonrpc':'2.0','method':'notifications/initialized','params':{}})+'\n'); p.stdin.flush()
print('tools  :', [t['name'] for t in rpc(2,'tools/list')['result']['tools']])
print('prompts:', [x['name'] for x in rpc(3,'prompts/list')['result']['prompts']])
p.terminate()
"
```

**A quick way to tell whether your session is stale:** ask the agent if it has `smartmdao_cookbook`.
If it does not, it connected before that tool existed — see Step 6b.

---

## Step 6b — Updating a server you have already added

Worth knowing, because "I changed the code and the agent still sees the old behaviour" is a
confusing five minutes.

**In most cases you do not need to re-add anything.** The server is a subprocess that your client
spawns fresh, so how you added it decides what "update" means:

| How you added it | To pick up changes |
|---|---|
| `uv run --directory /path/to/SmartMDAO smartmdao-mcp` | **Nothing.** `uv run` re-resolves the checkout each launch — just start a new session |
| `pip install smartmdao[mcp]` into a fixed environment | `pip install -U smartmdao[mcp]`, then start a new session |
| Anything | **Start a new session.** A running client keeps the old subprocess alive |

Documentation content — what `smartmdao_cookbook` returns — is read from disk *per call*, so in a
checkout even that needs no restart.

**To inspect, change or remove the registration itself:**

``` bash
claude mcp list              # what is configured, and whether it is connecting
claude mcp get smartmdao     # the exact command, args and scope
claude mcp remove smartmdao  # drop it
```

To change the command — a different checkout, or moving from a checkout to an installed package —
remove and re-add; there is no in-place edit:

``` bash
claude mcp remove smartmdao
claude mcp add smartmdao -- uv run --directory /new/path smartmdao-mcp
```

`claude mcp add` also takes `-s/--scope` (`local`, `user` or `project`). If you added it once at
`local` scope and again at `user` scope, you will have two entries — `claude mcp list` is how you
find out.

**Verify the update landed** by asking the server its version, which is read from the installed
package:

``` bash
uv run python -c "
import json, subprocess
p = subprocess.Popen(['smartmdao-mcp'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
p.stdin.write(json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2026-07-28','capabilities':{},'clientInfo':{'name':'manual','version':'0'}}})+'\n'); p.stdin.flush()
print(json.loads(p.stdout.readline())['result']['serverInfo'])
p.terminate()
"
```

If the version printed is not the one you expect, the client is talking to a different install than
the one you just changed — check `claude mcp get smartmdao` for the actual command.

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

### 7d. The mistake this catches that nothing else would

Run this directly — it is the most instructive single command in the guide:

``` bash
uv run python -c "
from smartmdao.mcp import validate_pipeline
r = validate_pipeline('tests/fixtures/wing_mda_disconnected.py')
for f in r['findings']: print(f\"[{f['severity']}] {f['code']}\n  {f['message']}\")
"
```

That fixture is **verbatim output from a coding agent** asked for exactly the wing model in step 7b.
It is structurally valid, it converges, its arithmetic is right — and its answer is the same for a
48 m wing at walking pace as for the original, because `compute_lift` consumes span, chord and speed
and produces a value *nothing reads*. The mass loop underneath is closed on itself.

```
[warning] disconnected-graph
  The pipeline falls into 2 disconnected pieces, so nothing computed in one can
  affect another: ['compute_lift'] -> ['lift'] consumed by nothing; [...]
```

**What it proves, and its limit.** Static analysis cannot tell you whether your physics is right.
It *can* tell you a discipline is wired to nothing — which is the difference between "the tools
can't check engineering" as an excuse and as an accurate, narrow statement.

### A note on `inputs`

You will see `inputs_used` in every response:

```
"inputs_used": {"requested": ["z1", "z2", "x1"], "found_in_source": ["y2"]}
```

The tools read the `run(...)` call in your file and merge what they find with whatever you asked
for. This exists because an agent once declared the design variables, forgot the cycle's seed, and
was told the seed was missing — so it reported a *working* file as broken and offered to patch it.
`found_in_source` is how you tell "the file already supplies this" from "you told me about this".

---

## Step 7e — Actually running one, without losing an afternoon

The tools so far execute nothing. This one does — in a child process, under a wall clock.

``` bash
uv run python scripts/cost_ladder_demo.py
```

**You should see** five cases. The two that matter:

**The cheapest rung is also the measurement.** `rung="smoke"` runs one sweep per discipline. It
reports `converged: False`, which is *correct* — one sweep is not convergence — and it returns:

```
one_sweep_seconds            0.0417
full_worst_case_seconds      2.502
note   one sweep took 0.0417s; a full run may need up to 60 sweeps, so budget
       up to 2.5s. Quote this before running it.
```

That is the difference between "this might take a while" and a figure you can agree to. Worst case
only — how many sweeps a loop needs is not knowable in advance.

**Failures come back as data.** A solve that will not stop is killed and says so; a discipline that
dies without raising is reported as a crash — and the demo keeps running afterwards, which is the
whole argument for the separate process.

**What it does not prove:** safety. Your agent has a shell. If the connector refuses to run
something, it will run `python model.py` instead — unsandboxed and untimed. The subprocess buys a
hard kill, typed results and crash isolation, not protection. Saying otherwise would be claiming
something we do not deliver.

Ask your agent directly:

> Run `scripts/sellar_benchmark_mda.py` with the smartmdao tools. Tell me what it would cost before
> you run the full thing.

Expect a smoke run first, a quoted estimate, and only then a full run.

---

## Step 7f — The one check nothing else can do

``` bash
uv run python scripts/translation_drift_demo.py
```

Two translations of one model. Identical disciplines, identical physics, identical inputs — the
only difference is which variable the solver watches to decide it has finished.

```
faithful : converged=True  after  1 sweeps
idiomatic: converged=True  after 78 sweeps

y1   faithful = 0.500000
     idiomatic = 9.817911
     relative  = 94.9%
```

**Both report success.** Neither raises. Neither is structurally invalid — `validate()` is clean on
both. A reviewer reading either file finds nothing wrong.

**What it proves:** every other tool in this connector reads structure, and structure is identical
here. Only running both and diffing the answers reveals that a translation changed the result. That
is why `compare_runs` exists, and why it is a better argument for execution tooling than "run my
pipeline".

For the **workflow** rather than the failure mode — including how to compare against code that is
not a pipeline at all — run:

``` bash
uv run python scripts/translation_equivalence_demo.py
```

It takes this project's own README loop, wraps it as a one-step pipeline, and compares it to the
translated version. They agree at a sensible tolerance; tighten it and a genuine difference appears,
because the hand-written loop `break`s *before* assigning `y2 = y2_next` and so returns the previous
value. About 2e-8 — real, tiny, and a judgement call rather than a bug. **Your tolerance is where
that judgement lives.**

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
- **`run_pipeline` does execute your code** — that one is not analysis. It runs in a child process
  under a wall clock, which buys a hard kill and crash isolation, **not** safety. Your agent has a
  shell either way; what this changes is that the run is bounded and the result is typed.
- **`compare_runs` compares answers, not intent.** It tells you two pipelines disagree; it cannot
  tell you which one is right. That is still the engineer's call — the point is that the choice is
  now made with the difference in front of you.

---

## If something fails

| Symptom | Likely cause |
|---|---|
| `pytest` reports fewer than 558 tests, with skips | `uv sync` did not install the extras — check for `openturns`, `mcp` and `clingo` |
| A script fails in `run_all.py` | Run it directly to see the traceback; `openturns` ones skip cleanly with a message if the extra is missing |
| `smartmdao-mcp: command not found` | Use `uv run smartmdao-mcp`, or install with `pip install smartmdao[mcp]` |
| The agent says it cannot find a pipeline | Step 8 — your pipeline is probably local to a function |
| Typing `/` shows only two smartmdao entries | Correct — those are prompts. Tools are never slash commands; see Step 6a |
| You changed the code but the agent behaves as before | Step 6b — start a new session; the old subprocess is still running |
| A `validate` finding names a variable your script clearly passes | Check `inputs_used.found_in_source`; if it is empty, your `run()` call is built too dynamically to read |
| A notebook shows stale output | Re-run `run_notebooks.py`; the committed outputs are regenerated, not hand-edited |
| A run is killed at 60s | That is the wall clock. Try `rung="smoke"` to see the unit cost, then raise `timeout_seconds` deliberately |
| A diagram command hangs | You called `visualize()` directly in a headless shell; pass `view=False`. The MCP path forces this already |

---

## Where to go next

- [cookbook.md](cookbook.md) — how to actually write pipelines; every snippet is executed by the suite
- [handoff.md](handoff.md) — what "done" means here, and the traps that have already cost time
- [003](design/003-determinism-and-the-engineer-in-the-loop.md) — *why* the project is built this
  way: determinism, traceability, and giving the engineer enough information to decide
- [known-issues.md](known-issues.md) — the sharp edges, with severities. Several are deliberate
- [roadmap.md](roadmap.md) — what is planned, and what is blocked on a decision
