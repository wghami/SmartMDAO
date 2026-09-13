# 002 — An agent as an MDA discipline

**Status:** mechanics proven with a stubbed model; no live model call yet
**Date:** 2026-09-13 · findings added after Phase 1

---

## The observation

`StandardConvergenceChecker` ([solvers.py:37](../../smartmdao/solvers.py:37)) falls back to
structural equality for non-numeric coupling variables. Its docstring already names the use case:

> ...an agent-authored MDA negotiating a shared plan or a resolved set of dependencies...

Nothing in the repository demonstrates it. The README makes the same claim in prose. This document
works out what it would actually mean, what it is good for, and where it breaks.

The claim is narrow and specific: **a language model can be a discipline inside an MDA feedback
loop, and the loop can converge.** OpenMDAO and GEMSEO cannot express this, because they converge
by driving a numeric residual to zero and there is no derivative of a design decision.

## Mechanics

A step is any callable, so a step can call a model and return a typed value:

```python
@pipeline.step(outputs=["architecture"])
@cached(history)
def propose_architecture(requirements: frozenset, violations: frozenset) -> Architecture:
    return ask_model(requirements, violations)      # frozen dataclass

@pipeline.step(outputs=["mass", "violations"])
def size_and_check(architecture: Architecture) -> tuple[float, frozenset]:
    ...                                             # pure numerics
```

`architecture → violations → architecture` is a cycle. `HybridSolver` finds it with Tarjan,
isolates it as an SCC, and hands it to a nested `IterativeSolver`. Convergence is reached when the
proposed architecture stops changing between sweeps.

Note what this inverts. Normally the agent is the outer loop and calls tools. Here the **solver**
is the outer loop and the model is a subroutine.

## Why this is not just a while-loop with a model in it

That is the obvious objection and it deserves a direct answer. Four properties come from the
solver, not from the loop:

**1. Termination is external.** This is the one that matters. Agent loops today end when the model
declares itself finished — the model grading its own homework, which is exactly where agent
reliability is weakest. Here the model contributes a value and *the solver* decides whether the
system is at rest, against a criterion the model does not control and cannot see.

**2. Only the cycle iterates.** `HybridSolver` runs non-cyclic SCCs exactly once and iterates only
the strongly connected block ([solvers.py:232](../../smartmdao/solvers.py:232)). A hand-rolled
loop re-runs everything each pass; when "everything" includes model calls, that is money. The
decomposition is automatic, so nobody has to work out by hand which subset genuinely needs to
iterate.

**3. Contributions are typed and bounded.** `validate_structure` checks the step's declared return
type against what consumers expect *before anything runs*, and `runtime_type_checks` enforces it
per call. The model is a discipline with a contract, not a free agent — it cannot return prose
where a dataclass is required.

**4. It is auditable for free.** `residual_history` lands in the result dict
([solvers.py:144](../../smartmdao/solvers.py:144)) and `HistoryBackend`
([cache.py:44](../../smartmdao/cache.py:44)) already keeps a chronological list of every value each
function produced. The full record of what the model proposed at each iteration exists with no
additional machinery.

## What it is for

Ranked by how much the numeric coupling actually earns its place.

### Discrete architectural choice — the strongest case

MDO has a well-known hole. Gradient-based optimization handles continuous sizing beautifully and
cannot touch **discrete architecture**: how many engines, wing-mounted or fuselage-mounted, which
material, which topology. In practice that part is decided by a human in a meeting, or by
brute-force enumeration over a combinatorial space.

Put the model in that role. It proposes an architecture; numeric disciplines size it and return
constraint violations; those feed back; it revises. The numerics **ground** it — it cannot
hallucinate a mass budget, because `scipy` computes the mass — and it covers the combinatorial
part gradients cannot reach. That is a real gap in practice, not a restatement of something that
already works.

### Requirements ↔ feasibility negotiation

Requirements flow down, sizing flows back, some turn out infeasible. Today that is a meeting. As a
loop: the model holds the requirement set, numeric disciplines size against it, infeasibility
feeds back, it reallocates margin. Converges on a self-consistent requirement set — and the
`HistoryBackend` trace *is* the traceable derivation that systems engineers currently assemble by
hand.

### Optimization self-repair

An `optimize()` run fails: bad scaling, a flipped constraint sign, bounds too tight.
`OptimizationResult` already carries `success`, `message`, and `state`. Feed those to a model step
that proposes a reformulation, re-run, feed back. The loop converges when the *problem statement*
stops changing. Attractive because it improves the formulation rather than the design.

### Weak cases, stated plainly

- **Any loop with no numeric discipline in it.** You are using SmartMDAO as a DAG runner and
  purpose-built agent frameworks have more ecosystem. The value here comes from *coupling* symbolic
  and numeric disciplines in one convergence criterion; without the numerics there is no argument.
- **Free-text coupling variables.** See below — these essentially never converge.

## Where it breaks

**Structural equality is a brutal criterion.** The distance function returns `0.0` or `inf` and
nothing between. One reworded sentence means "still moving". The coupling variable must therefore
be **low-entropy**: a frozenset of discrete choices, an enum, a small frozen dataclass of
decisions. Never prose. Normalise before returning — sort collections, round floats, canonicalise
— and run at temperature 0. Design rule: *couple on the decision, not on the explanation of it.*

**Oscillation is undetected.** A model that flip-flops A→B→A→B never satisfies structural equality
and will burn every one of `max_iterations`, each costing model calls. Nothing in the current
solver notices. The fix fits the existing architecture cleanly: `ConvergenceChecker` is a
`Protocol` and the instance persists across iterations, so a stateful checker that retains a value
history and reports a detected 2-cycle is a small addition. **This is a prerequisite, not a
nice-to-have** — see Phase 1 in [../roadmap.md](../roadmap.md).

**Mixed convergence is stricter than it looks.** With no `target_var`, the residual is a `max()`
across *every* produced variable ([solvers.py:161](../../smartmdao/solvers.py:161)). The whole
system is hostage to the model's output being byte-identical between sweeps. `target_var` is the
necessary escape hatch: judge convergence on the architectural variable and let the numerics settle
underneath it.

**Cost is multiplicative.** MDA iterations × model calls per iteration — and wrapping the whole
thing in `optimize()` multiplies again by evaluation count. A model inside an optimizer's inner
loop is not affordable. The workable topology is the inverse: **model in the outer architectural
loop, `optimize()` inner.** The model proposes an architecture, the optimizer sizes it optimally,
the result feeds back. `@cached` matters a great deal here, since a converging system revisits
near-identical states.

**Reproducibility.** MDO practitioners care about this more than most software audiences, and a
nondeterministic discipline is a hard sell. Cache aggressively, pin the model version, log every
call, and treat the `HistoryBackend` record as part of the result rather than as debug output.

---

# Phase 1 findings

Evidence comes from [`scripts/agent_as_discipline_demo.py`](../../scripts/agent_as_discipline_demo.py)
— a discrete propulsion-architecture problem with a deterministic stubbed `ask_model()` — and its
regression tests. The convergence mechanics work. Three things we did not anticipate turned out to
matter more than the things we did.

## The three open questions, answered

**1. Is structural equality on a frozen dataclass good enough?** Yes, with no custom
normalisation needed. Case 1 converges to `Architecture(n_motors=2, battery="li-s")` in two
sweeps; Case 2 converges in three. But this is a weaker result than it looks: the coupling
variable was *designed* low-entropy — two discrete fields and a boolean. It confirms the machinery,
not that real model output will behave. The normalisation question moves to Phase 3, where a live
model replaces the stub.

One incidental discovery: `list.__eq__` compares elements by identity before calling `__eq__`, so
a recurring value is recognised even when its type raises on comparison. Pinned by test.

**2. Is "converged" the right target?** Not by itself — and the interesting answer is that
*all three* outcomes are informative:

| Outcome | Meaning |
|---|---|
| Converged on a feasible architecture | The design closes. |
| Converged on the infeasible sentinel | **No architecture satisfies these requirements** — a real MDA result, arrived at normally. |
| Oscillation detected | The most diagnostic of the three: the cycle *names the tradeoff the model cannot resolve*. |

Case 3 returns `[4 motors, 2 motors]` — which says, precisely, that range and mass are in
conflict and the model has no move that satisfies both. That is more actionable than either
converged answer.

**3. How does a step retract?** With an explicit sentinel value. Returning `None` is actively
dangerous: `_update_memory` stores nothing ([executor.py:91](../../smartmdao/executor.py:91)), the
previous architecture stays in memory unchanged, and the checker reads that as **converged** — a
wrong answer presented as a correct one. The rule: *a discipline must be total.* "No answer" is a
value, not the absence of one. The demo uses a module-level `INFEASIBLE` instance.

## Three findings we did not expect

### The `ConvergenceChecker` protocol cannot say "give up" — *fixed in 1.9.0*

`distance()` returns a float, and `IterativeSolver` has exactly two exits: distance below
tolerance, or `max_iterations` exhausted. There is no way to report *"this will never converge,
stop now"* — which is precisely what oscillation detection needs to communicate.

`OscillationAwareConvergenceChecker` raises `OscillationDetectedError` from inside `distance()`.
That works and it stops the loop, but a distance function that raises is a poor fit for the
protocol's shape. The honest description is a workaround. A cleaner protocol would let a checker
return a verdict — *moving / at rest / hopeless* — rather than a float that has to smuggle a third
state out through the exception system.

Cost of the workaround: the run raises instead of returning, so `memory['residual_history']` is
lost. The error carries the cycle, which is the more useful artifact, but the trace is gone.

**Resolved in 1.9.0, and more cheaply than expected.** The prediction above — "a cleaner protocol
would let a checker return a verdict rather than a float" — implied a breaking change to
`ConvergenceChecker`. It turned out not to be necessary. A *companion* protocol,
`AbandonmentAware`, adds `abandon_reason()` alongside the untouched `distance()`; structural
typing means implementing one method is enough, and existing checkers are unaffected.

The lesson worth keeping: the instinct to widen an existing interface was wrong. Adding a second,
optional one did the same job with nobody's code broken.

Every block now returns a `ConvergenceReport` — `CONVERGED`, `MAX_ITERATIONS` or `ABANDONED`, with
the reason and the full residuals. An abandoned run keeps its own record of what it tried, which
matters most precisely here: when a model is a discipline, *how* it failed to settle is the
interesting result. See `scripts/convergence_report_demo.py`.

### Oscillation detection is incompatible with `HybridSolver` — *fixed in 1.8.0*

A stateful checker needs `distance()` called once per iteration. That only happens with
`IterativeSolver(target_var=...)`; otherwise the residual is a `max()` across every produced
variable ([solvers.py:161](../../smartmdao/solvers.py:161)), iterating a `set` in arbitrary order,
and a single history cannot tell those interleaved calls apart.

`HybridSolver` constructs its sub-solvers without forwarding `target_var`
([solvers.py:244](../../smartmdao/solvers.py:244)). So the automatic SCC detection that makes
SmartMDAO pleasant — "just add steps, the cycle is found for you" — is **unavailable** to
agent-as-discipline pipelines today. The demo drives `IterativeSolver` directly and declares its
step order by hand.

This undercuts one of the four advantages claimed above. "Only the cycle iterates" is still true
of `HybridSolver` in general, but a pipeline that needs oscillation detection cannot currently
have both.

**Resolved twice over.** First in practice: see [Topologies](#topologies-where-the-model-sits)
below — moving the model out of the cycle avoids the conflict entirely and is the better default
anyway. Then in code, in **1.8.0**: `HybridSolver(target_var=...)` forwards to the cyclic block
producing that variable, so topology A can now have both automatic cycle detection and oscillation
detection. `scripts/hybrid_target_var_demo.py` demonstrates it.

The fix carries a guard worth knowing about. A target is only given to the block that *produces*
it, because handing it to another block makes the residual `distance(None, None)` — which is
`0.0`, i.e. *converged*, on the first sweep, without iterating. The fix for one silent failure
mode had to avoid introducing a worse one.

Topology B+D remains the recommended default on cost grounds; this removes the correctness
argument against topology A, not the economic one.

### Step registration order is load-bearing, and failing it is silent

`IterativeSolver` runs steps in registration order. Register the model discipline *before* the
numeric one and sweep 1 hands it an empty violation set; it returns the architecture unchanged;
the solver sees no movement and reports **convergence at iteration 1** on an architecture that was
never evaluated and in fact violates its requirements.

No error, no warning — a confidently wrong answer. Pinned by
`test_registering_the_model_first_converges_prematurely`.

This is the single strongest argument for [001](001-mcp-connector.md)'s framing: it is exactly
the class of mistake an agent assembling a pipeline would make, and exactly the class of mistake
static analysis can catch. It belongs in `validate_pipeline`.

---

# Topologies: where the model sits

The Phase 1 implementation puts the model *inside* the cycle. That is one option of several, and
it turns out not to be the best default. Naming them makes the tradeoff visible.

| | Model position | Calls per run | Feedback? | Solver |
|---|---|---|---|---|
| **A** | Inside the cyclic block | one **per sweep** | yes, per sweep | `IterativeSolver` + `target_var` |
| **B** | Linear part, upstream of the cycle | **one** | no | `HybridSolver`, fully automatic |
| **C** | Linear part, downstream of the cycle | **one** | n/a — interprets results | `HybridSolver`, fully automatic |
| **B+D** | Linear part, plus an outer Python loop | one **per outer iteration** | yes, per full MDA | `HybridSolver` + a `for` loop |

## B+D is the better default

Case 4 of the demo runs it: the model picks a battery chemistry, `HybridSolver` discovers the
battery↔mass snowball cycle on its own and converges it numerically under that fixed architecture,
and an outer Python loop feeds violations back. It converges in **two outer iterations — two model
calls — against 14 numeric sweeps.**

Crucially, B+D sidesteps **all three** of the limitations Phase 1 surfaced:

- **No "give up" problem.** Termination lives in an ordinary `for` loop, so the
  `ConvergenceChecker` protocol's missing third verdict never arises. Repeat detection is three
  lines of Python instead of a stateful checker.
- **No `HybridSolver` conflict.** Nothing needs `target_var`, so automatic SCC detection works
  fully — the thing that makes SmartMDAO pleasant to use.
- **No step-order trap.** Execution order inside the pipeline is derived from the graph, not from
  registration order.

It is also the topology that matches how the use cases in this document actually work. Discrete
architecture selection, requirements negotiation and optimization self-repair are all
*decide → evaluate completely → revise* — which is B+D, not A.

## The naming mechanic that makes it work

The model step must consume a parameter named **differently** from the variable the numeric block
produces — `prior_violations`, not `violations`. Match the names and `build_dependency_graph`
grows an edge, `HybridSolver` pulls the model into the SCC, and it silently becomes topology A,
called once per sweep. The cost argument evaporates with no error to warn you.

Pinned by `test_naming_the_input_after_the_output_collapses_it_into_the_cycle`.

## So when is A right?

When the model's contribution is **genuinely coupled** — it must react to intermediate MDA state
rather than to a converged result. That is a narrower case than this document originally implied,
and every use case listed above is better served by B+D.

Topology A is not wasted: `OscillationAwareConvergenceChecker` is what makes it survivable at all,
and the same oscillation failure mode reappears in B+D's outer loop (where it is trivially handled).
But **A should be the exception, not the starting point.** This document previously implied
otherwise; that was wrong.

---

## What remains unproven

Everything about *real* model behaviour. The stub is deterministic and rule-based; it converges
because it was written to. Open until Phase 3 swaps in a live call:

- Whether real output normalises to a stable low-entropy value often enough to converge at all.
- Whether temperature 0 is sufficient, or whether convergence needs structural canonicalisation on
  top.
- Whether `max_period=4` is the right detection window for a real model's failure modes.
- What the actual call cost of a converging run looks like, and how much `@cached` recovers.
