# 003 — Determinism, traceability, and the engineer in the loop

**Status:** accepted and implemented — Phase 4, 1.15.0–1.19.0 (see [004](004-rule-backed-disciplines.md))
**Date:** 2026-09-13
**Amends:** [002](002-agent-as-discipline.md) — supersedes its production path, not its mechanics

---

## The principle

> We do not have to control everything the engineer does, but we have to give them enough
> information for an informed decision.

This is the governing constraint for everything built on top of SmartMDAO. It is written down here
because it was implicit for three phases, and an implicit principle is one the next contributor
will violate without noticing.

Two things follow, and they are not negotiable for this audience:

- **Determinism and reproducibility.** An engineer has to be able to re-run a study next year and
  get the same answer, and to explain to a design review why the answer is what it is. A
  discipline that returns something different each time is not a discipline; it is a rumour.
- **Traceability.** Every number in a result must trace back to an assumption someone can point
  at. "The model said so" is not traceable.

## The principle already explains what exists

Read retroactively, most of the work so far is this principle applied:

| Built | What it surfaces |
|---|---|
| `validate()` | Assumptions before anything runs — what must be seeded, what is unsatisfiable |
| `analyze()` | The structure the engineer wrote but could not see, e.g. an unintended feedback loop |
| `ConvergenceReport` | What the solve actually did, instead of a residual list to interpret by hand |
| `OscillationDetectedError` → abandonment | *Why* it failed, not just that it did |
| Faithful-vs-idiomatic translation | A choice the engineer makes, not a default chosen quietly |

None of these stop the engineer doing anything. All of them make the consequences visible first.
That is the pattern to keep.

---

## The problem with 002 as a production path

[002](002-agent-as-discipline.md) showed that a language model *can* be a discipline inside an MDA
feedback loop, and the mechanics hold up: `HybridSolver` finds the cycle, structural equality
converges it, oscillation is caught. Those findings stand.

What it could not answer is the question this audience asks first: **how do you reproduce this
run?** 002 lists it under "where it breaks" and leaves it there —

> Nondeterminism in a discipline is a hard sell. Cache aggressively, pin the model version, log
> every call.

Caching and logging make a run *replayable*. They do not make it *reproducible*, and they do not
make it reviewable: the artifact under review is a prompt and a model version, neither of which an
engineer can reason about the way they reason about an equation.

## Decision: ASP-backed disciplines

A language model generates an **Answer Set Programming** program (clingo), once, at authoring
time. That program — not the model — becomes the discipline.

```
authoring time (once, reviewed)      run time (every time, deterministic)
┌──────────────────────────┐         ┌────────────────────────────────┐
│ engineer states intent   │         │ facts derived from pipeline    │
│ agent generates ASP      │  ────▶  │ inputs + the fixed ASP program │
│ ENGINEER REVIEWS RULES   │         │ → clingo → atoms → discipline  │
└──────────────────────────┘         └────────────────────────────────┘
```

The nondeterminism is **relocated, not tolerated**. It moves out of the execution loop and into an
authoring step where a human reviews the result — and the thing under review is a set of logical
rules, which is exactly the kind of artifact engineers already review.

### Why it fits this library specifically

- **ASP output is low-entropy by construction.** 002's hard-won rule — *couple on the decision,
  never on the explanation of it* — is satisfied automatically. A set of atoms has no prose in it
  to perturb structural equality.
- **UNSAT is the honest `INFEASIBLE`.** Phase 1 had to invent a sentinel value because a
  discipline must be total. With ASP, "no feasible architecture exists" is not a convention we
  made up: it is a proof that no model satisfies the constraints.
- **Unsat cores give machine-checkable "why not".** 002's requirements↔feasibility negotiation
  stops being a plausible story and becomes *these constraints are mutually unsatisfiable, here
  they are*. That is traceability an engineer can take into a design review.
- **It targets the real gap.** Gradient-based MDO cannot touch discrete architectural choice.
  ASP is built for exactly that, and SmartMDAO's non-numeric convergence is what lets it couple to
  the continuous side.

### What this removes from scope

**SmartMDAO never calls a language model.** The coding agent generates the program; the library
runs clingo on it. The MCP `sampling` integration proposed in 002 and carried in the roadmap is no
longer needed, and the library takes no model-provider dependency of any kind.

`clingo` is one package with zero transitive dependencies — cheaper than anything removed in 1.7.0
and 1.8.0. It would be an optional extra regardless.

---

## Risks, stated plainly

### 1. The symbolic/numeric bridge is where the hypotheses hide

Going from `mass_kg = 880.0` to `mass(heavy)` requires a threshold. That threshold is a
**hypothesis**, it is usually buried in a helper function, and it silently determines the answer.
A perfectly reviewed ASP program sitting on top of an unreviewed discretisation is not traceable.

*Implication:* the discretisation layer must be first-class, declared and inspectable — reported
by `validate()` alongside everything else — not glue code between the two halves.

### 2. Determinism is not free

An ASP program can have many stable models. Same program, same facts, different answer set, unless
it is pinned with an optimisation statement plus a total tie-break.

*Implication:* a generated program that admits more than one optimal model is a **finding**, not a
detail. Enumerate and report it rather than silently taking the first.

### 3. The authoring step is still untrusted

Determinism lives in execution, not in generation. A model writes syntactically valid, semantically
wrong clingo very easily. The trust problem has been *moved* somewhere a human can review it —
that is the entire win — but it has not been eliminated.

*Implication:* if an engineer rubber-stamps generated rules, they have bought a false sense of
rigour and nothing else. Tooling must make review easy and make *skipping* it visible. This is the
one place the principle bites hardest: we do not prevent the engineer skipping review, but we must
never let them do it by accident.

### 4. Grounding can explode

ASP grounding is worst-case exponential. A generated program can be accidentally intractable in a
way nobody notices until it hangs — the same shape as the execution-cost problem in
[001](001-mcp-connector.md), and it wants the same answer: a budget and an estimate, not a spinner.

---

## What the principle forbids

Concrete rules this record imposes on anything built here:

1. **Never run something expensive without saying what it will cost first.** An estimate with a
   number, not a disclaimer.
2. **Never pick a default that changes the answer without saying so.** Convergence criteria, seeds,
   discretisation thresholds, translation fidelity — all are the engineer's to choose, and all must
   be visible when we choose one for them.
3. **Never present a generated artifact as verified.** Generated code, generated rules and
   generated translations are proposals until the engineer accepts them.
4. **Report what actually happened, including failure.** An abandoned solve returns its trace. A
   non-converged run says so. A finding that was suppressed says it was suppressed.

## Decisions

Settled before any code, so the first file written does not decide them by default.

### The layer ships as an optional `[asp]` extra in this repository

Mirroring `[mcp]` and `[openturns]`, which are working. `clingo` is one package with **zero
transitive dependencies** — cheaper than anything removed in 1.7.0 and 1.8.0 — so the install cost
of keeping it here is negligible, and one repository means one CI and one version.

A companion package was the alternative, and its argument is real: ASP-backed disciplines are a
different concern from MDAO orchestration. It was rejected on cost — two repositories to keep
version-compatible for a library this size, which is the same reasoning that kept the MCP connector
here in [001](001-mcp-connector.md).

### The program lives in a separate, reviewable `.lp` file

**The program is the artifact an engineer reviews.** That is the entire mechanism by which this
direction earns determinism, so it has to be a first-class file: diffable, version-controlled,
legible to someone who does not read Python.

Inline would keep a discipline in one place, and it was rejected precisely because it is easier —
rules buried in a string literal are rules nobody reviews, which is exactly how a hypothesis hides.
Risk 3 below says the authoring step is still untrusted; this decision is what makes review
practical rather than nominal.

### The discretisation layer is declared and validated

Risk 1 below is the sharpest in this document, so the bridge is not glue code. Thresholds like
`mass_kg = 880 → mass(heavy)` become a **declared, inspectable object** that `validate()` reports
on, alongside every other structural finding.

Plain Python with documentation was the cheaper option and was rejected: it leaves the thresholds
in helper functions, which is the status quo we identified as the risk. A perfectly reviewed ASP
program sitting on an unreviewed mapping is not traceable, and the mapping is where the answer is
actually decided.

## Still open

*(Both closed by [004](004-rule-backed-disciplines.md) on 2026-09-20. Left in place because this
record is append-only; read the findings below before acting on either.)*

- ~~**Naming.**~~ "ASP-backed discipline" is provisional and used throughout this document for want
  of a settled term. **Settled: "rule-backed discipline", `RuleDiscipline`.**
- ~~**How answer-set multiplicity is pinned**~~ in practice — an optimisation statement plus a total
  tie-break is the shape, but what enforces it, and what `validate()` says when more than one
  optimal model exists, is undesigned. **Settled: a static `unpinned-program` finding from
  `validate()`, and an `ambiguous-optimum` finding from a new, budgeted rung on the cost ladder.
  `validate()` never grounds.** (Shipped differently in 1.16.0, and recorded in 004's findings: `RuleDiscipline.solve()` raises `AmbiguousProgramError` listing every tied model, and the budget is `budget_seconds` on the discipline. No `ambiguous-optimum` finding exists.)

## Findings added after acceptance

**2026-09-20 — one claim in this record is wrong.** The install-cost argument above states, twice,
that `clingo` has **zero transitive dependencies**. It declares `cffi`, which brings `pycparser`:
three packages, not one. The conclusion is unaffected (three is still negligible, and the
one-repository decision rests on CI and versioning), but the fact was wrong in the direction that
flattered the decision. Detail in [004](004-rule-backed-disciplines.md).

**2026-09-20 — "unsat cores give machine-checkable *why not*" is true but not free.** Verified
against clingo 5.8.2: `SolveHandle.core()` returns *solver literals*, and a naive mapping back to
the assumption symbols named only one of the two constraints in a known two-constraint conflict. The
mechanism holds; turning it into an explanation an engineer can trust is implementation work with a
correctness risk of its own. See [004](004-rule-backed-disciplines.md).

## Related

- [001](001-mcp-connector.md) — the MCP connector, and the execution-cost problem this shares.
- [002](002-agent-as-discipline.md) — the mechanics this builds on; its production path is
  superseded here.
- [../roadmap.md](../roadmap.md) — phasing.
