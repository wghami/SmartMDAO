"""
Campaigns: many points of one pipeline, quoted first, resumable, never silent
about failures. See docs/design/006.

    from smartmdao.sweep import Campaign

    campaign = Campaign("routing/pipeline.py", store="campaigns/fig9",
                        inputs_from="leo_multicast.baseline:inputs",
                        grid={"constellation": ["kepler", "starlink"]},
                        seeds=range(100), seed_input="seed",
                        outputs=["latency_ms"])
    campaign.quote()                       # one point, measured; the whole campaign estimated
    campaign.run(budget_seconds=600)       # or .start(...) to run it detached
    campaign.aggregate()                   # means and intervals, and what they left out

**Resumable whatever stops it.** Every finished point is appended to
`<store>/points.jsonl` and flushed to disk before the next is handed out. A
budget running out, a stop request, the session ending, the process or the
machine dying - in every case, running the same campaign again skips what is
recorded and does the rest. The store holds only each point's JSON description
and its recorded outputs, never its inputs: the project's code rebuilds those
when a point runs, so no input type can stand in the way of a resume.

**Never two models averaged together.** A point's key includes a hash of the
model's source and the SmartMDAO version that ran it. Edit the model and
nothing stored matches; the status says how many stored points were set aside.
"""
import hashlib
import itertools
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .mcp.protocol import CRASHED, ERROR, FINISHED, OK, TIMED_OUT

#: The oldest SmartMDAO a project's environment may have to run campaign
#: workers: the first release with `smartmdao.mcp._sweeper`.
CAMPAIGN_MIN_VERSION = "1.30.0"

#: A worker is replaced after this many points, so a slow leak in a
#: discipline cannot grow for hours.
RECYCLE_AFTER = 500

#: How often a running coordinator rewrites its heartbeat, and how old one
#: may get before `status()` calls the campaign interrupted.
HEARTBEAT_SECONDS = 2.0
STALE_AFTER_SECONDS = 15.0

#: When no timeout is given: this multiple of the quoted point, but at least
#: the floor - or, with no quote, the fallback.
TIMEOUT_MULTIPLE = 20
TIMEOUT_FLOOR_SECONDS = 60.0
TIMEOUT_WITHOUT_QUOTE_SECONDS = 600.0


class CampaignError(Exception):
    """The campaign cannot go on as asked; the message says why."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _first_seen(values: Iterable[Any]) -> List[Any]:
    """Unique values in the order they first appear - so a reopened design is the same design."""
    seen, ordered = set(), []
    for value in values:
        marker = _canonical(value)
        if marker not in seen:
            seen.add(marker)
            ordered.append(value)
    return ordered


@dataclass
class Campaign:
    """
    A campaign over one pipeline file. Everything that decides the *results* is
    stored in `<store>/campaign.json`; a store holding a different campaign is
    refused rather than mixed into.
    """
    path: str
    store: str
    variable: Optional[str] = None
    outputs: Optional[Sequence[str]] = None
    inputs_from: Optional[str] = None
    inputs_args: Optional[Sequence[Any]] = None
    inputs_kwargs: Optional[Dict[str, Any]] = None
    inputs: Optional[Dict[str, Any]] = None
    grid: Optional[Dict[str, Sequence[Any]]] = None
    points: Optional[Sequence[Dict[str, Any]]] = None
    seeds: Optional[Iterable[Any]] = None
    seed_input: Optional[str] = None
    workers: Optional[int] = None
    point_timeout: Optional[float] = None
    allow_effects: bool = False
    python: Optional[str] = None
    project: Optional[str] = None
    _seeds: List[Any] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self):
        self.path = str(Path(self.path).expanduser().resolve())
        self.store = str(Path(self.store).expanduser().resolve())
        self._seeds = list(self.seeds) if self.seeds is not None else []
        if self._seeds and not self.seed_input:
            raise CampaignError("seeds were given without seed_input: name the input they go to.")
        if not self.grid and not self.points:
            raise CampaignError("a campaign needs a grid, a list of points, or both.")

    # ------------------------------------------------------------------ design

    def spec(self) -> Dict[str, Any]:
        """Everything that decides the results - not how fast they are computed."""
        return {
            "variable": self.variable,
            "outputs": list(self.outputs) if self.outputs else None,
            "inputs_from": self.inputs_from,
            "inputs_args": list(self.inputs_args or []),
            "inputs_kwargs": dict(self.inputs_kwargs or {}),
            "inputs": dict(self.inputs or {}),
            "seed_input": self.seed_input,
            "design": self.design(),
        }

    def design(self) -> List[Dict[str, Any]]:
        """Every point, each with its seed: the grid's product, then the listed points."""
        params: List[Dict[str, Any]] = []
        if self.grid:
            names = list(self.grid)
            params.extend(dict(zip(names, values)) for values in itertools.product(*(self.grid[n] for n in names)))
        params.extend(dict(point) for point in (self.points or []))
        seeds = self._seeds or [None]
        design, seen = [], set()
        for point in params:
            for seed in seeds:
                entry = {"params": point, "seed": seed}
                marker = _canonical(entry)
                if marker not in seen:                     # a point listed twice runs once
                    seen.add(marker)
                    design.append(entry)
        return design

    def key(self, entry: Dict[str, Any], model: str, version: str) -> str:
        """A point's identity: the model's source, the SmartMDAO that ran it, the point."""
        if not hasattr(self, "_key_base"):
            # Computed once: rebuilding the design for every key made keying a
            # campaign quadratic in its size.
            self._key_base = _canonical({k: v for k, v in self.spec().items() if k != "design"})
        return hashlib.sha256(
            _canonical({"model": model, "smartmdao": version, "spec": self._key_base, **entry}).encode()
        ).hexdigest()

    # ------------------------------------------------------------------- store

    @property
    def _dir(self) -> Path:
        return Path(self.store)

    def _file(self, name: str) -> Path:
        return self._dir / name

    def _read_json(self, name: str) -> Dict[str, Any]:
        try:
            return json.loads(self._file(name).read_text())
        except (OSError, ValueError):
            return {}

    def _write_json(self, name: str, data: Dict[str, Any]) -> None:
        target = self._file(name)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True))
        os.replace(temporary, target)                     # never a half-written file

    def _claim_store(self) -> Dict[str, Any]:
        """Creates the store, or checks it holds this campaign."""
        self._dir.mkdir(parents=True, exist_ok=True)
        stored = self._read_json("campaign.json")
        spec = self.spec()
        if stored and stored.get("spec") != spec:
            raise CampaignError(
                f"{self.store} holds a different campaign (its design, outputs or inputs differ). "
                f"Use another store, or open this one with Campaign.open()."
            )
        if not stored:
            stored = {"path": self.path, "spec": spec, "created": _now()}
            self._write_json("campaign.json", stored)
        return stored

    def records(self) -> Dict[str, Dict[str, Any]]:
        """The last record for every key in the store. A torn last line is skipped."""
        latest: Dict[str, Dict[str, Any]] = {}
        try:
            lines = self._file("points.jsonl").read_text().splitlines()
        except OSError:
            return latest
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                continue                                   # a write cut short by a kill
            latest[record["key"]] = record
        return latest

    def _append(self, record: Dict[str, Any]) -> None:
        with open(self._file("points.jsonl"), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())                     # on disk before the next point

    @classmethod
    def open(cls, store: str, **run_settings) -> "Campaign":
        """The campaign a store holds, to resume, inspect or aggregate it."""
        stored = json.loads((Path(store) / "campaign.json").read_text())
        spec = stored["spec"]
        return cls(
            path=stored["path"], store=store, variable=spec["variable"], outputs=spec["outputs"],
            inputs_from=spec["inputs_from"], inputs_args=spec["inputs_args"],
            inputs_kwargs=spec["inputs_kwargs"], inputs=spec["inputs"], seed_input=spec["seed_input"],
            points=_first_seen(entry["params"] for entry in spec["design"]),
            seeds=_first_seen(entry["seed"] for entry in spec["design"]) if spec["seed_input"] else None,
            **run_settings,
        )

    # ----------------------------------------------------------------- workers

    def _interpreter(self):
        from .mcp.environment import resolve, version_tuple

        interpreter, refusal = resolve(self.path, self.python, self.project)
        if refusal:
            raise CampaignError(refusal["error"])
        if interpreter.smartmdao and version_tuple(interpreter.smartmdao) < version_tuple(CAMPAIGN_MIN_VERSION):
            raise CampaignError(
                f"The environment at {interpreter.environment} has SmartMDAO {interpreter.smartmdao}; "
                f"campaigns need {CAMPAIGN_MIN_VERSION} or later there."
            )
        return interpreter

    def _setup(self) -> Dict[str, Any]:
        spec = self.spec()
        return {
            "path": self.path, "variable": spec["variable"], "outputs": spec["outputs"],
            "inputs_from": spec["inputs_from"], "inputs_args": spec["inputs_args"],
            "inputs_kwargs": spec["inputs_kwargs"], "inputs": spec["inputs"],
            "seed_input": spec["seed_input"], "allow_effects": self.allow_effects,
        }

    def _timeout(self) -> float:
        if self.point_timeout:
            return float(self.point_timeout)
        quoted = self._read_json("campaign.json").get("quote", {}).get("one_point_seconds")
        if quoted:
            return max(TIMEOUT_FLOOR_SECONDS, TIMEOUT_MULTIPLE * quoted)
        return TIMEOUT_WITHOUT_QUOTE_SECONDS

    # ------------------------------------------------------------------- quote

    def quote(self) -> Dict[str, Any]:
        """
        Runs one point, in the project's environment, and estimates the rest.

        The point is recorded like any other, so it is never computed twice.
        """
        self._claim_store()
        pool = _Pool(self._interpreter(), self._setup(), size=1, log=self._file("workers.log"))
        try:
            started = time.monotonic()
            ready = pool.start_one()
            startup = time.monotonic() - started
            model, version = ready["model_hash"], ready["smartmdao"]
            done = self.records()
            pending = [e for e in self.design() if done.get(self.key(e, model, version), {}).get("status") not in FINISHED]
            entry = pending[0] if pending else self.design()[0]
            record = pool.run_one(0, entry, self.key(entry, model, version), self._timeout())
            self._append(self._decorate(record, entry, model, version))
        finally:
            pool.close()

        workers = self._pool_size(max(len(pending) - 1, 1))
        remaining = max(len(pending) - 1, 0)
        estimate = startup + math.ceil(remaining / workers) * record.get("elapsed", 0.0)
        quote = {
            "points": len(self.design()),
            "pending": remaining,
            "workers": workers,
            "one_point_seconds": record.get("elapsed"),
            "startup_seconds": round(startup, 3),
            "estimate_seconds": round(estimate, 1),
            "point": {k: record.get(k) for k in ("status", "outputs", "error", "reason") if record.get(k) is not None},
            "outputs": ready["outputs"],
        }
        stored = self._read_json("campaign.json")
        stored["quote"] = {k: quote[k] for k in ("one_point_seconds", "startup_seconds")}
        self._write_json("campaign.json", stored)
        quote["point_timeout_seconds"] = round(self._timeout(), 1)
        quote["note"] = (
            f"One point took {quote['one_point_seconds']}s. The {remaining} left on {workers} worker(s) "
            f"should take about {quote['estimate_seconds']}s. Quote this before starting; "
            f"starting needs a budget."
        )
        return quote

    def _pool_size(self, pending: int) -> int:
        size = self.workers or max((os.cpu_count() or 2) - 1, 1)
        return max(1, min(size, pending))

    @staticmethod
    def _decorate(record: Dict[str, Any], entry: Dict[str, Any], model: str, version: str) -> Dict[str, Any]:
        return {**record, **entry, "model_hash": model, "smartmdao": version, "finished": _now()}

    # --------------------------------------------------------------------- run

    def run(self, budget_seconds: float, retry_failed: bool = False) -> Dict[str, Any]:
        """
        Runs every point not yet recorded as finished, in parallel, and blocks.

        `budget_seconds` is required: when it runs out no new point is handed
        out, points still running are stopped and will run again next time, and
        the campaign ends as `stopped: budget`. Nothing recorded is lost.
        """
        if not budget_seconds or budget_seconds <= 0:
            raise CampaignError("a campaign needs a positive budget_seconds; there is no default.")
        stored = self._claim_store()
        interpreter = self._interpreter()
        started, deadline = time.monotonic(), time.monotonic() + budget_seconds
        try:
            self._file("stop").unlink()
        except FileNotFoundError:
            pass

        state = {"state": "running", "pid": os.getpid(), "started": _now(), "budget_seconds": budget_seconds}
        self._beat(state, force=True)

        pool = _Pool(interpreter, self._setup(), size=0, log=self._file("workers.log"))
        try:
            ready = pool.start_one()
            model, version = ready["model_hash"], ready["smartmdao"]
            stored.update({"model_hash": model, "smartmdao": version, "interpreter": interpreter.describe()})
            self._write_json("campaign.json", stored)

            done = self.records()
            retry = (ERROR, TIMED_OUT, CRASHED) if retry_failed else ()
            todo = [
                e for e in self.design()
                if (status := done.get(self.key(e, model, version), {}).get("status")) is None
                or status in retry
            ]
            size = self._pool_size(len(todo))
            for _ in range(size - 1):
                pool.start_one(expect=model)
            outcome = pool.run_all(
                todo, lambda e: self.key(e, model, version), self._timeout(), deadline,
                record=lambda rec, e: self._append(self._decorate(rec, e, model, version)),
                should_stop=lambda: self._file("stop").exists(),
                beat=lambda counts: self._beat({**state, **counts}),
            )
        except CampaignError as error:
            self._beat({**state, "state": "refused", "error": str(error)}, force=True)
            raise
        finally:
            pool.close()

        final = {**state, **outcome, "elapsed_seconds": round(time.monotonic() - started, 3)}
        self._beat(final, force=True)
        return {**self.status(), "run": final}

    def _beat(self, data: Dict[str, Any], force: bool = False) -> None:
        now = time.monotonic()
        if force or now - getattr(self, "_last_beat", 0.0) >= HEARTBEAT_SECONDS:
            self._last_beat = now
            self._write_json("coordinator.json", {**data, "heartbeat": time.time()})

    # ------------------------------------------------------ detached and status

    def start(self, budget_seconds: float, retry_failed: bool = False) -> Dict[str, Any]:
        """
        Runs the campaign in its own process and returns at once. It outlives
        the caller - a session ending does not stop it - and `status()` reads
        its progress from the store. Refused while another coordinator is alive
        on this store; an interrupted one is simply resumed.
        """
        if not budget_seconds or budget_seconds <= 0:
            raise CampaignError("a campaign needs a positive budget_seconds; there is no default.")
        self._claim_store()
        self._interpreter()
        current = self.status()
        if current["state"] == "running":
            raise CampaignError(f"a campaign is already running on {self.store} (pid {current.get('pid')}).")

        settings = {"workers": self.workers, "point_timeout": self.point_timeout,
                    "allow_effects": self.allow_effects, "python": self.python, "project": self.project}
        command = [sys.executable, "-m", "smartmdao._campaign", self.store,
                   "--budget", str(budget_seconds), "--settings", json.dumps(settings)]
        if retry_failed:
            command.append("--retry-failed")
        log = open(self._file("coordinator.log"), "a", encoding="utf-8")
        detach = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
                  if os.name == "nt" else {"start_new_session": True})
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **detach)
        log.close()
        self._beat({"state": "starting", "pid": process.pid, "started": _now(),
                    "budget_seconds": budget_seconds}, force=True)
        return {"started": True, "pid": process.pid, "store": self.store,
                "note": "Running on its own. Ask status() for progress; stop() stops it, resumably."}

    def stop(self) -> Dict[str, Any]:
        """Asks a running campaign to stop handing out points. Resumable."""
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file("stop").write_text(_now())
        return {"stopping": True, "store": self.store}

    def status(self) -> Dict[str, Any]:
        """Where the campaign stands, read from the store alone."""
        stored = self._read_json("campaign.json")
        coordinator = self._read_json("coordinator.json")
        state = coordinator.get("state", "not started")
        if state in ("running", "starting") and time.time() - coordinator.get("heartbeat", 0) > STALE_AFTER_SECONDS:
            state = "interrupted"                        # its process died without saying so

        model, version = stored.get("model_hash"), stored.get("smartmdao")
        design = self.design()
        records = self.records()
        current = {self.key(e, model, version): e for e in design} if model else {}
        counts: Dict[str, int] = {}
        failures = []
        for key in current:
            status = records.get(key, {}).get("status", "pending")
            counts[status] = counts.get(status, 0) + 1
            if status in (ERROR, TIMED_OUT, CRASHED):
                failures.append({k: records[key].get(k) for k in ("params", "seed", "status", "error", "reason") if records[key].get(k) is not None})
        set_aside = sum(1 for key in records if key not in current)

        result = {
            "store": self.store,
            "state": state,
            "points": len(design),
            "counts": counts if model else {"pending": len(design)},
            "failures": failures[:5],
            "more_failures": max(len(failures) - 5, 0),
        }
        for name in ("pid", "budget_seconds", "elapsed_seconds", "error"):
            if name in coordinator:
                result[name] = coordinator[name]
        if set_aside:
            result["set_aside"] = set_aside
            result["note"] = (
                f"{set_aside} stored point(s) belong to another version of the model or of "
                f"SmartMDAO. They are kept but never mixed in; their points run again."
            )
        return result

    # --------------------------------------------------------------- aggregate

    def aggregate(self, confidence: float = 0.95) -> Dict[str, Any]:
        """
        Groups points that differ only by seed. For each numeric output: n,
        mean, standard deviation and a Student-t confidence half-width - over
        the `ok` points only, with what was left out counted beside them.
        """
        from scipy import stats

        stored = self._read_json("campaign.json")
        model, version = stored.get("model_hash"), stored.get("smartmdao")
        records = self.records()
        groups: Dict[str, Dict[str, Any]] = {}
        for entry in self.design():
            record = records.get(self.key(entry, model, version), {}) if model else {}
            group = groups.setdefault(_canonical(entry["params"]), {"params": entry["params"], "values": {}, "excluded": {}})
            status = record.get("status", "pending")
            if status != OK:
                group["excluded"][status] = group["excluded"].get(status, 0) + 1
                continue
            for name, value in (record.get("outputs") or {}).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    group["values"].setdefault(name, []).append(float(value))

        result = []
        for marker in sorted(groups):
            group = groups[marker]
            summary = {"params": group["params"], "excluded": group["excluded"], "outputs": {}}
            for name, values in sorted(group["values"].items()):
                n = len(values)
                mean = sum(values) / n
                std = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else 0.0
                half = float(stats.t.ppf((1 + confidence) / 2, n - 1) * std / math.sqrt(n)) if n > 1 else None
                summary["outputs"][name] = {"n": n, "mean": mean, "std": std, "ci_half_width": half}
            result.append(summary)
        return {"confidence": confidence, "groups": result}


class _Pool:
    """
    Long-lived workers in the project's environment, each loading the pipeline
    once. One reader thread per worker feeds a single queue, so the coordinator
    waits on results and deadlines alike - and nothing relies on select(),
    which Windows does not offer for pipes.
    """

    def __init__(self, interpreter, setup: Dict[str, Any], size: int, log: Path):
        self.interpreter, self.setup, self.log_path = interpreter, setup, log
        self.results: "queue.Queue" = queue.Queue()
        self.slots: List[Dict[str, Any]] = []
        self.model: Optional[str] = None

    def start_one(self, expect: Optional[str] = None, index: Optional[int] = None) -> Dict[str, Any]:
        """
        Starts a worker and waits for it to load; refuses as the worker does.
        With `index`, the new worker takes over that slot.
        """
        log = open(self.log_path, "a", encoding="utf-8")
        process = subprocess.Popen(
            [self.interpreter.path, "-m", "smartmdao.mcp._sweeper"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, bufsize=1,
        )
        log.close()
        slot = {"id": len(self.slots) if index is None else index, "process": process, "busy": None, "done": 0}
        if index is None:
            self.slots.append(slot)
        else:
            self.slots[index] = slot
        threading.Thread(target=self._read, args=(slot["id"], process), daemon=True).start()
        process.stdin.write(json.dumps(self.setup) + "\n")
        process.stdin.flush()
        ready = self._wait_for(slot["id"], TIMEOUT_WITHOUT_QUOTE_SECONDS)
        if ready is None or not ready.get("ready"):
            reason = (ready or {}).get("error") or f"the worker exited; see {self.log_path}"
            raise CampaignError(reason)
        if expect and ready["model_hash"] != expect:
            raise CampaignError("the model's source changed while the campaign was starting; start it again.")
        self.model = ready["model_hash"]
        return ready

    def _read(self, slot_id: int, process) -> None:
        for line in process.stdout:
            self.results.put((slot_id, process, line))
        self.results.put((slot_id, process, None))                  # end of stream: it died or was killed

    def _wait_for(self, slot_id: int, timeout: float) -> Optional[Dict[str, Any]]:
        """
        The next message from one worker. Messages from the others that arrive
        meanwhile are put back, in order - a result is never dropped because a
        replacement worker happened to be loading.
        """
        deadline = time.monotonic() + timeout
        others = []
        try:
            while time.monotonic() < deadline:
                try:
                    item = self.results.get(timeout=max(deadline - time.monotonic(), 0.01))
                except queue.Empty:
                    break
                got, process, line = item
                if got == slot_id and process is self.slots[slot_id]["process"]:
                    return json.loads(line) if line else None
                others.append(item)
            return None
        finally:
            for item in others:
                self.results.put(item)

    def _send(self, slot: Dict[str, Any], entry: Dict[str, Any], key: str) -> None:
        slot["busy"] = {"entry": entry, "key": key, "since": time.monotonic()}
        slot["process"].stdin.write(json.dumps({"key": key, **entry}) + "\n")
        slot["process"].stdin.flush()

    def run_one(self, slot_id: int, entry: Dict[str, Any], key: str, timeout: float) -> Dict[str, Any]:
        slot = self.slots[slot_id]
        self._send(slot, entry, key)
        record = self._wait_for(slot_id, timeout)
        slot["busy"] = None
        if record is None:
            self._kill(slot)
            return {"key": key, "status": TIMED_OUT, "error": f"no answer within {timeout:g}s"}
        return record

    def run_all(self, todo, key_of, timeout, deadline, record, should_stop, beat) -> Dict[str, Any]:
        pending = list(todo)
        counts = {"finished": 0, "failed": 0, "pending": len(pending)}
        stopping = None

        def hand_out(slot):
            nonlocal stopping
            if stopping is None and should_stop():
                stopping = "request"                         # asked between two points: start no more
            if pending and stopping is None:
                entry = pending.pop(0)
                self._send(slot, entry, key_of(entry))

        for slot in self.slots:
            hand_out(slot)

        while any(slot["busy"] for slot in self.slots):
            now = time.monotonic()
            if stopping is None and now >= deadline:
                stopping = "budget"
                for slot in self.slots:                      # not failures: they run again next time
                    if slot["busy"]:
                        pending.insert(0, slot["busy"]["entry"])
                        slot["busy"] = None
                        self._kill(slot)
                break
            if stopping is None and should_stop():
                stopping = "request"                         # running points finish, none start

            waits = [slot["busy"]["since"] + timeout - now for slot in self.slots if slot["busy"]]
            try:
                slot_id, process, line = self.results.get(timeout=max(min(waits + [HEARTBEAT_SECONDS]), 0.01))
            except queue.Empty:
                slot_id = None

            if slot_id is not None and process is self.slots[slot_id]["process"]:
                slot = self.slots[slot_id]
                busy = slot["busy"]
                if busy:
                    if line:
                        result = json.loads(line)
                    else:
                        result = {"key": busy["key"], "status": CRASHED,
                                  "error": f"the worker died while running this point; see {self.log_path}"}
                    record(result, busy["entry"])
                    counts["finished" if result["status"] in FINISHED else "failed"] += 1
                    slot["busy"] = None
                    slot["done"] += 1
                    if not line or slot["done"] >= RECYCLE_AFTER:
                        slot = self._replace(slot)          # the new worker, not the dead one
                    hand_out(slot)

            for slot in self.slots:
                busy = slot["busy"]
                if busy and time.monotonic() - busy["since"] > timeout:
                    record({"key": busy["key"], "status": TIMED_OUT,
                            "error": f"no answer within {timeout:g}s; the worker was stopped"}, busy["entry"])
                    counts["failed"] += 1
                    slot["busy"] = None
                    hand_out(self._replace(slot))

            counts["pending"] = len(pending)
            beat(dict(counts))

        counts["pending"] = len(pending)
        if stopping == "budget":
            return {**counts, "state": "stopped: budget"}
        if stopping == "request" or pending:
            return {**counts, "state": "stopped: request"}
        return {**counts, "state": "finished"}

    def _kill(self, slot: Dict[str, Any]) -> None:
        try:
            slot["process"].kill()
        except OSError:                                      # pragma: no cover - already gone
            pass

    def _replace(self, slot: Dict[str, Any]) -> Dict[str, Any]:
        """
        Retires a worker - crashed, overrun or worn - and returns the fresh one
        that takes its place. Callers must use the one returned: the old
        worker's pipe is closed.
        """
        self._kill(slot)
        self.start_one(expect=self.model, index=slot["id"])
        return self.slots[slot["id"]]

    def close(self) -> None:
        for slot in self.slots:
            try:
                slot["process"].stdin.close()
            except OSError:                                  # pragma: no cover
                pass
            try:
                slot["process"].wait(timeout=5)
            except subprocess.TimeoutExpired:                # pragma: no cover - a worker that will not stop
                self._kill(slot)
