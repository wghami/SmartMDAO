# Known issues and limitations

Things that are true about the current code, recorded so they are not rediscovered. Nothing here
is a bug report against behaviour that already works as documented — these are the sharp edges.

**Severity key:** 🔴 blocks planned work · 🟡 papercut · ⚪ by design, documented for visibility

---

## 🟡 The diagram draws a parameter with a default as a missing input

`PipelineVisualizer` classifies a variable as missing when it is consumed, not produced and not
passed, without asking whether the parameter has a default. `latency(distance, note=0.0)` therefore
gets a red `note (?)` hexagon, while `validate()`, which uses only required parameters, correctly
reports nothing. The two views of one pipeline disagree. **Fix direction:** classify with the
analysis' `_required_inputs`, and decide whether a defaulted, unsupplied parameter is drawn at all.
Queued in the [roadmap](roadmap.md).

---

## ✅ RESOLVED in 1.27.0 — units lived only in variable names

dB wired into linear, km into m: nothing read the `_db` or `_km` in a name, so a slip produced a
number, and the number converged. **Fixed** by [008](design/008-units.md): declared units, checked
for consistency and never converted.

---

## ✅ RESOLVED in 1.27.0 — two consumers could declare incompatible types for one input

Found while designing units. `distance: float` in one step and `distance: str` in another, both
reading the same external input, passed `validate()`. The conflict surfaced only at `run()`, as a
`TypeMismatchError`, once a value was passed. It is now a `type-mismatch` from `validate()`, and it
is reported only when no single value could satisfy both annotations.

---

## ✅ RESOLVED in 1.26.0 — a pipeline could only be handled by a server that had its libraries

Reported from real use. The server imported pipeline files in its own process and ran them with
its own interpreter. A model importing networkx, cvxpy or a GeoTIFF reader could not even be
analysed unless the server's environment held them, and two models pinning different versions of
a library needed two servers. **Fixed** by [007](design/007-project-interpreter.md): each file is
handled in its own project's environment, reported on every response.

---

## ✅ RESOLVED in 1.26.0 — a print() in a discipline broke run_pipeline

From 1.13.0, the run process answered in JSON on the same stdout a discipline's `print()` wrote
to. Any printing discipline turned a successful run into "The run process returned output that was
not valid JSON". A file that printed while being *loaded* also wrote into the MCP server's JSON-RPC
stream. The Python client logged "Failed to parse JSONRPC message from server" and recovered;
other clients need not. **Fixed**: user code prints to stderr while it is loaded, run or worked
on, and that text comes back in `stderr`.

---

## 🟡 compare_runs uses the server's environment

Every other tool handles a file in its own project's environment (1.26.0). `compare_runs` still
loads both files in the server's process before running them, so it has the pre-1.26.0 limit: both
files' imports must be installed where the server runs. **Fix direction:** run each side through
the worker's `run` op, resolved separately, and move the side-effect check into the worker. Record
006 needs the same per-point path, so do it with the sweep.

---

## ✅ RESOLVED — a notebook's outputs could go stale without failing anything, and every re-run rewrote all sixteen

CI re-executed the notebooks but only failed on an exception, so a notebook whose committed output
no longer matched what its code printed passed. Meanwhile every local re-run rewrote all sixteen
files — per-cell timestamps, measured timings, temp paths — so each PR carried noise to be
discarded by hand, and a real output change was easy to miss in it. Notebook 06 also printed a
`frozenset` in hash order, which changes with `PYTHONHASHSEED`: an unstable output in the
notebook series about a project whose governing principle is determinism.

**Fixed** without freezing anything: timestamps are no longer recorded; the set is printed sorted;
and `run_notebooks.py` compares each re-run with the committed copy after masking only
measurements, writing back only what really changed. `--check`, which CI now runs, fails on a
stale output and names the first cell that differs. Checked by tampering with a committed output
(caught) and by re-running under two hash seeds (unchanged).

**What did not work at first:** comparing images byte for byte in CI. The first CI run failed the
two notebooks with diagrams while all fourteen others matched: the runner's fonts rasterise
differently. Locally a re-run is still exact, pixels included, so a changed diagram is rewritten;
`--check` compares that an image is present and every word of text exactly, but not the pixels.

---

## ✅ RESOLVED in 1.24.0 — an entry file inside a package could not be loaded

`analyze_pipeline(pkg/pipeline.py)` with `from .physics import lift` failed with "attempted
relative import with no known parent package": the loader imported every file under a generated
module name with its own directory on `sys.path`. Reported from real use; the workaround was to
keep entry files beside the package.

**Fixed** by importing a file inside an `__init__.py` chain as `pkg.module`, with the package's
parent on `sys.path` — what `python -m pkg.module` does.

---

## ✅ RESOLVED in 1.24.0 — an edited sibling module was analysed as its first version

Found while fixing the entry above. The MCP server is one long-lived process, and the loader
dropped only the pipeline file's own module after a load. A module the file imported from the
user's project — `physics.py` beside it — stayed cached, so after editing it every analysis
still saw the old signature until the server was restarted. Nothing failed; the answer was
simply about code that no longer existed.

**Fixed** by dropping, after each load, every module it imported from the user's project.
Installed libraries stay cached: re-importing them is slow, and some extension modules cannot be
imported twice.

---

## ✅ RESOLVED in 1.24.0 — every analysis call had to repeat the input list

Reported from real use on a contract-first pipeline: 23 disciplines, 91 external inputs, and no
`run()` call for the tools to read them from. Every `analyze` / `validate` / `render` call needed
the full list — 91 names pasted into 18 calls — and a first skeleton validated as broken, with 35
false `missing-input` findings, until the list was supplied.

**Fixed** by `Pipeline(inputs=[...])`: declared once, used by every analysis and MCP tool when a
call passes none. An explicit list still wins. A declared name no step reads is reported as
`unconsumed-input`, which is also the only signal when a typo hits a parameter that has a default.

---

## ✅ RESOLVED in 1.24.0 — naming a factory explicitly crashed every MCP handler

`analyze_pipeline(path, variable="build")`, where `build` is annotated `-> Pipeline`, failed with
`TypeError: 'Factory' object is not iterable` from 1.12.0 on. The loader reused the variable that
held the inputs read from source for the factory it had found, so every handler iterated a
descriptor. Discovered factories and named plain callables were unaffected, which is why it went
unnoticed: the tests named factories, and went through handlers, but never both at once.

---

## ✅ RESOLVED in 1.21.0 — `visualize()` ignored `orientation` and `graph_type`

Both parameters sat in the public signature, typed `Literal['TB', 'LR']` and
`Literal['flow', 'bipartite']`, and **neither changed the output** — every combination produced
byte-identical files. They were left over from a renderer that predated the XDSM view, which
combines what `"flow"` and `"bipartite"` used to show separately and always reads top-left to
bottom-right.

**Removed**, rather than documented as inert. A `Literal` of two options is a strong hint that
choosing between them does something, and a parameter that lies is worse than one that is absent.
Breaking for anyone passing them, hence the version bump; `PipelineVisualizer.build()` now takes no
arguments and `render_pipeline_diagram` no longer accepts them over MCP.

Found while writing [`notebooks/02-visualization.ipynb`](../notebooks/02-visualization.ipynb),
which hashed the output of all three combinations rather than asserting they differed.
`test_build_takes_no_layout_options` pins the signature so they are not reintroduced by habit.

---

## 🟡 `visualize()` hangs in a headless process

`PipelineVisualizer.render()` ([visualization.py:94](../smartmdao/visualization.py:94)) defaults to
`view=True`, which calls `plt.show()`. With an interactive matplotlib
backend and no display, this blocks.

CI works around it by setting `MPLBACKEND: Agg` in the workflow environment. **The library itself
does not.** Any server-side or headless consumer must force the Agg backend and pass
`view=False`.

*Severity downgraded:* `smartmdao.mcp.rendering` now forces Agg (with `force=True`, since
`pyplot` is already imported by the time it runs) and never passes `view=True`, so the server path
is safe. The trap remains for anyone calling `visualize()` directly in a headless process.

*Fix direction:* changing the library default is a behaviour change for existing interactive
users, so it needs its own decision rather than being folded in here.

---

## ✅ MOSTLY RESOLVED in Phase 2.5 — the MCP loader could only see module-level pipelines

`load_pipeline` used to require a `Pipeline` assigned to a module-level variable, which read **7 of
this repository's own 23 scripts**. Every failure was a factory function — normal, good Python.

**Fixed:** the loader now calls factories, either a module-level callable declaring `-> Pipeline`
or one the caller names explicitly. Ambiguity lists every candidate; a factory needing arguments is
reported with the argument names.

**Remaining, and unfixable:** a pipeline built inside a function body and never returned. There is
nothing to call and nothing to read; reaching it would mean running the function, which is the one
thing this layer promises not to do. Measured after the change: **9 auto-discovered, 1 more by
name, 14 unreachable** — where the 14 are demo scripts building pipelines inside `run_*_demo()`.
That shape is normal for a *script* and unusual for a *model*, so the figure understates the
picture for real engineering code.

*Guidance for anyone wanting their model readable:* expose it as a module-level instance, or as a
factory annotated `-> Pipeline` and callable with no arguments. See
[`scripts/pipeline_discovery_demo.py`](../scripts/pipeline_discovery_demo.py).

---

## ✅ RESOLVED in 1.11.0 — an agent had no reliable way to learn the API

The MCP server exposed two example scripts as *resources* and the `pipeline_from_prose` prompt
mentioned none of them. Since MCP resources must be explicitly fetched and many clients never
surface them, the practical grounding available to a coding agent was the README — which covers a
fraction of the API. Generated code was plausible against functions that do not exist.

**Fixed** by serving guidance as a **tool** rather than a resource (`smartmdao_cookbook`), because
tools get called and resources get ignored; by naming it in the server `instructions`, which are
always in context and are what actually drives the call; and by writing
[cookbook.md](cookbook.md), whose every snippet is executed by `tests/test_cookbook.py`.

Three tests keep it honest: every snippet runs, every referenced script exists, and every name in
`smartmdao.__all__` is covered. The last one immediately found 11 exported names with no guidance
at all.

`AGENTS.md` (symlinked as `CLAUDE.md`) covers agents working in the repository rather than through
MCP.

---

## ✅ RESOLVED in 1.13.0 — no way to run a pipeline cheaply, or to know what it would cost

An engineer will ask the agent to run the model. Refusing is not available to us: the agent has a
shell and will run `python model.py` instead — unsandboxed, untimed, with results scraped from
stdout. Refusal does not create a boundary, it moves the work somewhere with less control.

What is missing is the middle of the cost ladder:

| Rung | Cost | Status |
|---|---|---|
| `analyze` / `validate` | free, nothing runs | shipped |
| single sweep | one call per discipline | **expressible today** via `max_iterations=1`, not exposed |
| budgeted run | capped sweeps + wall clock | missing |
| full run | whatever it takes | missing |
| `optimize` | full run × 10²–10³ | missing |

The single sweep is the interesting rung twice over: it is the cheapest possible smoke test *and*
it measures the unit cost, so the estimate for every rung above it falls out of it. Measured on a
toy model with a 20 ms discipline: one sweep 0.04 s, full run 0.90 s — a 22× ratio the engineer
could have been told before committing.

**Fixed** by `run_pipeline`, which takes a `rung`: `smoke` (the default — one sweep per
discipline), `budgeted` (capped sweeps) or `full`. A smoke run returns a `cost_estimate` derived
from its own measured sweep, so the agent quotes a number instead of a disclaimer. Every rung runs
in a child process under a mandatory wall clock.

`smoke` is the default deliberately: an unbounded run should never be the thing that happens by
accident.

---

## ✅ RESOLVED in 1.14.0 — a translated pipeline could silently change the answer

Converting hand-written code to SmartMDAO is a stated use case, and the risk is semantic drift that
nothing detects. The README's own "without SmartMDAO" example is the illustration:

```python
if abs(y2_next - y2) < 1e-6: break     # converges on y2 ALONE
```

The obvious translation converges on a `max()` across **both** `y1` and `y2` — a different
criterion, which can stop at a different iteration or not at all. Faithfulness requires
`target_var="y2"`, and nothing warns you. Similarly, a hand loop using under-relaxation
(`y2 = 0.5*y2 + 0.5*y2_next`) loses its damping entirely, since SmartMDAO has no built-in
relaxation, and may diverge where the original converged.

A translation that quietly changes the answer is worse than no translation: it looks cleaner, so it
gets trusted.

**Fixed** by `compare_runs`: both pipelines, the same inputs, a diff of the resulting state.
Numbers compare within a tolerance so a different iteration count is absorbed; anything non-numeric
compares exactly, because there is no "nearly" for a frozenset of decisions. A different
*destination* counts too — close numbers are not a match when one side never converged.

Demonstrated on exactly the drift described above: two translations differing only in which
variable the solver watches, **both reporting `converged: True`**, with one answer 95% out. Nothing
that reads structure could catch it, because both files are perfectly well-formed.

Whether a translation should be *faithful* (preserve the original's convergence semantics) or
*idiomatic* (use the default and accept small numerical differences) remains the engineer's
explicit choice, per [003](design/003-determinism-and-the-engineer-in-the-loop.md) — but now it is
a choice made with the difference in front of them.

---

## ✅ RESOLVED in 1.12.0 — a disconnected discipline was invisible

Found in real use. A coding agent asked for a wing sized from span, chord and speed produced a
pipeline that **validated clean, converged, and whose arithmetic was correct** — and whose answer
did not depend on span, chord or speed at all. `compute_lift` consumed them and produced `lift`,
which nothing read; the mass loop underneath was closed on itself.

The graph was in two disconnected pieces. Nothing reported it.

**Fixed** by `disconnected-graph` (warning): if the dependency graph falls into more than one
weakly-connected piece, every piece is named along with the outputs nothing consumes. Measured
against all nine loadable pipelines in `scripts/`: **nine clean, and it isolates the bad one.**

Deliberately *not* a general "orphaned output" check — every healthy pipeline has terminal outputs
(`objective`, `constraint_1`), so flagging unused variables alone would fire on almost everything.
The *disconnection* is what is diagnostic. The original Phase 2 plan had it the other way round;
see the correction in [roadmap.md](roadmap.md).

The file is kept verbatim as `tests/fixtures/wing_mda_disconnected.py`.

---

## ✅ RESOLVED in 1.12.0 — a seed the file already passed was reported as missing

Also found in real use, and worse: the tool reported a **working file as broken**.

An agent called `analyze_pipeline` on `scripts/sellar_benchmark_mda.py` with
`inputs=["z1","z2","x1"]`. The script already passes `y2=1.0`, but the agent had to *guess* what
the file supplies, guessed the design variables, and omitted the seed — so the tool flagged as
missing precisely the thing the agent forgot to mention. The agent then reported the pipeline as
invalid and offered to patch a file that was fine.

Circular: you had to know the answer to ask the question correctly.

**Fixed** by reading the file's own `run()` call statically (AST, nothing executed) and merging
those names with the caller's. Handles `run(z1=..., y2=...)` and the
`inputs = {...}; run(**inputs)` shape the benchmarks use. The response reports `inputs_used` split
into `requested` and `found_in_source`, so the agent can say *where* a value came from rather than
asserting. Dynamic construction is still invisible — this narrows the guessing, it does not
eliminate it.

---

## ✅ RESOLVED in 1.19.0 — INFEASIBLE said nothing about why

[003](design/003-determinism-and-the-engineer-in-the-loop.md) promises that UNSAT gives a
machine-checkable "why not" an engineer can take into a design review. Until 1.19.0 it gave a
sentinel and nothing else.

**Fixed** by `RuleDiscipline.explain_infeasible`, which returns a `Conflict`: the **minimal** set of
facts that cannot hold together — remove any one and the rules become satisfiable — or
`rules_alone=True`, meaning the program contradicts itself and no input could have worked. The
distinction is the useful part: it says whether to read the requirements or the program.

**Not built on clingo's unsat core**, and the reason is worth keeping.
[004](design/004-rule-backed-disciplines.md) flagged the core mapping as a risk; measuring it found
a sharper problem. The core is expressed in solver literals *and is not minimal* — on a three-fact
conflict where one fact appears in no rule at all, it named **all three**. An explanation that
implicates an innocent constraint is worse than none, because it gets acted on and the real
contradiction survives.

Dropping one fact at a time costs one solve per fact and is minimal by construction. That cost is
why it is a separate method: `solve()` stays cheap, so a convergence loop is not charged for an
explanation nobody read.

---

## ✅ RESOLVED in 1.19.0 — clingo printed diagnostics straight to stderr

An injected fact never occurs in a rule head — that is what makes it a fact — so clingo emitted
`atom does not occur in any rule head` for **every fact on every solve**. Inside a convergence loop
that is once per sweep per fact, and a perfectly correct program looked alarming.

**Fixed** by passing a `logger` callback to `clingo.Control`, routing its diagnostics to
`smartmdao.rules` at debug level. Pinned by a test asserting stderr stays empty across a solve.

---

## 🟡 A grounding blow-up cannot be interrupted in-process

**Partly addressed in 1.18.0, and the part that is not is worth stating plainly.**

`RuleDiscipline(budget_seconds=...)` now bounds solving, and the program is memoised on its facts
so a repeated fact set costs nothing. But the budget **cannot stop grounding**, which is the
worst-case-exponential half ([003](design/003-determinism-and-the-engineer-in-the-loop.md), risk 4).

Measured against clingo 5.8.2, because the shape of the fix depended on it:

| | Interruptible? |
|---|---|
| `handle.cancel()` during a solve | **Yes** — returns promptly, `result.interrupted` is `True` |
| `Control.interrupt()` during `ground()` | **No** — ignored; a 1.7M-atom grounding ran to completion in 2.9 s |

So a generated program that is accidentally intractable *to ground* still hangs the process, and
`budget_seconds` will not save you. Calling that a budget without saying which half it covers would
be the same overclaim Phase 3 records about the subprocess, where a reliability mechanism kept
getting described as a safety one.

*What you can do today:* `run_pipeline` runs a whole pipeline in a subprocess under a mandatory wall
clock, so it does provide a hard kill — at a per-call cost that would be absurd once per sweep.

*Fix direction:* nothing in-process will do it. Either a persistent worker process that grounds on
request and can be killed, or an upstream grounding limit clingo does not currently expose.

---

## ⚪ Grounding happens once per distinct fact set, not once

Memoisation means a repeated fact set is free, but a *new* one re-grounds the whole program. Inside
a converging loop that is usually a handful of grounds rather than one per sweep, and
`RuleDiscipline.cost` reports exactly how many.

Grounding once and using `assign_external` for the facts would reduce it to a single ground, but it
constrains how the `.lp` may be written — the injected atoms would have to be declared `#external`.
Measured: injecting facts as plain program text works **whether or not** the program declares them
external, so the simpler form was chosen deliberately. A real trade, not an oversight.

---

## 🟡 A rule-backed discipline in a loop needs *two* seeds, not one

`RuleDiscipline` registers under `rules_<program stem>` by default, and that name joins the
alphabetical ordering inside a cyclic block just as `discretise_<band>` does.

Put a rule-backed discipline into a mass loop that already has a band and the cycle typically needs
**two** initial guesses — one for whichever step sorts first, one for the band — where the same
loop needed one before. Adding a discipline changed what `run()` requires, with no existing
discipline touched.

*Mitigation:* `name=` is a constructor argument precisely so the ordering is the engineer's to
choose, and `analyze()` reports the real answer because it plans over the same `effective_steps`
the solver runs. Ask it.

---

## 🟡 Naming a fact after the loop's own variable silently changes the topology

`RuleDiscipline(facts=["mass_band"])` wires an edge from the loop's own variable into the rules, so
`HybridSolver` pulls the discipline into the SCC and applies it **once per sweep, on unconverged
values**. `facts=["prior_mass_band"]` leaves it on the linear part, where the engineer drives it
with an outer loop.

One word of difference. The two `.lp` files can be identical apart from the predicate name, and
nothing about either looks wrong.

This is the same mechanic [002](design/002-agent-as-discipline.md) pins for a model step
(`prior_violations`, not `violations`), and it now applies to `facts`. Pinned by
`test_naming_a_fact_after_the_loops_own_variable_collapses_it_into_the_cycle`.

*Partially addressed in 1.17.0:* `validate()` reports `rules-in-cycle` when it happens, so the
consequence is visible even though the cause still is not.

---

## ✅ PARTLY ADDRESSED in 1.17.0 — a decision inside a loop is now reported

Not resolved — **reported**. The two entries below describe failures that are real and remain real;
what changed is that `validate()` now names them up front instead of leaving them to be discovered
by a solve that quietly picks one answer.

- `rules-in-cycle` — a rule-backed discipline inside a cyclic block.
- `discretisation-in-cycle` — a band derived inside a cyclic block.

Both are decided statically from the SCC decomposition, with **nothing executed** — pinned by a
test that makes `_load_clingo` raise and asserts the findings still appear. Both are **warnings**:
002 finds the topology defensible when the choice must genuinely react to intermediate state, and
003 forbids deciding for the engineer.

**Also downgraded in 1.17.0:** `discretisation-unused` went from warning to **info**. It fires when
no *step* consumes a band — which is exactly what the **recommended** B+D topology looks like, since
there the caller reads the band from the result and acts on it outside the pipeline. An orphan and a
deliberate terminal output are indistinguishable statically, and a warning that fires on the
recommended pattern teaches people to ignore the finding list. Found while building the topology
checks, not by design.

---

## 🟡 The solve watchdog runs on a second thread, and CI failed once

`RuleDiscipline`'s budget is enforced by a daemon thread that calls
`SolveHandle.cancel()` when the wall clock expires. That is the only concurrency
anywhere in this library.

`rule_backed_discipline_demo.py` failed once in CI and passed eight times locally — five plain
runs, three pinned to one core — and then passed CI on the next attempt. No traceback was available
because `run_all.py` did not print one (fixed in the same change).

The only plausible mechanism found: the watchdog could call `cancel()` on a handle the main thread
was already disposing, which reaches a C extension. **1.22.0 serialises the two with a lock**, so a
watchdog that wakes after the caller has finished does nothing.

**Recorded as suspected, not proven.** The failure was never reproduced, and a fix that cannot be
demonstrated against the symptom is a hypothesis. If it recurs, `run_all.py` now prints the captured
output, which is where to start.

*Fix direction if it does recur:* drive the timeout from the main thread with `handle.wait(timeout)`
instead of a watchdog. That removes the cross-thread call entirely, at the cost of restructuring the
optimal-model enumeration loop, which needs a per-model wait rather than one budget for the solve.

---

## ✅ RESOLVED in 1.23.0 — a declared side effect in a loop was reported, not prevented

In 1.22.0 `validate()` reported `side-effect-in-cycle` and nothing else, and `effects="once"` was a
statement of intent nothing enforced. As of 1.23.0:

- **`effects=True` that would repeat is refused by `run()`** with `SideEffectError`, before
  anything executes — measured with a counter, not inferred.
- **`effects="once"` is latched**: it executes on the first sweep of a `run()` and reuses the result.
- **Anything that multiplies runs refuses any declared effect** unless `allow_effects=True`:
  `PipelineEvaluator`, because an optimizer runs the pipeline once per evaluation and a per-run
  latch cannot help; and `compare_runs`, because it executes both files.

This is the one place SmartMDAO refuses rather than warns, deliberately — see
[005](design/005-side-effecting-steps.md). Every other failure here is recoverable; a sent message
is not.

---

## 🟡 `effects="once"` inside a loop makes the loop converge somewhere else

Found by measuring the latch rather than reasoning about it. On a two-step loop whose fixed point
is 20, `effects="every-sweep"` settles at 20 and `effects="once"` settles at **10** — and **both
report `converged`**.

It is not an edge case. A step inside a cyclic block is there *because* its output feeds back, so
latching it always freezes a coupling the loop depends on. The loop then converges against the
first-sweep value rather than its own fixed point.

*Reported* as `side-effect-latched` (warning). *Fixed* structurally, by the engineer: split the
step into a pure part that stays in the loop and a side-effecting part on the linear part after it,
where it runs once on the converged result. Demonstrated in
[`notebooks/15-side-effects.ipynb`](../notebooks/15-side-effects.ipynb).

---

## ✅ RESOLVED in 1.23.0 — analysing a file executed its module-level `run()`

`load_pipeline` imports a file to find its pipeline, and importing runs top-level code. A bare
`pipeline.run(...)` at module level — the way a great many scripts are written — therefore
**executed the whole study every time the file was analysed**. Measured: two calls to
`validate_pipeline` fired a declared side effect twice, and `run_pipeline` executed such a file
**twice** per run, once at import and once for real.

That broke the analysis layer's first invariant — *introspection never requires execution* — and
it predates Phase 5; side effects only made it visible. A pipeline with no declared effects was
equally affected, just less noticeably.

**Fixed** by suspending `Pipeline.run()` on the loading thread for the duration of the import.
Calling `run()` at top level now loads normally and executes nothing; *using* its result raises an
explanation pointing at `if __name__ == "__main__":` rather than returning an empty dict that would
be misread as an answer. Thread-local, so a real solve elsewhere is unaffected.

**Behaviour change:** a file whose top-level code reads the result of a module-level `run()` no
longer loads. That is the honest outcome — it only ever loaded by running the study.

---

## ⚪ Nothing verifies that a step declared pure is pure

`effects` is a claim the author makes about their own function, like `outputs=[...]`. Nothing
inspects the body, and nothing could in general — a step can write a file and return a float, which
is exactly why the declaration is not inferred.

This layer improves on silence, not on certainty. A pipeline with no declarations is not a pipeline
with no side effects; it is a pipeline nobody has annotated.

---

## 🔴 A threshold inside a feedback loop gives the loop more than one answer

Found in 1.15.0 by running [`scripts/discretisation_demo.py`](../scripts/discretisation_demo.py),
not by reasoning about it.

A band inside a cycle makes the loop piecewise: structure mass depends on which band the total
falls in, and the total depends on the structure mass. With a 500 kg cutover and a 250 kg payload,
**both** of these are genuine fixed points:

| Initial guess | Settles at | Band | Status |
|---|---|---|---|
| `total_mass_kg=400` | 450 kg | `light` | `converged`, 2 iterations |
| `total_mass_kg=900` | 510 kg | `heavy` | `converged`, 2 iterations |

450 really is light and 510 really is heavy, so neither run is wrong and neither is a rounding
artifact. **The initial guess alone decides which answer comes back, and nothing in the result says
the other one exists.**

This is what [003](design/003-determinism-and-the-engineer-in-the-loop.md) calls answer-set
multiplicity, arriving on the *numeric* side before any rules engine is involved — which means
"more than one optimal model is a finding, not a detail" is not an ASP-specific requirement, and
[004](design/004-rule-backed-disciplines.md)'s two-halves split applies here too.

*Fix direction:* nothing statically decidable. A band's edges are known and so are the steps in the
cycle, but whether two fixed points exist depends on the discipline functions, which analysis never
executes. The honest tool is the runtime one: re-solve from seeds either side of each edge and
report divergent destinations — the same shape as `compare_runs`, and a natural companion to the
grounding rung planned in 4.3.

---

## 🟡 Declaring a band changes which variable needs an initial guess

`Discretisation` synthesises one step per band, named `discretise_<band>`. That name takes part in
the **alphabetical ordering** `HybridSolver` uses inside a cyclic block, and `discretise_` sorts
before most verbs an engineer would choose (`size_`, `sum_`, `compute_`).

So the synthetic step usually runs *first*, and its unproduced input — the band's **source**
variable — becomes the one needing a seed. In the demo's mass loop the guess must be
`total_mass_kg`, not the `mass_band` you would reach for by looking at what the first discipline
consumes.

The same trap as the existing alphabetical-ordering entry below, with a new way to trigger it:
adding a band to a working pipeline can change what `run()` requires, without any discipline
changing.

*Mitigation, not a fix:* `analyze()` reports it correctly — it plans over the same
`effective_steps` the solver runs, so the answer is right by construction. Ask it rather than
reasoning it out.

---

## ✅ RESOLVED in 1.15.0 — the symbolic/numeric threshold was invisible

[003](design/003-determinism-and-the-engineer-in-the-loop.md) names this as its sharpest risk.
Going from `mass_kg = 880.0` to `mass(heavy)` requires a threshold; the threshold is a hypothesis;
and in ordinary code it lives in a helper function where nobody reviews it. A perfectly reviewed
set of rules sitting on an unreviewed mapping is not traceable, because the mapping is where the
answer is actually decided.

**Fixed** by `Bands` / `Discretisation`, declared on the `Pipeline`:

```python
Pipeline(discretisation=Discretisation(
    mass_band=Bands("mass_kg", edges=[800], names=["light", "heavy"]),
))
```

`explain()` states every interval and which side an edge value falls on; `validate()` reports
`discretisation-unused` and `discretisation-non-numeric`. Incoherent declarations — two names for
three intervals, descending edges, an unreachable band — raise `DiscretisationError` at
construction, since there is no informed decision to hand back about a typo.

**The design choice worth remembering:** a band is registered as an ordinary `Step` rather than
handled as a special case. A band genuinely *is* a function from one variable to another, so the
existing machinery already fits — `missing-input` reports a source nothing produces (against
`discretise_<name>`, which reads well in the message), `duplicate-output` reports a name a step
also declares, and the solver orders it with no changes at all. **The feature shipped two findings
instead of the four that were planned**, and the instinct to add a parallel set of
discretisation-aware checks would have produced a second planner that drifts.

`closed="left"` vs `closed="right"` is a declared field rather than a convention, because whether
880 is heavy is exactly the kind of default that changes an answer quietly.

---

## 🔴 Step registration order is load-bearing, and getting it wrong is silent

`IterativeSolver` runs steps in registration order. If a step that *consumes* a feedback variable
is registered before the step that *produces* it, the first sweep sees that variable's initial
value only. A step that returns its input unchanged in that situation makes the solver report
**convergence at iteration 1** — on a state that was never evaluated.

No error, no warning. Demonstrated by
`tests/regression/test_agent_as_discipline.py::test_registering_the_model_first_converges_prematurely`,
where a pipeline confidently converges on an architecture that violates its own requirements.

*Fix direction:* this is exactly what static analysis should catch, and it is a listed target for
`validate_pipeline` in Phase 2. `HybridSolver` avoids the trap entirely by deriving order from the
graph — it is specific to hand-ordered `IterativeSolver` use.

---

## ✅ RESOLVED in 1.8.0 — oscillation detection could not be combined with `HybridSolver`

`OscillationAwareConvergenceChecker` is stateful and needs `distance()` called exactly once per
iteration. That only holds when a `target_var` is set; otherwise the residual is a `max()` across
every produced variable ([solvers.py:387](../smartmdao/solvers.py:387)), iterating a `set` in
arbitrary order, and one history cannot separate those interleaved calls.

`HybridSolver` used to build its sub-solvers without forwarding `target_var`, so a pipeline could
have automatic cycle detection *or* oscillation detection, never both.

**Fixed:** `HybridSolver(target_var=...)` now forwards to the cyclic block that produces that
variable. `scripts/hybrid_target_var_demo.py` shows the same pipeline burning all 30 sweeps without
a target and being caught at sweep 4 with one.

**The guard matters more than the feature.** A target is only ever given to the block that
*produces* it. Handing it to any other block would be silently catastrophic: that block's snapshot
has no entry for the name, so the residual becomes `distance(None, None)` — which is `0.0`, i.e.
*converged*. The block would report success on its first sweep without iterating at all. A target
matching no cyclic block is ignored, with a warning.

`validate()` now reports two related mistakes statically: `checker-needs-target-var` (a custom
checker with no target) and `target-var-not-produced`. Deliberately *not* reported: `HybridSolver`
with the standard checker and no target — that is the idiomatic default and flagging it would fire
on almost every correct pipeline.

---

## 🟡 Which variable needs an initial guess depends on step *names*

Inside a cyclic block the shared planner, which `HybridSolver` runs, puts the steps in `sorted`
alphabetical order for deterministic execution ([graph.py:306](../smartmdao/graph.py:306)). The alphabetically-first step therefore runs
first, and whichever of its inputs the cycle has not produced yet must be supplied to `run()` as
an initial guess.

The consequence is surprising: **renaming a step can change which variable you have to seed**, and
getting it wrong is a `KeyError` from deep inside the solve rather than an up-front complaint. In
the demo's mass-growth loop, `size_airframe` sorts before `size_battery`, so the guess must be
`battery_mass_kg` — not the `total_mass_kg` you would reach for by intuition. Widening a cycle
widens the set of variables needing seeds, for the same reason.

*Fix direction:* a natural target for `analyze_pipeline` in Phase 2 — "this cycle needs an initial
guess for X" is exactly the sort of thing static analysis can state up front, and it is derivable
without executing anything.

---

## ✅ RESOLVED in 1.9.0 — `ConvergenceChecker` could not signal "this will never converge"

`distance()` returns a float, and `IterativeSolver` exited only on tolerance or exhausted
iterations. There was no third verdict, so `OscillationAwareConvergenceChecker` *raised* from
inside `distance()` to abort a hopeless run — effective, but a poor fit for a function whose
contract is to return a magnitude, and it destroyed `memory['residual_history']` on the way out.

**Fixed without breaking `distance()`.** Rather than widening the existing protocol (which would
have broken every checker in existence), 1.9.0 adds a *companion* protocol:

```python
@runtime_checkable
class AbandonmentAware(Protocol):
    def abandon_reason(self) -> Optional[str]: ...   # None = keep iterating
```

Structural typing, so implementing one method is enough — no inheritance, and checkers that do not
implement it are asked nothing and behave exactly as before.

Every iterative block now also records a `ConvergenceReport` in
`memory['convergence_reports']`, with `status` of `CONVERGED`, `MAX_ITERATIONS` or `ABANDONED`,
the iteration count, the full residuals, a reason, and the block's step names. Previously a caller
got only `residual_history` and had to re-apply the tolerance by hand to tell convergence from
exhaustion.

**Behaviour change:** `OscillationAwareConvergenceChecker.raise_on_detection` now defaults to
`False`. A detected oscillation stops the solve and returns a report with the trace intact, rather
than raising. Pass `raise_on_detection=True` for the old behaviour — at the cost of losing the
residual history, which is what prompted the change.

---

## ✅ RESOLVED in 1.7.0 / 1.8.0 — heavy runtime dependencies

The base install used to pull `openturns` (needed by exactly one optimizer backend) and
`ipykernel` (needed by nothing at all).

- **1.7.0** moved `openturns` behind an `[openturns]` extra.
- **1.8.0** moved `ipykernel` to the `dev` group. Investigation found the only reference to it
  anywhere in the repository was the `pyproject.toml` line itself, added in the first commit — and
  it pulled **14 packages**, including a debugger, a Jupyter kernel, ZeroMQ and Tornado.

Both are breaking for anyone who relied on the transitive install, hence the version bumps.
`OpenTURNSBackend` still registers without OpenTURNS present and raises an actionable `ImportError`
at the call rather than at import.

---

## 🟡 Duplicate output names silently overwrite

`map_producers` ([graph.py:50](../smartmdao/graph.py:50)) builds a `{name: Step}` dict, so if two
steps declare the same output name the last one registered wins — with no warning. The first
step still executes; its output is simply unreachable, and the dependency graph wires consumers to
the wrong producer.

*Fix direction:* detect collisions in `validate_structure` and raise, or at minimum warn. This is
a genuine candidate for `validate_pipeline` in the MCP tool surface, since it is exactly the class
of mistake an agent generating many steps would make.

---

## 🟡 `@cached` functions are keyword-only

The decorator's wrapper is `def wrapper(**kwargs)` ([cache.py:134](../smartmdao/cache.py:134)).
Inside a pipeline this is invisible, because `StepExecutor` always invokes
`step.fn(**params)`. But calling a cached discipline *directly* with positional arguments fails:

```python
discipline_1(1.0, 1.0, 1.0, 1.0)      # TypeError
discipline_1(z1=1.0, z2=1.0, ...)     # works
```

Surprising when testing a discipline in isolation, or reusing one outside a pipeline.

*Fix direction:* accept `*args`, bind them against the unwrapped signature, and normalise to
kwargs before hashing — the key must stay order-independent.

---

## 🟡 `tarjan_scc` is recursive

`strongconnect` ([graph.py:18](../smartmdao/graph.py:18)) recurses per node, so a pipeline with a
chain longer than Python's recursion limit (~1000 by default) raises `RecursionError`.

Not reachable for any realistic MDO problem, but it is a hard ceiling rather than a soft one, and
a generated pipeline could plausibly get large.

*Fix direction:* iterative Tarjan with an explicit stack, if it ever matters.

---

## ⚪ Generic container types are checked on origin only

`_concrete_classes` ([validation.py:34](../smartmdao/validation.py:34)) reduces `list[int]` to
`list`, so `list[int]` and `list[str]` are indistinguishable to the type checker. Element types
are never inspected.

Deliberate — deep structural checking would be expensive on every call inside a convergence loop.
Documented so nobody reads a passing validation as stronger than it is.

---

## ⚪ `int` does not satisfy `float`

`StandardTypeChecker` is strict by design; `int` is not accepted where `float` is declared. This
surprises people, and the answer is the documented extension point: implement `TypeChecker` and
loosen `_concrete_classes`. Recorded here because it is the single most likely "is this a bug?"
question.

---

## ⚪ `HDF5Backend` cannot store arbitrary Python objects

Scalars, strings, and numpy arrays only — dicts, dataclasses, and custom objects need
`PickleDiskBackend`. Already stated in the class docstring; surfaced here because the failure
happens at write time, deep inside a converging loop, which is an unpleasant place to discover it.

---

## ⚪ A step returning `None` silently stores nothing

`_update_memory` returns early on `None` ([executor.py:91](../smartmdao/executor.py:91)),
which is what makes side-effect-only steps possible. The consequence inside a feedback loop is
that the previous value of that variable persists unchanged — which the convergence checker will
read as "at rest".

Relevant to [002-agent-as-discipline.md](design/002-agent-as-discipline.md), where a model step
needs some way to signal "no feasible answer" and `None` is the obvious-but-wrong choice.
