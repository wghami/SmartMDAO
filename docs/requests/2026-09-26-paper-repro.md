# Requests from paper-repro: what SmartMDAO should improve, and why

*Received 2026-09-26 from paper-repro, a downstream project that uses SmartMDAO through its MCP
connector on a model outside aerospace MDO. Each item states the problem and the evidence first,
then a suggested direction and acceptance criteria. The `F-` numbers refer to entries in that
project's feedback log.*

> **Kept as evidence.** This is an edited copy: details internal to the requesting project have
> been removed, and only what bears on SmartMDAO is kept. The plan it produced — including where
> SmartMDAO pushed back — is **Phase 6** in [roadmap.md](../roadmap.md). Decisions live in the
> roadmap and the design records, not here.

---

## The model the evidence comes from

A contract-first pipeline: 23 disciplines split across 6 entry files, with 91 external inputs,
all signatures and no physics yet. It is clean through `analyze` / `validate` / `render`.
`run_pipeline`, `compare_runs` and caching have **not** been exercised yet, so a second batch of
feedback is expected after the first smoke run. The generated code pins SmartMDAO by git tag
(`v1.23.0` at the time of writing).

---

## Priority, and when each item is needed

| Order | Item | Needed | Size (estimate) |
|---|---|---|---|
| 1 | **R1: declared inputs** (F-001, F-007) | Now: every analyze / validate / render call | small |
| 2 | **R2: report stub steps** (F-008) | Now: contract-first progress | small |
| 3 | **R3: sweep / Monte Carlo driver** (F-003) | Before the first campaign of runs | medium |
| 4 | **R4: run each pipeline in its own project's interpreter** (F-009) | Before a second model with different dependencies | medium |
| 5 | **R5: package-relative imports in the loader** (F-005) | Nice to have; there is a workaround | small |
| 6 | **R6: grouped XDSM** (F-002, F-006) | Before teaching material is written | medium |
| 7 | **R7: units** (F-004) | Later; needs a design record first | large |

---

## The requests

### R1. Let a pipeline declare its external inputs once (F-001, F-007)

**Problem.** `analyze_pipeline`, `validate_pipeline` and `render_pipeline_diagram` discover
external inputs only from literal `run(...)` calls in the file (`inputs_used.found_in_source`).
A contract-first pipeline has no `run(...)` call, and a module-level list is ignored. Every call
must repeat `inputs=[...]`.

**Evidence.**
- `validate_pipeline(path)` on a first 17-step skeleton gave `valid: false` with 35 false
  `missing-input` findings. The same call with `inputs=[35 names]` gave `valid: true`.
- On the 23-step contract, 91 names had to be pasted into 18 calls (6 entry files × 3 tools).
- It was hit independently three times, on three different days.

**Why it matters.** Each call is long and error-prone, and a missed name shows up as a false
finding — which trains people to ignore findings. Contract-first is the natural way to design a
pipeline before its disciplines exist, and "which inputs are external" is a property of the
pipeline, not of the call.

**Direction (suggestion).** A declared input list that analysis picks up, for example
`Pipeline(inputs=[...])` or a `pipeline.inputs` attribute, and/or a module-level constant. An
explicit `inputs=` on the call still wins. `inputs_used` reports where the list came from
(`requested` / `declared` / `found_in_source`).

**Acceptance.** analyze / validate / render with no `inputs=` use the declared list; an explicit
argument overrides it; the source of the list is reported; a declared name that no step consumes
is reported, since it may be a typo; the cookbook shows it; introspection still calls no discipline
(invariant 1).

### R2. Report which steps are still stubs (F-008)

**Problem.** A contract step whose body only raises `NotImplementedError` looks the same, to
analyze and explain, as a finished discipline.

**Evidence.** `analyze_pipeline` on the contract lists 23 steps, all stubs, with nothing to tell
them apart. Progress is tracked by hand instead.

**Why it matters.** When a pipeline is built contract-first, "how much of it is real?" should be
answered by the pipeline itself. A hand-kept list drifts.

**Direction.** A **static** check (AST): is the body, ignoring the docstring, a single
`raise NotImplementedError(...)`? Report `stubs: [...]` in `analyze_pipeline` / `explain`;
`validate_pipeline` could flag a stub as an informational finding.

**Acceptance.** Stubs are detected without calling anything. A step with any other body is never
reported as a stub.

### R3. A seeded sweep / Monte Carlo driver (F-003)

**Problem.** `run_pipeline` runs one point, with the smoke / budgeted / full rungs. A reproduction
campaign is many points: realisations × sweep values × scenarios. The roadmap defers `sweep`
"until someone asks": **this is the ask.**

**Evidence (the planned campaign).** One figure needs 100 realisations × 15 values × 2 × 2
configurations; others need 100 time slots per run and up to 37 runs per scenario; then a
sensitivity campaign of 45 entries, each sweeping one assumption over its range.

**Why it matters.** Without a driver, a campaign becomes a plain Python loop around
`pipeline.run`, losing what `run_pipeline` gives: a time limit, crash isolation, typed results, and
a cost quoted before an expensive run (design record 003: decide with a number in hand).

**Direction, domain-neutral.** A design — a grid and/or explicit points — with seeds as ordinary
**pipeline inputs**, never global random state. Quote the cost first from a smoke run of one
point. Run each point isolated, with a time limit. Keep results **resumable**: one record per
point, completed points skipped on re-run. Aggregate with means and confidence intervals over
seeds. A failed point is recorded, never silently dropped. A design record first.

**Acceptance.** Deterministic for fixed seeds; interruptible and resumable; per-point failures
reported; the cost estimate shown before the full run; exposed through the MCP, with the same
rungs philosophy.

### R4. Load and run a pipeline with the interpreter of the project that owns it (F-009)

**Problem.** The MCP server imports pipeline files **in its own process**
(`smartmdao/mcp/loader.py`) and runs them with **its own interpreter** (`run_in_subprocess` →
`sys.executable -m smartmdao.mcp._runner`). So a pipeline whose disciplines import third-party
libraries can only be analysed or run by an MCP installed in *that* environment.

**Evidence.** The model's disciplines will import networkx, cvxpy and a GeoTIFF reader, and keep
those imports inside step bodies only so the MCP can still introspect the file. Different models
bring conflicting pins (networkx 2.x for one, 3.x for another). The only workaround is one MCP
server per environment, which is acceptable for a few projects and wasteful for tens.

**Why it matters.** One MCP serving many projects, each with its own dependencies and pinned
SmartMDAO version, is what lets SmartMDAO be used across a portfolio of models rather than inside
one environment.

**Direction.** Resolve the owning project's interpreter — the nearest `pyproject.toml` with a
`.venv`, or an explicit `project=` / `python=` argument. Load, analyse and run in a subprocess of
that interpreter, returning JSON. Keep invariant 1. **Version skew:** the subprocess runs the
project's SmartMDAO version, which may differ from the server's — keep the runner protocol stable
and versioned, or refuse clearly when versions are incompatible. Never mis-parse silently.

**Acceptance.** A pipeline importing a library absent from the MCP's environment can be analysed,
validated, rendered and run through the MCP; version mismatches are reported, not guessed around;
timeouts and crash isolation are unchanged.

### R5. Import an entry file that sits inside a package (F-005)

**Problem.** The loader imports the file under a generated module name, with its directory on
`sys.path`, so a relative import in the file fails.

**Evidence.** `analyze_pipeline(pkg/pipeline.py)` with `from .physics import lift` gives
`ok: false`, "attempted relative import with no known parent package". A sibling import and an
absolute import of a package next to the file both work.

**Why it matters.** One module per discipline is good design, and the natural entry point lives
inside the package. The workaround — entry files beside the package — works, so this is small and
low priority.

**Direction.** If the file's directory has an `__init__.py` chain, import it as `pkg.module` with
the package's parent on `sys.path`.

**Acceptance.** Relative imports work; standalone files behave as today.

### R6. An XDSM that keeps related disciplines together (F-002, F-006)

**Problem.** The diagonal follows topological execution order only, so independent branches
interleave.

**Evidence.** A 17-step skeleton alternated between two independent parts of the model. The
23-step contract gives a 7277 × 5346 px figure in which the branches alternate down the diagonal.
The workaround — one entry file per part, rendered separately — is readable at 1900 px.

**Why it matters.** The XDSM is a teaching device, and a diagram that mixes unrelated parts teaches
less. The same holds for any multi-part workflow.

**Direction, domain-neutral** ("group", not a domain term). An optional group per step, for example
`@pipeline.step(outputs=..., group="...")` or a step → group mapping passed to `visualize` /
`render_pipeline_diagram`. Order the diagonal by group wherever dependencies allow, say so when a
cross-group dependency forces interleaving, and optionally draw coloured bands. Ungrouped
pipelines render exactly as today.

**Acceptance.** Groups are contiguous when the graph allows it, the order still respects
execution, and the output is unchanged when no groups are given.

### R7. Units on inputs and outputs (F-004) — later, design record first

**Problem.** Units live only in naming conventions (`_db`, `_dbm`, `_hz`, `_mbps`, `_km`). dB
versus linear and Mbps versus bit/s are classic link-budget bugs.

**Evidence.** 14 of the first 35 inputs carry a unit that matters to the result, and the published
reference values reproduce to the third decimal only with particular constants — so unit slips
would show up as spurious discrepancies.

**Direction.** For example `Annotated[float, "dB"]` with a pluggable unit checker, modelled on
`TypeChecker`. **A missing unit is "unchecked", never an error** (invariant 2). Units are read
from annotations without execution (invariant 1).

---

## Release request

Please release with a pushed tag (for example `v1.24.0`): the requesting project pins SmartMDAO by
tag and bumps deliberately, and the docs' "fixed in 1.x" references need the tag.
