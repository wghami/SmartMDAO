# 007 — Running each pipeline with its own project's Python

**Status:** proposed — not implemented; roadmap 6.6 builds it, and 6.7 (the sweep, record 006)
builds on the same worker
**Date:** 2026-09-26
**Relates to:** [001](001-mcp-connector.md) (what the subprocess buys, and what it does not);
[003](003-determinism-and-the-engineer-in-the-loop.md) (report, do not guess); request R4 in
[requests/2026-09-paper-repro.md](../requests/2026-09-paper-repro.md)

---

## The problem

The MCP server analyses a pipeline file by importing it **in its own process**
([loader.py](../../smartmdao/mcp/loader.py)), and runs one with **its own interpreter**
(`sys.executable -m smartmdao.mcp._runner`, in [execution.py](../../smartmdao/mcp/execution.py)).
So a pipeline can only be analysed or run by a server installed in an environment that also holds
everything the pipeline imports. Verified on 1.25.0: a file whose first line imports a library the
server's environment lacks fails *before any analysis*:

```
analyze_pipeline(...)  ->  Importing needs_lib.py failed: ModuleNotFoundError: No module named ...
```

The requester's model imports networkx, cvxpy and a GeoTIFF reader, and already keeps those imports
inside step bodies purely so the server can introspect the file. Their next model needs networkx
2.x where this one needs 3.x. The only workaround today is one server per environment: tolerable
for a few projects, wasteful for tens.

There is a second, quieter problem. Even where the imports happen to resolve, the server analyses
with **its own** SmartMDAO version, while the project runs with the one it pins. A project pinned
to 1.24.0 has no groups; analysed by a 1.25.0 server, it is described by a version it does not run.
An analysis should describe what `python pipeline.py` *in that project* would do.

## What this record decides

1. How the server finds the interpreter a file belongs to.
2. That analysis *and* execution happen in that interpreter, through one **worker** process speaking
   a **versioned protocol** — the substrate 006's sweep builds on.
3. What happens when the project's SmartMDAO is too old, missing, or newer than the server.
4. When the worker is skipped, because it costs almost a second.

---

## 1. Which interpreter

In order of precedence, and **always reported** in every response as
`interpreter: {path, environment, source, smartmdao}`:

| Source | Rule |
|---|---|
| `explicit` | A `python=` argument on the tool call: that executable. |
| `project` | A `project=` argument: `<project>/.venv`'s interpreter. |
| `discovered` | The nearest ancestor of the file holding a `pyproject.toml` **and** a `.venv`: that `.venv`'s interpreter. |
| `server` | None of the above: the server's own interpreter, which is today's behaviour. |

`.venv/bin/python` on POSIX, `.venv/Scripts/python.exe` on Windows. Nothing else is guessed: not
`VIRTUAL_ENV`, not conda, not whichever `python` is on `PATH`. A project that keeps its
environment elsewhere passes `python=` explicitly.

**Discovery is on by default.** That is a default that changes *which code runs*, so it needs
defending against 003. The defence: the discovered environment is the one the project itself runs
in. Analysing with the server's instead is the *less* truthful choice, because it describes the
file under dependencies and a SmartMDAO version the project never uses. Because the choice is
reported on every call, it is never silent.

**A discovered environment without SmartMDAO** falls back to `server`, reported with the reason
("`<dir>/.venv` has no SmartMDAO"). Refusing would break every project that today relies on the
server's environment happening to hold its dependencies. An **explicit** `python=` or `project=`
without SmartMDAO is refused instead: the caller said which environment to use, and a
substitution there would be exactly the guess 003 forbids.

**Comparing environments, not binaries.** `.venv/bin/python` is normally a symlink to a *shared*
base interpreter (verified here: this checkout's resolves into `~/.local/share/uv/python/…`). Two
different projects' interpreters therefore resolve to the same file. "Same interpreter as the
server" is decided by comparing the environment directory with the server's `sys.prefix`, never
by resolving the executable.

## 2. One worker, one protocol

`python -m smartmdao.mcp._worker`, run by the resolved interpreter. It reads one JSON request on
stdin and writes one JSON response on stdout, as `_runner` does today, and it serves every tool:

```json
{"protocol": [1, 1], "op": "validate", "args": {"path": "…", "variable": null, "inputs": null}}
```
```json
{"protocol": 1, "smartmdao": "1.26.0", "result": { … what the handler returns … }}
```

- `op` is one of `analyze`, `validate`, `explain`, `render`, `run`. `run` is today's `_runner`,
  folded in. The worker calls the **same handler functions** the server calls in-process, so there
  is one implementation and two transports, never two implementations.
- The request carries the **range** of protocol versions the server speaks. The worker answers in
  the highest one it also speaks, or refuses with its own range. One process, no handshake
  round trip. That matters at 0.8 s a spawn.
- The response names the worker's SmartMDAO version. A project's answer is shaped by the version it
  pins (a 1.24.0 project has no `group_notes`), and the caller must be able to tell.
- Every call runs under a wall clock, analysis included: analysing a file imports it, and a file
  whose top-level code hangs must not hang the server. Crash isolation is unchanged. The
  subprocess still buys reliability, not safety ([001](001-mcp-connector.md)): `python=` can name
  any executable, which the agent could equally run from its shell.

Only JSON crosses the boundary, as with `_runner` today. `run` inputs must be JSON values, and
results come back summarised. The project may run a different Python minor version from the
server, and nothing depends on the two agreeing.

## 3. Versions that do not match

| Project's SmartMDAO | What happens |
|---|---|
| Not installed | `discovered`: fall back to `server`, reported. `explicit` / `project`: refused, with the install command. |
| Older than the worker (no `smartmdao.mcp._worker` module) | `discovered`: fall back to `server`, reported. `explicit` / `project`: refused, stating the version: "has SmartMDAO 1.24.0; this server needs >= 1.26.0". The version is read from the environment's `dist-info` directory, so no spawn is needed to say it. |
| Has the worker, no common protocol version | Refused, naming both ranges. |
| **Below the correctness floor** | As above: refused when named, fallback when discovered, even if the protocol matches. See below. |
| Newer than the server | Fine, if a protocol version is shared. The response says which version answered. |

**The correctness floor.** A protocol match says the two processes can *talk*. It does not say the
project's SmartMDAO gives answers worth having. Up to 1.22.0 the loader ran a file's top-level
`pipeline.run()` while "analysing" it, and up to 1.23.0 an edited sibling module was analysed
stale. `MIN_PROJECT_VERSION` is a constant, raised whenever a fix lands in the loader or the
analysis that makes older answers **wrong** rather than merely poorer, and the refusal cites the
fix. It starts at the release that ships the worker, which is later than every fix so far, so
today it costs nothing. It exists so that the next such fix has somewhere to go.

## 4. When the worker is skipped

Measured on this machine: a fresh interpreter importing the handlers costs **0.8–0.9 s**. The same
analysis in-process, warm, takes **7 ms**. The verify loop — analyse, validate, fix, repeat — is
the connector's main use, and a hundredfold tax on it is not a detail.

So when the resolved environment **is** the server's own (`source: server`, or a discovered
`.venv` whose directory equals the server's `sys.prefix`), the handlers run in-process as they do
today. One implementation, two transports: the in-process path is the worker's `op` dispatch
called directly, not a copy of it. `run` keeps its subprocess either way, because a wall clock
needs one.

**Considered and deferred: a persistent worker per environment**, spawned once and reused. It would
make a foreign environment cost what the server's own does. It also brings a lifecycle (restart on
crash or timeout, and eviction, which 1.24.0 showed is easy to get wrong), and a long-lived process
running user code in someone else's environment. Not built until a real session shows the spawn
cost dominating. The trigger, and the numbers above, are recorded so that decision can be made
with evidence.

## What 006 inherits

The sweep is many `run` calls. It gets the environment resolution, the protocol and the version
refusals for free. Parallel points are parallel worker processes, independent by construction.
The worker's reported SmartMDAO version is what 006's resumable store keys on, next to the model's
hash, so a resumed campaign cannot silently mix two versions. `compare_runs` resolves each side
separately; two sides in two environments are allowed and both are reported.

## Alternatives rejected

- **Ship the server's SmartMDAO into the project's environment** (for example via `PYTHONPATH`).
  The pipeline would then run under a SmartMDAO it does not pin, which is the second problem above
  in a different form, and a server newer than the project's Python would break it.
- **Opt-in only (`python=` required).** Simpler, with no default to defend. It leaves the
  wrong-version analysis as the default for everyone who does not know to ask, and the brief's
  problem was precisely that the default is wrong.
- **Pickle across the boundary.** Richer values, but pickles tie both ends to one Python version
  and to importable classes on both sides. JSON is what `_runner` already uses and has not been
  the limit.

## Open questions for the maintainer

1. **Discovery on by default** (above), or opt-in?
2. **Falling back** to the server when a discovered `.venv` lacks SmartMDAO, or refusing there too?
3. **Windows**: the path rule covers it, but CI runs only on Linux. Add a Windows job when this
   ships, or state it as untested?

## Exit criterion for 6.6

A pipeline importing a library absent from the server's environment is analysed, validated,
rendered and run through the MCP, with `interpreter` reported on each response. A project pinned
below the floor is refused with its version and the fix it lacks. The verify loop against the
server's own environment is no slower than today. Timeouts and crash isolation are unchanged.

---

## Findings from building it (6.6)

- **A discovered project below the floor falls back instead of being refused.** As first written,
  the table above refused it, which would have broken every project pinned before 1.26.0, the
  requester's included, on the day the server was upgraded. That is the same argument this record
  already accepted for a discovered `.venv` without SmartMDAO. Refusal stays for environments the
  caller *named*.
- **The version is read statically.** It comes from `smartmdao-<version>.dist-info` in the
  environment's `site-packages`, so the version checks cost no spawn. A metadata-only query of
  the interpreter is used for an explicit interpreter outside a venv, where there is no such
  directory.
