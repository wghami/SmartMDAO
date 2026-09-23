# 005 — Steps that touch the world outside the pipeline

**Status:** accepted as direction; nothing implemented
**Date:** 2026-09-20
**Relates to:** [003](003-determinism-and-the-engineer-in-the-loop.md) — this is its principle
applied to a failure mode 003 did not anticipate

---

## The problem

A step inside a cyclic block runs **once per sweep**. For a numeric discipline that is the whole
point — iteration is how a feedback loop converges. For a step that writes a file, launches a
subprocess, posts to an API or sends a message, it is something else entirely.

```python
@pipeline.step(outputs=["ticket_id"])
def raise_ticket(design: Architecture) -> str:
    return jira.create_issue(summary=f"Review {design.spar}")   # 30 tickets
```

Nothing in SmartMDAO currently treats re-running a step as anything other than *wasteful*. The
solver has no reason to think one function differs from another, `validate()` has nothing to report,
and the engineer finds out afterwards.

## Why this matters now, and not before

Until recently the answer would have been "do not do that" — SmartMDAO was an MDAO library, the
steps were physics, and physics has no side effects.

That is no longer the intended scope. The engine — name-based wiring, order derived from the graph,
analysis that executes nothing — is not aerospace-specific, and the project intends pipelines like
*read a file → parse it → launch a subprocess → collect the result*. Every step in that sentence
touches the world.

A workflow engine that silently re-runs a subprocess thirty times is not a workflow engine.

## The asymmetry that decides the design

Every other finding in this library describes something **recoverable**. A disconnected discipline
wastes a run. A wrong seed raises a `KeyError`. An unpinned program returns an arbitrary-but-valid
answer. The engineer reads the finding, fixes the model, runs it again.

Side effects are not like that. **You cannot un-send an email, un-launch a job, or un-delete a
file.** The cost of being told afterwards is unbounded, and no amount of reporting recovers it.

This is why the rule here is stricter than anywhere else in the project, and it is worth being
explicit that the exception is deliberate rather than a drift in standards.

---

## Decision 1: side effects are **declared per step**

```python
@pipeline.step(outputs=["report_path"], effects="once")
def write_report(summary: str) -> str:
    Path("out.pdf").write_text(summary)
    return "out.pdf"
```

`effects` becomes part of the `Step`, alongside its inputs and outputs, so `analyze()` and
`validate()` see it **without executing anything** — the invariant the whole analysis layer rests
on. It is the same shape as `outputs=[...]`: a claim the author makes about their own function,
which the library then reasons about.

### The vocabulary

| Value | Meaning |
|---|---|
| *(absent)* | Undeclared. The library assumes the step is pure and says nothing. |
| `effects=True` | This step touches the world. **What should happen in a loop is not stated.** |
| `effects="once"` | Execute on the first sweep of a `run()`; reuse the stored result afterwards. |
| `effects="every-sweep"` | Execute every sweep. You meant it. |

`True` is the important one. It lets an author mark a function honestly without having thought
about loops yet — and that is exactly the case where the library should stop and ask.

### Rejected: inferring it

A step returning `None` is already how a side-effect-only step is expressed, so it looks like a
free signal. It is not: a step can write a file *and* return a float, and most interesting ones do.
Inference would be confidently wrong in the direction that matters — declaring a destructive step
pure.

### Rejected: a pipeline-level registry

`Pipeline(effects={"write_report": "once"})` would put every side effect in one reviewable place,
which is a real benefit and the argument that makes `Discretisation` pipeline-level. It was rejected
because a threshold is a property of *the model*, while a side effect is a property of *the
function* — it travels with the code, survives being moved between pipelines, and is wrong the
moment the two drift apart. Keying by step name would also make a rename silently disarm the
declaration.

---

## Decision 2: refuse, rather than warn, when it would run more than once

Two halves, matching the split [004](004-rule-backed-disciplines.md) established:

**`validate()` reports it statically**, as a warning, with nothing executed:

```
WARNING side-effect-in-cycle
  'raise_ticket' declares side effects and sits inside the loop
  size -> raise_ticket -> sum, so it would run once per sweep.
  Declare effects="once" or effects="every-sweep" to say which you mean.
```

**The solver refuses at run time**, unless the step said which behaviour it wants:

```
RuntimeError: 'raise_ticket' has side effects (effects=True) and sits inside
the loop size -> raise_ticket -> sum. It would run once per sweep.
Declare which you want:
    effects="once"        run on the first sweep, reuse the result
    effects="every-sweep" run every time - you meant it
```

### Why this breaks the project's own pattern, on purpose

[003](003-determinism-and-the-engineer-in-the-loop.md) says: *we do not have to control everything
the engineer does, but we have to give them enough information for an informed decision.* Every
finding in the library so far is a warning for that reason.

An informed decision requires being asked **before** the consequence, and for side effects the
consequence is irreversible. A warning printed alongside thirty created tickets is not information
the engineer can act on; it is a post-mortem. Refusing is what makes the decision available while it
is still a decision.

Note what is *not* being refused: nothing stops `effects="every-sweep"`. The engineer keeps the
choice. They are simply required to make it explicitly, once, in the file.

### The check must be solver-aware

"Would run more than once" is not the same as "is in a cycle":

- `HybridSolver` — a step runs repeatedly only inside a cyclic block.
- `IterativeSolver` — **every** step is swept repeatedly, cyclic or not. A side-effecting step
  anywhere in an `IterativeSolver` pipeline re-runs.
- `DAGSolver` — every step runs exactly once. Nothing to refuse.

This mirrors the finding from Phase 2 that analysis must be solver-aware, and the implementation
must use the same `_ordered_steps` machinery rather than asking whether the graph has a cycle.

---

## Decision 3: `"once"` means once per `run()`

The latch is scoped to a single solve. A second `run()` is a second study and executes the step
again.

Within a run, the step executes on its first invocation and its stored outputs are reused for every
later sweep of that block. **The output is frozen while the rest of the loop keeps iterating**,
which is a real semantic change and the reason `"once"` is opt-in rather than the default: a
silently latched step would converge a loop against a value that stopped responding to it.

---

## Risks

### 1. `optimize()` is a worse case than any loop, and `"once"` does not save you

An optimizer calls `run()` hundreds of times. `"once"` is scoped per run, so a step declared
`"once"` still executes once **per evaluation** — a thousand tickets rather than thirty.

This is the sharpest thing in this record and it is **not** settled here. The shape of an answer is
that `PipelineEvaluator` should refuse any pipeline containing a declared side effect unless told
otherwise, since an optimizer is by definition going to run it many times. Left open deliberately
rather than designed in passing.

### 2. Idempotence is a claim, not a property

`effects="once"` protects against repetition *within* a run. It says nothing about whether the
effect is safe to repeat across runs, retries or a `compare_runs` that executes both files. The
library cannot know; the engineer is asserting.

### 3. `run_pipeline` and `compare_runs` really do execute

Phase 3's subprocess buys a hard kill, typed results and crash isolation — **not** protection.
Running a pipeline through the MCP connector runs its side effects for real, and `compare_runs` runs
them **twice**, once per file. That is worth stating in the tool descriptions, because "compare two
translations" does not sound like "send every notification twice".

### 4. A declaration can be wrong

Nothing verifies that a step marked pure is pure. This layer improves on silence, not on certainty,
and the record should not be read as a guarantee.

---

## What this record does not decide

- **The optimizer case** (risk 1), which needs its own decision.
- **Whether `effects` belongs in `explain()`** — probably yes, since "what does this pipeline touch
  outside itself" is exactly the sort of thing a reviewer wants stated, but it is untested.
- **Whether a side-effecting step should be reported at all when it is safe** (acyclic, `DAGSolver`,
  runs once). Silence is defensible; so is an `info` finding listing what the pipeline touches.

## Related

- [003](003-determinism-and-the-engineer-in-the-loop.md) — the principle this applies, and the one
  place it deliberately departs from it.
- [004](004-rule-backed-disciplines.md) — the static-warning / runtime-enforcement split reused here.
- [../known-issues.md](../known-issues.md) — *a step returning `None` stores nothing*, which is how
  side-effect-only steps are expressed today.

---

## Findings added after Phase 5.2 (2026-09-23)

**Risk 1 and the `compare_runs` question are settled**, both the same way: anything that multiplies
runs refuses a pipeline with *any* declared effect unless the caller passes `allow_effects=True`.
`PipelineEvaluator` refuses at construction; `compare_runs` returns a typed refusal. Every
declaration counts there, `"every-sweep"` included — both it and `"once"` are scoped to a single
`run()`, and the caller's whole purpose is to make many of them.

**`"once"` changes the answer, and this record understated it.** Decision 3 said the latch "freezes
the output while the rest of the loop keeps iterating" and called that a semantic change. Measuring
it made the consequence concrete: on a two-step loop whose fixed point is 20, `"every-sweep"`
settles at 20 and `"once"` settles at **10 — and both report `converged`**.

That is not an edge case. A step inside a cyclic block is in the cycle *because* its output feeds
back, so latching any in-cycle step freezes a coupling the loop depends on. The latch honours the
declaration exactly and the answer is still wrong. `validate()` therefore reports `"once"` as
`side-effect-latched` rather than treating it as a settled answer, and the message names the real
fix: **split the step** — a pure step inside the loop, the side effect on the linear part after it,
where it runs once on the converged result. That is 002's *decide → evaluate completely → act*
shape, arrived at from a different direction.

**The loader executed files it was only meant to read.** Not anticipated here, found while
building the `compare_runs` check. `load_pipeline` imports a file to find its pipeline, and
importing runs top-level code — so a bare `pipeline.run(...)` at module level executed the whole
study every time the file was *analysed*. Measured: two calls to `validate_pipeline` fired a
declared side effect twice, and `run_pipeline` fired it twice per run (once at import, once for
real). That broke the first invariant in [handoff.md](../handoff.md) — *introspection never requires
execution* — for a very common shape of script, and it predates this record; side effects only
made it visible.

Fixed by suspending `Pipeline.run()` on the loading thread for the duration of the import. A
module that only *calls* `run()` at top level loads normally; one that goes on to *use* the result
is told plainly that nothing executed and to guard the run with `if __name__ == "__main__":`,
rather than being handed an empty result it would misread as an answer.

**`effects` in `explain()` is settled** (1.22.0): it lists what a pipeline touches, because a
reviewer wants it stated and it costs nothing to say. **Reporting safe effects is settled too:**
an effect that runs once — outside any loop, under `DAGSolver` — gets no finding. `explain()` is
where it is visible; a finding would fire on every correct pipeline that writes its results out.
