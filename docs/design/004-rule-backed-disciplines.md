# 004 — Rule-backed disciplines: naming, and pinning multiplicity

**Status:** accepted as direction; nothing implemented
**Date:** 2026-09-20
**Amends:** [003](003-determinism-and-the-engineer-in-the-loop.md) — settles its two open questions,
and corrects one factual claim in it

---

## Why this record exists

[003](003-determinism-and-the-engineer-in-the-loop.md) set the direction and deliberately left two
questions open, on the grounds that they should be settled *before the first file is written, so
the first file does not decide them by default*. This record settles them.

Both were listed in [handoff.md](../handoff.md) under *Open decisions* — judgement calls left to
the maintainer rather than bugs. Nothing in Phase 4 should start until they are closed, which is
what makes this a docs-only step rather than the beginning of the implementation.

---

## Decision 1: the concept is a **rule-backed discipline**

`RuleDiscipline` in the API; "rule-backed discipline" in prose. 003 used "ASP-backed discipline"
throughout, marked provisional.

```python
from smartmdao import RuleDiscipline

sizing = RuleDiscipline(
    program="wing_architecture.lp",
    discretisation=bands,
)
```

**Name the thing the engineer reviews, not the engine that runs it.** The entire argument of 003 is
that determinism is bought by relocating the untrusted step into an authoring step where a human
reviews *a set of logical rules*. The name should say that. An engineer defending a result in a
design review can say "these are the rules"; "this is the answer set" invites a question about
answer set semantics that has nothing to do with the wing.

`ASP` and `clingo` stay where they are implementation facts: the `[asp]` extra, the architecture
doc, and the error message you get when the extra is missing. That mirrors how `[openturns]` is
named after the dependency while the user-facing concept is an optimizer backend.

**Rejected: `AspDiscipline`.** Honest and searchable, and it is what the literature calls it. It
loses on audience — this library's readers are MDO engineers, not logic programmers, and the term
of art is a barrier exactly at the moment we are asking them to review something unfamiliar.

**Rejected: `DeclarativeDiscipline`.** Broadest, and leaves room for a non-ASP solver later. It was
rejected for vagueness: every discipline in this library is declarative in some sense (you declare
a function and the graph is derived), so the name draws no line. Keeping room for a second backend
is a real benefit, but a speculative one, and renaming later is cheap while a vague name is
permanent.

---

## Decision 2: multiplicity is **static warning, runtime finding**

003, risk 2: an ASP program can have many stable models, so the same program and the same facts can
yield a different answer set unless pinned with an optimisation statement plus a total tie-break.
It requires that a program admitting more than one optimal model is *a finding, not a detail*.

The question left open was what enforces it, and what `validate()` says. The tension is that
**invariant 1 says introspection never requires execution**, and multiplicity is only truly knowable
by solving. Splitting it in two keeps both halves honest:

| Layer | Cost | What it can say |
|---|---|---|
| `validate()` — static, parses the `.lp` | free | `unpinned-program`: no optimisation statement, or no *well-formed* total tie-break. A program that *can* be ambiguous. See the finding below — presence of a tie-break is not the property to check. |
| A new rung on the cost ladder — grounds and solves | budgeted, estimated first | `ambiguous-optimum`: this program, on these facts, **actually has** more than one optimal model. Here they are. |

`validate()` never grounds. That is not pedantry about the invariant: 003's risk 4 is that grounding
is worst-case exponential, so a `validate()` that grounds is a `validate()` that can hang — and the
whole value of the analysis layer is that it is free and therefore always run.

The runtime half is **not a new cost story**. Phase 3 already built the ladder: a cheap rung that
measures unit cost, an estimate quoted before anything expensive, a mandatory wall clock, a
subprocess that survives a crash. Grounding is another rung on it, and reusing that machinery is
most of the reason this decision is cheap.

### Verified: the enumeration works

Checked against `clingo` 5.8.2 rather than asserted. `--opt-mode=optN` with `models=0`, filtering on
`model.optimality_proven`, enumerates every *proven optimal* model — two tied answer sets at equal
cost are both returned:

```
optimal models: [(['choice(a)', ...], [3]), (['choice(b)', ...], [3])]
```

So "enumerate and report rather than silently taking the first" is implementable as stated. Taking
the first would have returned `choice(a)` with nothing anywhere indicating that `choice(b)` was
equally good — which is the exact failure 003 forbids.

### Verified, and it moves the static half: "has a tie-break" is not a check

Writing a worked `.lp` to test the decision above produced the sharpest finding in this record.

A total tie-break was written the obvious way — a second `#minimize` at lower priority:

```prolog
#minimize { C@1,A : selected(A), complexity(A,C) }.   % the real objective
#minimize { 1@0,A : selected(A) }.                    % "tie-break"
```

It is not a tie-break. The weight is the constant `1` for every candidate, so it separates nothing.
Two architectures tied on complexity still came back as **two proven-optimal models**. The working
form ranks over a *distinct* value per candidate:

```prolog
rank(all_electric,1). rank(parallel_hybrid,2). rank(series_hybrid,3). rank(turboprop,4).
#minimize { R@0,A : selected(A), rank(A,R) }.
```

Measured: two optimal models become one.

**This changes what the static half of decision 2 has to do.** `unpinned-program` as first specified
— "no optimisation statement, or no tie-break" — would have passed the broken program above, which
is genuinely ambiguous. Presence is not the property. The check must establish that the tie-break
**ranks over a distinct value per candidate**, and a generated program is exactly where the constant
form will appear, because it is the shape that looks right.

Two consequences, both carried into 4.3:

1. The static check is harder than "grep for `#minimize`", and it has a **false-negative mode that
   flatters the program**. That is the direction findings must never fail in.
2. It raises the value of the runtime rung. Static analysis can establish that a tie-break is
   *well-formed*; only solving establishes that the optimum is *unique on these facts*. The two
   halves were a convenience when this record was drafted; they are now load-bearing.

The decision itself stands — this sharpens the specification rather than changing the shape.

### Verified, and less comfortable: unsat cores do not map back for free

003 claims unsat cores give a machine-checkable "why not", and that this is traceability an engineer
can take into a design review. The mechanism exists — `SolveHandle.core()` after solving under
assumptions — but it returns **solver literals**, and the sign convention does not map naively onto
the assumption symbols that produced them.

On a three-assumption program whose real conflict is `{assume(1), assume(2)}`, the raw core came
back as `[-1, -2, -3, 1]`, and a first-cut positive-literal lookup named **one** of the two
conflicting assumptions. An explanation that is missing half the conflict is worse than no
explanation, because it points the engineer at the wrong constraint.

This does not change the direction. It does mean **"surface the unsat core" is implementation work
with a correctness risk, not a free property of using clingo** — scheduled into 4.3 and to be
demonstrated against a known-conflicting program rather than trusted.

---

## Correction to 003: clingo is not dependency-free

003 states, twice, that `clingo` is "one package with **zero transitive dependencies**" and uses it
to justify keeping the layer in this repository rather than splitting it out.

Measured: `clingo` declares `cffi`, which brings `pycparser`. **Three packages, not one.**

The argument survives — three packages against the 14 that `ipykernel` pulled in before 1.8.0 is
still a negligible install cost for an optional extra, and the in-repo decision stands on the
one-CI-one-version reasoning from [001](001-mcp-connector.md) anyway. But the stated fact was wrong,
and it was wrong in the direction that flattered the decision. Recorded here rather than edited into
003, per the convention that design records are amended by appending findings, not by rewriting
history.

Worth noting how cheap this was to catch: one `importlib.metadata.requires('clingo')` call. The
claim had been sitting in an accepted design record for a week.

---

## What this unblocks

With both questions closed, Phase 4 has an order. See [roadmap.md](../roadmap.md) for the phasing;
the shape of it is that **the discretisation layer comes before any clingo code**, because it is the
sharpest risk in 003, it is testable with no extra installed, and building it second would mean
retrofitting the bridge around whatever the first `.lp` file happened to need.

## Related

- [003](003-determinism-and-the-engineer-in-the-loop.md) — the direction this settles the last two
  questions of.
- [001](001-mcp-connector.md) — the cost-ladder shape that the grounding budget reuses.
- [../roadmap.md](../roadmap.md) — Phase 4 phasing.

---

## Finding added after Phase 4.3 (2026-09-20)

**The unsat-core risk recorded above was real, and sharper than stated. The mechanism was dropped.**

This record warned that `SolveHandle.core()` returns solver literals whose sign convention does not
map naively onto the assumption symbols, and that a first-cut mapping named one of two conflicting
constraints. Building the feature found a second problem that settles the question:

**clingo's core is not minimal.** Measured on a program with three facts, where exactly two of them
conflict and the third appears in no rule at all, the core came back naming **all three** — plus a
literal whose sign did not correspond to any assumption. Both the selector-atom technique and
direct assumptions produced the same shape.

An explanation that implicates an innocent constraint is worse than no explanation, because it gets
acted on: the engineer changes the wrong requirement and the contradiction stays.

**What shipped instead (1.19.0):** `RuleDiscipline.explain_infeasible` drops one fact at a time and
asks whether the program is still unsatisfiable. A fact whose absence keeps it unsatisfiable was
never part of the reason, so it is discarded. What remains is **minimal by construction** — remove
any one of it and the rules become satisfiable — with no dependence on clingo's internal
representation at all.

It costs one solve per fact, plus one to confirm. That is why it is a separate method rather than
something `solve` does: charging every sweep of a convergence loop for an explanation nobody read is
exactly what [001](001-mcp-connector.md)'s cost ladder exists to prevent.

A second, unplanned result falls out of the same mechanism: when no fact is implicated, the rules
contradict themselves regardless of input. `Conflict.rules_alone` reports it, and the distinction is
the useful part — it tells the engineer whether to read their requirements or the program.

**The general lesson, which is the reason this is recorded here rather than in a changelog.** The
risk in this document was identified by *running the mechanism once and looking at the output*.
Trusting it because clingo is a mature solver and the API is named `core()` would have shipped a
feature that confidently points at the wrong rule. A mechanism can be correct by its own
specification and still be the wrong thing to build a claim on.
