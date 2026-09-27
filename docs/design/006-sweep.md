# 006 — Campaigns: many points, quoted first, resumable, never silent about failures

**Status:** proposed — not implemented; roadmap 6.7 builds it after approval
**Date:** 2026-09-27
**Relates to:** [003](003-determinism-and-the-engineer-in-the-loop.md) (a number before a
commitment; report, do not guess); [007](007-project-interpreter.md) (the worker, and the
persistent worker it deferred); [009](009-json-boundary.md) (`inputs_from`); request R3 and the cost
note in [requests/2026-09-27-paper-repro-f011.md](../requests/2026-09-27-paper-repro-f011.md)

---

## The problem, in the requester's numbers

`run_pipeline` runs one point. A reproduction campaign is many, and the requester measured theirs
by hand, because no smoke run could pass the MCP before 1.28.0:

| What | Cost (one core, ±30 %) |
|---|---|
| Constellation + ISL, one pass | Kepler 0.02–0.05 s; Starlink 0.13–0.17 s; worst stressed case 11.8 s |
| Routing, one realization | Kepler 64–86 ms; Starlink about 1.1 s |
| Routing campaign: 100 realizations × 2 constellations × 2 scenarios | about 4 min serial |
| Downlink allocation, one slot | 0.5–2.4 s (the solver dominates) |
| Fig. 9 sweep, 74 runs | about 4–5 h serial |
| Stress plan, 45 entries | each multiplies one of the above |

What they had to write by hand, and what a driver must therefore provide:

- points built by the project's own code (`baseline.inputs(scenario)` plus overrides), never
  hand-copied JSON;
- parallelism over independent realizations and slots;
- a resumable store per point;
- seeds as inputs;
- failure records per point, since their solver sometimes mis-reports infeasibility and the SCA
  alternative can answer "inaccurate";
- a cost quote from one point before the full run.

Without a driver, a campaign is a Python loop around `pipeline.run`. It loses the wall clock, the
crash isolation, the typed result and the quote: everything `run_pipeline` gives a single point.

## What was measured before designing

**A process per point is not affordable.** On this machine, a small pipeline point costs **0.71 s**
through `run_pipeline`, which starts a fresh interpreter each time, and **0.66 ms** in a process that
has already loaded the file: about 1,000×. At the requester's 0.02–0.05 s per routing pass, one
process per point would make a campaign 15–35 times slower than its work. [007](007-project-interpreter.md)
deferred a persistent worker "until a real session shows the spawn cost dominating". A campaign
is that case.

**Hours do not fit in a tool call.** A 4–5 hour campaign cannot live inside one MCP call: clients
time out, and sessions end. The campaign has to run on its own, and a later call must be able to
ask how it is going.

---

## Proposal

### 1. What a campaign is

A campaign is a **pipeline file**, a list of **points**, and the **outputs** to record.

```python
from smartmdao.sweep import Campaign

campaign = Campaign(
    "routing/pipeline.py",
    store="campaigns/fig9",                      # a directory; required, never a hidden default
    inputs_from="leo_multicast.baseline:inputs",  # how each point's inputs are built (009)
    grid={"constellation": ["kepler", "starlink"], "scenario": ["space", "ground"]},
    seeds=range(100), seed_input="seed",
    outputs=["latency_ms", "throughput_mbps"],
)
```

- **Points** come from a `grid` (the Cartesian product of named values), an explicit `points`
  list, or both. A point is a small JSON object, so it can be named in a tool call.
- **How a point becomes inputs:** the point's values go to `inputs_from` as keyword arguments, and
  the function builds the full input set inside the worker. Arrays and tuples never cross JSON. A
  point's `inputs` field, if present, overrides single values afterwards, as `inputs` does in
  `run_pipeline`.
- **Seeds are ordinary inputs.** `seeds=range(100)` crosses every point with 100 seeds, passed as
  the input named `seed_input`. SmartMDAO never seeds a global random state. A campaign whose
  `seed_input` no step reads is refused, because its "realizations" would all be one run.
- **Only what is recorded is computed.** Each worker runs `pipeline.upstream(*outputs)` (1.29.0),
  so a campaign that reports latency never runs the downlink allocation.

### 2. The quote comes first

`campaign.quote()`, or `sweep_pipeline(..., start=False)` over MCP, the default:

1. expands the design and reports its size: "400 points";
2. runs **one** point in the project's environment, under a wall clock, and measures it;
3. returns the estimate, "one point 0.08 s; 400 points on 3 workers ≈ 11 s plus 3 s startup",
   with the point's outputs, so a wrong model is caught before hours are spent on it.

**Starting needs a budget.** `campaign.run(budget_seconds=...)`, or `start=True` with
`budget_seconds`, is required, and has no default. A campaign past its budget stops handing out
points and is recorded as `stopped: budget`. It stays resumable. An unbounded campaign is never
what happens by accident, the same rule as the `smoke` rung.

### 3. How it runs

- **A pool of long-lived workers**, in the project's environment, resolved as in 007.
  - Each worker loads the pipeline once, then runs points one after another. It is a child
    process speaking JSON lines, so a point's result reaches the store as soon as it is known.
  - The default pool size is the number of CPUs minus one, capped by the number of points, and
    reported. It can be set.
- **Isolation per process, not per point.** A worker that crashes or overruns a point's timeout
  is killed. That point is recorded as `crashed` or `timed_out`, a fresh worker takes its place,
  and every other point carries on.
  - A worker is also recycled after a fixed number of points (proposed: 500), so a slow leak in a
    discipline cannot grow for hours.
- **The per-point timeout** defaults to 20 times the quoted point, and at least 60 s. It is
  reported, and can be set. It changes no answer, only which points are marked failed.
- **Parallelism is over points.** The requester's independent realizations and slots become
  parallel when they are points: a slot index in the grid, or a seed. Parallelism *inside* one
  point stays the project's business.
- **Side effects:** a campaign multiplies runs, so a pipeline declaring any effect is refused
  unless `allow_effects=True`, as with `compare_runs` and the optimizer
  ([005](005-side-effecting-steps.md)).

### 4. The store: resumable, and never mixing two models

The store is a directory:

| File | Holds |
|---|---|
| `campaign.json` | the design, the interpreter, the SmartMDAO version, the model hash, the start time |
| `points.jsonl` | one line per finished point, appended as it finishes |

- **A point's key** is a hash of:
  - the **model hash**: the entry file plus every user module the load imported (the loader
    already identifies them to forget them, 1.24.0), plus the `inputs_from` module;
  - the **SmartMDAO version** the worker reports;
  - the point, its seed and the recorded outputs.
- **Resuming** means running the same campaign again. Points whose key is already in the store with
  status `ok` or `not_converged` are skipped. After an edit to the model, nothing matches, so
  everything is recomputed. The status report says how many stored points were ignored as
  belonging to another model version, so two versions are never averaged together.
- **Append-only JSON lines** survive a kill mid-write: a torn last line is ignored and that point
  runs again.
- **Recorded outputs must be JSON**: numbers, strings, and lists up to a cap. An array output
  larger than the cap fails that point with "reduce it in a step". This is one of the open
  questions below.

### 5. Failures are recorded, never dropped

Every point ends in exactly one status:

| Status | Meaning |
|---|---|
| `ok` | ran, every loop converged |
| `not_converged` | ran, but a loop ended `MAX_ITERATIONS` or `ABANDONED`; outputs kept, reason recorded |
| `error` | a step raised; type, message and a traceback tail kept |
| `timed_out` / `crashed` | killed, or died, as above |

A solver that mis-reports infeasibility is the project's to detect. The recommended pattern is a
discipline that raises, or returns an explicit sentinel ([002](002-agent-as-discipline.md): a
discipline must be total). A raised error becomes an `error` point with the message kept.
`retry_failed=True` on a resume runs the failed points again.

### 6. Aggregates say what they left out

`campaign.aggregate()` groups points that differ only by seed. For each numeric output it reports
`n`, the mean, the standard deviation and a 95 % confidence half-width (Student's t, from SciPy,
which is already a dependency). **Failed and `not_converged` points are excluded from the numbers,
and counted beside them**: `n_ok = 97, excluded = 3 (2 error, 1 not_converged)`. An average that
quietly dropped its failures would present itself as a complete measurement.

Results are keyed, so the aggregate is the same whatever order the workers finished in.

### 7. Over MCP

| Tool | Does |
|---|---|
| `sweep_pipeline(path, store, …, start=False)` | the quote; with `start=True` and `budget_seconds`, starts the campaign **detached** and returns at once |
| `sweep_status(store)` | progress, the ETA, failures so far (the first few in full), aggregates so far |
| `sweep_stop(store)` | stops handing out points; running points finish; resumable |

The detached campaign is a coordinator process that outlives the tool call. If the session ends,
it carries on. If it is killed, a new `start` resumes from the store.

### 8. `compare_runs` moves onto the worker in the same work

Both need a per-point run in the project's environment and an effects check done where the file
can be imported. The worker gains an op that reports a loaded file's declared effects and source
literals. `compare_runs` then resolves and runs each side in its own environment, and the last
tool using the server's environment is gone.

---

## Alternatives rejected

- **One process per point.** It is the simplest isolation, and measured at about 1,000× the cost
  of a small point.
- **Threads in one process.** The GIL serialises pure-Python disciplines, and one crashing
  discipline takes every point with it.
- **`multiprocessing` with pickled pipelines.** Pipelines built in scripts, closures and lambdas
  do not pickle reliably, and the fork/spawn difference across platforms makes behaviour
  OS-dependent. Workers load the *file*, as every other tool does.
- **A campaign as one long tool call.** Clients time out and sessions end. Detached, with status
  and stop, is the only shape that fits a 5-hour run.
- **Averaging whatever finished.** It is simpler, and it misreports every campaign that had
  failures.

## Open questions for the maintainer

1. **Detached campaigns over MCP** (start, status, stop), as proposed, or blocking only?
2. **A budget required to start**, with no default, as proposed, or a default budget?
3. **`not_converged` points excluded from aggregates and counted**, as proposed, or included with
   a flag?
4. **Recorded outputs as JSON**, with arrays capped (proposed), or arrays stored in HDF5 through
   `h5py`, already a dependency?
5. **Scope of the Python API:** file-based `Campaign` only (proposed), or also in-process
   `Pipeline` objects, run sequentially, without isolation?

## Exit criterion for 6.7

The requester's routing campaign shape is reproduced end to end:
- a grid × 100 seeds, with points built by `inputs_from`;
- a quote from one point;
- started detached in the project's own environment, in parallel;
- killed midway and resumed without recomputing a finished point;
- a deliberately failing point recorded, not averaged;
- aggregates with confidence intervals that state what they excluded.

`compare_runs` runs in each side's own environment.
