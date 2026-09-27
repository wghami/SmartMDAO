"""
Campaigns: roadmap 6.7, design record 006.

The requirement the maintainer added: a campaign resumes from where it stopped,
whatever stopped it - a budget, a stop request, the session or the process
dying - and the store never depends on an input's type. Those are tested by
doing each of them to a real campaign.
"""
import json
import os
import signal
import sys
import textwrap
import time
from pathlib import Path

import pytest

import smartmdao.sweep as sweep
from smartmdao.mcp import _sweeper
from smartmdao.mcp.protocol import CRASHED, ERROR, NOT_CONVERGED, OK, TIMED_OUT
from smartmdao.sweep import Campaign, CampaignError

MOODY = """
import os, time
from smartmdao import Pipeline

pipeline = Pipeline(inputs=["mode", "seed"])

@pipeline.step(outputs=["value"])
def behave(mode: str, seed: float) -> float:
    if mode == "raise":
        raise ValueError("solver reported 'inaccurate'")
    if mode == "hang":
        time.sleep(30)
    if mode == "crash":
        os._exit(3)
    if mode == "slow":
        time.sleep(0.3)
    return seed * 2.0

@pipeline.step(outputs=["label"])
def name(mode: str) -> str:
    return mode
"""

BUILT = """
from typing import Tuple
from smartmdao import Pipeline

pipeline = Pipeline(inputs=["box", "seed"])

@pipeline.step(outputs=["area"])
def area(box: Tuple[float, float], seed: float) -> float:
    return box[0] * box[1] + seed
"""

BASELINE = """
def inputs(scenario, scale=1.0):
    return {"box": ({"small": 2.0, "large": 4.0}[scenario] * scale, 3.0)}
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))
    return path


@pytest.fixture
def moody(tmp_path):
    return str(write(tmp_path / "model" / "moody.py", MOODY))


def campaign(moody, tmp_path, name="store", **settings):
    defaults = dict(grid={"mode": ["ok"]}, seeds=range(4), seed_input="seed", outputs=["value"], workers=1)
    return Campaign(moody, str(tmp_path / name), **{**defaults, **settings})


# ==============================================================================
# The design
# ==============================================================================

def test_a_design_is_the_grid_then_the_points_each_crossed_with_the_seeds(moody, tmp_path):
    c = Campaign(moody, str(tmp_path / "s"), grid={"a": [1, 2], "b": ["x"]}, points=[{"a": 9, "b": "y"}, {"a": 1, "b": "x"}],
                 seeds=[0, 1], seed_input="seed")
    assert [(e["params"], e["seed"]) for e in c.design()] == [
        ({"a": 1, "b": "x"}, 0), ({"a": 1, "b": "x"}, 1),
        ({"a": 2, "b": "x"}, 0), ({"a": 2, "b": "x"}, 1),
        ({"a": 9, "b": "y"}, 0), ({"a": 9, "b": "y"}, 1),        # the repeated point runs once
    ]


def test_without_seeds_each_point_runs_once(moody, tmp_path):
    assert Campaign(moody, str(tmp_path / "s"), points=[{"mode": "ok"}]).design() == [{"params": {"mode": "ok"}, "seed": None}]


@pytest.mark.parametrize("settings, says", [
    ({"grid": {"mode": ["ok"]}, "seeds": [1]}, "without seed_input"),
    ({}, "needs a grid"),
])
def test_an_incomplete_campaign_is_refused(moody, tmp_path, settings, says):
    with pytest.raises(CampaignError, match=says):
        Campaign(moody, str(tmp_path / "s"), **settings)


def test_a_reopened_campaign_is_the_same_campaign(moody, tmp_path):
    """Seeds keep their order: sorting '10' before '2' once made a store look foreign."""
    original = campaign(moody, tmp_path, seeds=range(12), grid={"mode": ["ok", "slow"]})
    original._claim_store()
    reopened = Campaign.open(original.store)
    assert reopened.design() == original.design()
    reopened._claim_store()                                  # accepted, not "a different campaign"


def test_a_store_holding_another_campaign_is_refused(moody, tmp_path):
    campaign(moody, tmp_path)._claim_store()
    with pytest.raises(CampaignError, match="holds a different campaign"):
        campaign(moody, tmp_path, seeds=range(5))._claim_store()


def test_a_torn_last_line_is_skipped(moody, tmp_path):
    c = campaign(moody, tmp_path)
    c._claim_store()
    Path(c.store, "points.jsonl").write_text('{"key": "a", "status": "ok"}\n{"key": "b", "sta')
    assert list(c.records()) == ["a"]


# ==============================================================================
# Quote, run, resume
# ==============================================================================

def test_the_quote_runs_one_point_and_records_it(moody, tmp_path):
    c = campaign(moody, tmp_path)
    quote = c.quote()

    assert quote["points"] == 4 and quote["pending"] == 3
    assert quote["point"]["status"] == OK and quote["outputs"] == ["value"]
    assert "Quote this before starting" in quote["note"]
    assert len(c.records()) == 1                             # never computed twice
    assert c._timeout() == sweep.TIMEOUT_FLOOR_SECONDS       # 20 x a millisecond is below the floor


def test_a_quote_on_a_finished_campaign_still_measures_a_point(moody, tmp_path):
    c = campaign(moody, tmp_path, seeds=[1])
    c.quote()
    assert c.quote()["pending"] == 0


def test_a_run_records_every_point_and_a_second_run_computes_nothing(moody, tmp_path):
    c = campaign(moody, tmp_path, workers=2)
    first = c.run(budget_seconds=60)
    assert first["run"]["state"] == "finished" and first["counts"] == {OK: 4}

    again = c.run(budget_seconds=60)
    assert again["run"]["finished"] == 0
    assert len(Path(c.store, "points.jsonl").read_text().splitlines()) == 4


def test_a_budget_stops_it_and_a_new_run_finishes_it(moody, tmp_path):
    c = campaign(moody, tmp_path, grid={"mode": ["slow"]}, seeds=range(8), point_timeout=10)
    stopped = c.run(budget_seconds=1.2)
    assert stopped["run"]["state"] == "stopped: budget"
    assert stopped["counts"].get("pending", 0) > 0

    finished = c.run(budget_seconds=60)
    assert finished["counts"] == {OK: 8}
    assert len(Path(c.store, "points.jsonl").read_text().splitlines()) == 8     # nothing twice


def test_a_stop_request_lets_running_points_finish_and_starts_no_more(moody, tmp_path, monkeypatch):
    c = campaign(moody, tmp_path, grid={"mode": ["slow"]}, seeds=range(6), point_timeout=10)
    c._claim_store()
    real = sweep._Pool.run_all

    def stop_after_first(self, todo, key_of, timeout, deadline, record, should_stop, beat):
        def recording(result, entry):
            record(result, entry)
            c.stop()                                          # asked while the campaign runs
        return real(self, todo, key_of, timeout, deadline, recording, should_stop, beat)

    monkeypatch.setattr(sweep._Pool, "run_all", stop_after_first)
    result = c.run(budget_seconds=60)
    assert result["run"]["state"] == "stopped: request"
    assert result["counts"][OK] == 1


def test_a_budget_is_required(moody, tmp_path):
    for bad in (None, 0, -1):
        with pytest.raises(CampaignError, match="budget_seconds"):
            campaign(moody, tmp_path).run(budget_seconds=bad)
        with pytest.raises(CampaignError, match="budget_seconds"):
            campaign(moody, tmp_path).start(budget_seconds=bad)


# ==============================================================================
# Failures are recorded, never dropped
# ==============================================================================

def test_every_way_a_point_can_fail_is_recorded(moody, tmp_path):
    c = campaign(moody, tmp_path, grid={"mode": ["ok", "raise", "hang", "crash"]}, seeds=[1],
                 workers=2, point_timeout=1.5)
    result = c.run(budget_seconds=60)

    assert result["counts"] == {OK: 1, ERROR: 1, TIMED_OUT: 1, CRASHED: 1}
    errors = {f["status"]: f["error"] for f in result["failures"]}
    assert "inaccurate" in errors[ERROR]
    assert "1.5s" in errors[TIMED_OUT] and "died" in errors[CRASHED]


def test_failed_points_run_again_only_when_asked(moody, tmp_path):
    c = campaign(moody, tmp_path, grid={"mode": ["raise"]}, seeds=[1])
    c.run(budget_seconds=60)
    assert c.run(budget_seconds=60)["run"]["finished"] + c.run(budget_seconds=60)["run"]["failed"] == 0
    assert c.run(budget_seconds=60, retry_failed=True)["run"]["failed"] == 1


def test_a_long_run_replaces_its_workers(moody, tmp_path, monkeypatch):
    monkeypatch.setattr(sweep, "RECYCLE_AFTER", 2)
    assert campaign(moody, tmp_path, seeds=range(5)).run(budget_seconds=60)["counts"] == {OK: 5}


def test_a_campaign_with_more_failures_than_shown_says_so(moody, tmp_path):
    c = campaign(moody, tmp_path, grid={"mode": ["raise"]}, seeds=range(7))
    status = c.run(budget_seconds=60)
    assert len(status["failures"]) == 5 and status["more_failures"] == 2


# ==============================================================================
# Refusals
# ==============================================================================

def test_a_seed_no_step_reads_is_refused_before_anything_runs(moody, tmp_path):
    c = campaign(moody, tmp_path, seed_input="nothing_reads_this")
    with pytest.raises(CampaignError, match="same run"):
        c.run(budget_seconds=60)
    assert c.status()["state"] == "refused"


def test_declared_side_effects_are_refused_unless_allowed(tmp_path):
    model = write(tmp_path / "effects.py", """
        from smartmdao import Pipeline
        pipeline = Pipeline(inputs=["a"])
        @pipeline.step(outputs=["b"], effects=True)
        def send(a: float) -> float:
            return a
    """)
    c = Campaign(str(model), str(tmp_path / "s"), points=[{"a": 1.0}])
    with pytest.raises(CampaignError, match="once per point"):
        c.quote()
    allowed = Campaign(str(model), str(tmp_path / "s2"), points=[{"a": 1.0}], allow_effects=True)
    assert allowed.run(budget_seconds=60)["counts"] == {OK: 1}


def test_an_environment_without_campaign_workers_is_refused(moody, tmp_path):
    project = tmp_path / "old"
    (project / ".venv" / "bin").mkdir(parents=True)
    (project / ".venv" / "bin" / "python").write_text("")
    (project / ".venv" / "lib" / "python3.11" / "site-packages" / "smartmdao-1.29.0.dist-info").mkdir(parents=True)
    with pytest.raises(CampaignError, match="1.30.0 or later"):
        campaign(moody, tmp_path, project=str(project))._interpreter()


def test_an_interpreter_that_does_not_exist_is_refused(moody, tmp_path):
    with pytest.raises(CampaignError, match="No such interpreter"):
        campaign(moody, tmp_path, python=str(tmp_path / "nope")).quote()


def test_a_worker_that_cannot_start_is_reported(moody, tmp_path):
    c = campaign(moody, tmp_path)
    c._claim_store()
    interpreter = c._interpreter()
    pool = sweep._Pool(interpreter, {**c._setup(), "path": str(tmp_path / "gone.py")}, 0, Path(c.store, "w.log"))
    with pytest.raises(CampaignError, match="No such file"):
        pool.start_one()
    pool.close()


def test_a_model_that_changes_while_starting_is_refused(moody, tmp_path):
    c = campaign(moody, tmp_path)
    c._claim_store()
    pool = sweep._Pool(c._interpreter(), c._setup(), 0, Path(c.store, "w.log"))
    with pytest.raises(CampaignError, match="changed while"):
        pool.start_one(expect="another hash")
    pool.close()


# ==============================================================================
# Status, and never two models averaged together
# ==============================================================================

def test_status_before_anything_ran(moody, tmp_path):
    status = campaign(moody, tmp_path).status()
    assert status["state"] == "not started" and status["counts"] == {"pending": 4}


def test_an_edited_model_sets_the_old_points_aside(moody, tmp_path):
    c = campaign(moody, tmp_path)
    c.run(budget_seconds=60)
    Path(moody).write_text(Path(moody).read_text() + "\n# edited\n")

    status = c.run(budget_seconds=60)
    assert status["counts"] == {OK: 4} and status["run"]["finished"] == 4      # recomputed
    assert status["set_aside"] == 4 and "never mixed in" in status["note"]


def test_a_coordinator_that_stopped_beating_is_interrupted(moody, tmp_path, monkeypatch):
    c = campaign(moody, tmp_path)
    c._claim_store()
    c._write_json("coordinator.json", {"state": "running", "pid": 1, "heartbeat": time.time() - 100})
    assert c.status()["state"] == "interrupted"


# ==============================================================================
# Detached, and killed
# ==============================================================================

def wait_for(check, seconds=60.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.3)
    return False


@pytest.mark.skipif(os.name == "nt", reason="kills with SIGKILL")
def test_a_killed_campaign_resumes_where_it_stopped(moody, tmp_path, monkeypatch):
    """The maintainer's requirement, done to a real campaign: kill -9, then start again."""
    c = campaign(moody, tmp_path, grid={"mode": ["slow"]}, seeds=range(20), workers=2, point_timeout=10)
    assert c.start(budget_seconds=120)["started"] is True
    assert wait_for(lambda: c.status()["counts"].get(OK, 0) >= 4)

    with pytest.raises(CampaignError, match="already running"):
        c.start(budget_seconds=120)

    os.kill(c.status()["pid"], signal.SIGKILL)               # nothing gets to clean up
    monkeypatch.setattr(sweep, "STALE_AFTER_SECONDS", 2.0)
    assert wait_for(lambda: c.status()["state"] == "interrupted", 10)
    kept = c.status()["counts"][OK]

    c.start(budget_seconds=120)
    assert wait_for(lambda: c.status()["state"] == "finished")
    assert c.status()["counts"] == {OK: 20}
    assert len(Path(c.store, "points.jsonl").read_text().splitlines()) == 20      # none twice
    assert kept < 20


def test_the_detached_entry_point_runs_a_stored_campaign(moody, tmp_path):
    from smartmdao._campaign import main

    c = campaign(moody, tmp_path)
    c._claim_store()
    assert main([c.store, "--budget", "60", "--settings", json.dumps({"workers": 1})]) == 0
    assert c.status()["counts"] == {OK: 4}


def test_the_detached_entry_point_reports_a_refusal(moody, tmp_path, capsys):
    from smartmdao._campaign import main

    c = campaign(moody, tmp_path, seed_input="unread")
    c._claim_store()
    assert main([c.store, "--budget", "60", "--retry-failed"]) == 1
    assert "campaign refused" in capsys.readouterr().err


# ==============================================================================
# Aggregates say what they left out
# ==============================================================================

def test_aggregates_give_intervals_and_count_what_they_excluded(moody, tmp_path):
    c = campaign(moody, tmp_path, grid={"mode": ["ok", "raise"]}, seeds=range(10))
    c.run(budget_seconds=60)
    groups = {g["params"]["mode"]: g for g in c.aggregate()["groups"]}

    value = groups["ok"]["outputs"]["value"]
    assert value["n"] == 10 and value["mean"] == pytest.approx(9.0)
    assert value["ci_half_width"] == pytest.approx(2.262 * value["std"] / 10 ** 0.5, rel=1e-3)
    assert "label" not in groups["ok"]["outputs"]            # not a number
    assert groups["raise"]["outputs"] == {} and groups["raise"]["excluded"] == {ERROR: 10}


def test_one_point_has_no_interval(moody, tmp_path):
    c = campaign(moody, tmp_path, seeds=[3])
    c.run(budget_seconds=60)
    value = c.aggregate()["groups"][0]["outputs"]["value"]
    assert value == {"n": 1, "mean": 6.0, "std": 0.0, "ci_half_width": None}


def test_nothing_run_aggregates_to_pending(moody, tmp_path):
    assert campaign(moody, tmp_path).aggregate()["groups"][0]["excluded"] == {"pending": 4}


# ==============================================================================
# The worker, in-process (the child process is not measured by coverage)
# ==============================================================================

def test_the_worker_builds_inputs_from_the_project(tmp_path):
    write(tmp_path / "baseline.py", BASELINE)
    entry = write(tmp_path / "built.py", BUILT)
    state, ready = _sweeper.prepare({"path": str(entry), "outputs": ["area"], "inputs_from": "baseline:inputs",
                                     "inputs_kwargs": {"scale": 0.5}, "seed_input": "seed"})
    assert ready["ready"] is True and len(ready["model_hash"]) == 64

    record = _sweeper.run_point(state, {"key": "k", "params": {"scenario": "large"}, "seed": 1})
    assert record["status"] == OK and record["outputs"] == {"area": 7.0}      # (4 * 0.5) * 3 + 1


def test_the_model_hash_covers_the_inputs_from_module(tmp_path):
    write(tmp_path / "baseline.py", BASELINE)
    entry = write(tmp_path / "built.py", BUILT)
    setup = {"path": str(entry), "outputs": ["area"], "inputs_from": "baseline:inputs"}
    before = _sweeper.prepare(setup)[1]["model_hash"]
    write(tmp_path / "baseline.py", BASELINE + "\n# edited\n")
    assert _sweeper.prepare(setup)[1]["model_hash"] != before


def test_an_inputs_from_module_that_cannot_be_found_hashes_without_it(tmp_path):
    entry = write(tmp_path / "built.py", BUILT)
    assert _sweeper._inputs_from_file("nowhere.at.all:inputs", entry) == ()
    assert _sweeper._inputs_from_file(None, entry) == ()


@pytest.mark.parametrize("setup, says", [
    ({"path": "missing.py"}, "No such file"),
    ({"outputs": ["nope"]}, "No step produces"),
])
def test_the_worker_refuses_what_it_cannot_load(moody, setup, says):
    state, reply = _sweeper.prepare({"path": moody, **setup})
    assert state is None and says in reply["error"]


def test_the_worker_records_what_it_cannot_do(moody):
    state, _ = _sweeper.prepare({"path": moody, "outputs": ["value"], "seed_input": "seed"})
    too_big = _sweeper.run_point(state, {"key": "k", "params": {"mode": "ok"}, "seed": 2 ** 60})
    assert too_big["status"] == ERROR and "too large" in too_big["error"]


def test_a_loop_that_does_not_settle_is_not_converged(tmp_path):
    entry = write(tmp_path / "loop.py", """
        from smartmdao import Pipeline, IterativeSolver
        pipeline = Pipeline(solver=IterativeSolver(max_iterations=2), inputs=["a", "y"])
        @pipeline.step(outputs=["x"])
        def one(a: float, y: float) -> float:
            return a + y
        @pipeline.step(outputs=["y"])
        def two(x: float) -> float:
            return x + 1.0
    """)
    state, _ = _sweeper.prepare({"path": str(entry), "outputs": ["x"]})
    record = _sweeper.run_point(state, {"key": "k", "params": {"a": 1.0, "y": 0.0}})
    assert record["status"] == NOT_CONVERGED and record["reason"]


def test_outputs_are_stored_as_json_or_refused_with_a_reason():
    import numpy as np

    assert _sweeper.to_json(np.float64(2.5), "x") == 2.5
    assert _sweeper.to_json(np.arange(3), "x") == [0, 1, 2]
    assert _sweeper.to_json({"a": (1, 2)}, "x") == {"a": [1, 2]}
    for value, says in ((np.zeros(20_000), "reduce it"), (list(range(20_000)), "reduce it"), (object(), "cannot store")):
        with pytest.raises(_sweeper.OutputNotStorable, match=says):
            _sweeper.to_json(value, "x")


# ==============================================================================
# Over MCP
# ==============================================================================

def test_sweep_pipeline_quotes_by_default_and_needs_a_budget_to_start(moody, tmp_path):
    from smartmdao.mcp.campaigns import sweep_pipeline

    store = str(tmp_path / "mcp")
    quote = sweep_pipeline(moody, store, grid={"mode": ["ok"]}, seeds=3, seed_input="seed", outputs=["value"], workers=1)
    assert quote["ok"] is True and quote["quote"]["points"] == 3               # seeds=3 means 3 realizations

    refused = sweep_pipeline(moody, store, grid={"mode": ["ok"]}, seeds=3, seed_input="seed", outputs=["value"], start=True)
    assert refused["refused"] == "budget"

    assert sweep_pipeline(moody, store, grid={"mode": ["ok"]}, seeds=[1], seed_input="seed")["ok"] is False


def test_sweep_status_and_stop_over_mcp(moody, tmp_path):
    from smartmdao.mcp.campaigns import sweep_pipeline, sweep_status, sweep_stop

    store = str(tmp_path / "mcp")
    started = sweep_pipeline(moody, store, grid={"mode": ["ok"]}, seeds=[1, 2], seed_input="seed",
                             outputs=["value"], workers=1, start=True, budget_seconds=60)
    assert started["ok"] is True and started["started"] is True
    assert wait_for(lambda: sweep_status(store)["state"] == "finished")

    status = sweep_status(store)
    assert status["counts"] == {OK: 2} and status["aggregate"]["groups"][0]["outputs"]["value"]["n"] == 2
    assert sweep_stop(store)["stopping"] is True
    assert sweep_status(str(tmp_path / "none"))["ok"] is False
    assert sweep_stop(str(tmp_path / "none"))["ok"] is False


# ==============================================================================
# Paths the child processes take, exercised in-process
# ==============================================================================

def test_the_worker_refuses_effects_and_an_unread_seed_in_process(tmp_path, moody):
    model = write(tmp_path / "effects.py", """
        from smartmdao import Pipeline
        pipeline = Pipeline(inputs=["a"])
        @pipeline.step(outputs=["b"], effects=True)
        def send(a: float) -> float:
            return a
    """)
    assert _sweeper.prepare({"path": str(model), "outputs": ["b"]})[1]["refused"] == "side-effects"
    assert _sweeper.prepare({"path": moody, "outputs": ["value"], "seed_input": "unread"})[1]["refused"] == "seed"


def test_a_quoted_point_that_hangs_is_timed_out(moody, tmp_path):
    quote = campaign(moody, tmp_path, grid={"mode": ["hang"]}, seeds=[1], point_timeout=1).quote()
    assert quote["point"]["status"] == TIMED_OUT


def test_a_stop_request_during_a_long_point(moody, tmp_path, monkeypatch):
    """Asked while a point is running, not between two: that point still finishes."""
    import threading

    monkeypatch.setattr(sweep, "HEARTBEAT_SECONDS", 0.05)
    c = campaign(moody, tmp_path, grid={"mode": ["slow"]}, seeds=range(4), point_timeout=10)
    c._claim_store()
    real_send = sweep._Pool._send

    def send_then_stop(self, slot, entry, key):
        real_send(self, slot, entry, key)
        threading.Timer(0.1, c.stop).start()                  # arrives mid-point

    monkeypatch.setattr(sweep._Pool, "_send", send_then_stop)
    result = c.run(budget_seconds=60)
    assert result["run"]["state"] == "stopped: request" and result["counts"][OK] == 1


def test_a_detached_start_can_retry_failed_points(moody, tmp_path):
    c = campaign(moody, tmp_path, grid={"mode": ["raise"]}, seeds=[1])
    c.run(budget_seconds=60)
    c.start(budget_seconds=60, retry_failed=True)
    assert wait_for(lambda: c.status()["state"] == "finished")
    assert len(Path(c.store, "points.jsonl").read_text().splitlines()) == 2    # tried again


def test_the_campaign_tools_through_the_protocol(moody, tmp_path):
    import asyncio

    from smartmdao.mcp import create_server

    def call(name, arguments):
        result = asyncio.run(create_server().call_tool(name, arguments))
        assert result.is_error is False
        return json.loads(result.content[0].text)

    missing = str(tmp_path / "none")
    assert "No campaign" in call("sweep_status", {"store": missing})["error"]
    assert "No campaign" in call("sweep_stop", {"store": missing})["error"]
    assert "needs a grid" in call("sweep_pipeline", {"path": moody, "store": missing})["error"]
