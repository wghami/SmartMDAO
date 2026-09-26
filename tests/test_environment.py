"""
Each pipeline in its own project's environment. Roadmap 6.6, design record 007.

Three layers:

- `environment.resolve`: which interpreter a file belongs to, on fake
  environments (a `bin/python` file and a `dist-info` directory are all the
  resolver looks at, and it never starts them).
- the worker protocol (`_worker.handle`) and the client's failure handling
  (`worker.call`), the latter against small fake interpreters.
- the real thing: a separate project with its own `.venv` and a library the
  server's environment lacks, analysed, validated, rendered and run.
"""
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from smartmdao.mcp import environment, handlers, worker
from smartmdao.mcp._worker import PROTOCOL, handle
from smartmdao.mcp.environment import (
    DISCOVERED, EXPLICIT, MIN_PROJECT_VERSION, PROJECT, SERVER,
    Interpreter, installed_version, queried_version, resolve, version_tuple,
)

REPO = Path(__file__).resolve().parents[1]
POSIX = pytest.mark.skipif(os.name == "nt", reason="fake interpreters are shell scripts")


def fake_env(root: Path, version=MIN_PROJECT_VERSION, windows=False) -> Path:
    """A directory shaped like a virtual environment, never executed."""
    env = root / ".venv"
    if windows:
        (env / "Scripts").mkdir(parents=True)
        (env / "Scripts" / "python.exe").write_text("")
        site = env / "Lib" / "site-packages"
    else:
        (env / "bin").mkdir(parents=True)
        (env / "bin" / "python").write_text("")
        site = env / "lib" / "python3.11" / "site-packages"
    site.mkdir(parents=True)
    if version:
        (site / f"smartmdao-{version}.dist-info").mkdir()
    return env


def project(root: Path, **env) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text("[project]\nname = 'p'\n")
    fake_env(root, **env)
    (root / "model.py").write_text("")
    return root / "model.py"


# ==============================================================================
# Which interpreter
# ==============================================================================

def test_a_file_outside_any_project_uses_the_server(tmp_path):
    interpreter, refusal = resolve(tmp_path / "loose.py")
    assert refusal is None
    assert interpreter.source == SERVER and interpreter.is_server


def test_a_file_in_this_repository_uses_the_server_without_a_worker():
    """Its .venv IS the server's environment - compared by directory, not by binary."""
    interpreter, _ = resolve(REPO / "scripts" / "sellar_benchmark_mda.py")
    assert interpreter.source == SERVER


def test_a_project_with_its_own_environment_is_discovered(tmp_path):
    interpreter, _ = resolve(project(tmp_path / "proj"))
    assert interpreter.source == DISCOVERED
    assert interpreter.smartmdao == MIN_PROJECT_VERSION
    assert not interpreter.is_server


def test_the_nearest_project_with_a_venv_wins(tmp_path):
    outer = project(tmp_path / "outer", version="9.0.0")
    inner = tmp_path / "outer" / "sub"
    inner.mkdir()
    (inner / "pyproject.toml").write_text("")                  # no .venv: skipped
    interpreter, _ = resolve(inner / "model.py")
    assert interpreter.smartmdao == "9.0.0"
    assert Path(interpreter.environment) == outer.parent / ".venv"


def test_the_windows_layout_is_recognised(tmp_path):
    interpreter, _ = resolve(project(tmp_path / "win", windows=True))
    assert interpreter.path.endswith("python.exe")
    assert interpreter.smartmdao == MIN_PROJECT_VERSION


@pytest.mark.parametrize("version, says", [
    (None, "has no SmartMDAO"),
    ("1.24.0", "has SmartMDAO 1.24.0, below"),
])
def test_a_discovered_environment_that_cannot_serve_falls_back_with_a_note(tmp_path, version, says):
    """Refusing would break a project the day the server was upgraded."""
    interpreter, refusal = resolve(project(tmp_path / "old", version=version))
    assert refusal is None
    assert interpreter.source == SERVER
    assert says in interpreter.note


@pytest.mark.parametrize("version, kind", [(None, "no-smartmdao"), ("1.24.0", "version")])
def test_a_named_environment_that_cannot_serve_is_refused(tmp_path, version, kind):
    """The caller said which environment: substituting another would be a guess."""
    root = tmp_path / "named"
    project(root, version=version)
    _, refusal = resolve(root / "model.py", project=str(root))
    assert refusal["refused"] == kind
    assert refusal["ok"] is False


def test_a_named_project_is_used(tmp_path):
    root = tmp_path / "named"
    project(root)
    interpreter, _ = resolve("/elsewhere/model.py", project=str(root))
    assert interpreter.source == PROJECT


def test_a_named_project_without_a_venv_is_refused(tmp_path):
    _, refusal = resolve("x.py", project=str(tmp_path))
    assert refusal["refused"] == "interpreter" and "has no .venv" in refusal["error"]


def test_an_explicit_interpreter_that_does_not_exist_is_refused(tmp_path):
    _, refusal = resolve("x.py", python=str(tmp_path / "nope"))
    assert refusal["refused"] == "interpreter"


def test_an_explicit_interpreter_is_used_and_its_venv_read(tmp_path):
    env = fake_env(tmp_path)
    interpreter, _ = resolve("x.py", python=str(env / "bin" / "python"))
    assert interpreter.source == EXPLICIT
    assert interpreter.smartmdao == MIN_PROJECT_VERSION


def test_naming_the_servers_own_interpreter_is_the_server():
    interpreter, _ = resolve("x.py", python=sys.executable)
    assert interpreter.source == SERVER


def test_the_description_leaves_out_what_is_unknown():
    assert "note" not in Interpreter("p", "e", SERVER, "1.0.0").describe()


@pytest.mark.parametrize("text, expected", [
    ("1.26.0", (1, 26, 0)), ("1.26.0rc1", (1, 26, 0)), ("1.26.0+local.3", (1, 26, 0)), ("2.0", (2, 0)),
])
def test_version_tuples(text, expected):
    assert version_tuple(text) == expected


def test_no_dist_info_means_no_version(tmp_path):
    assert installed_version(fake_env(tmp_path, version=None)) is None


def test_an_interpreter_can_be_asked_its_version():
    """For an explicit interpreter outside a venv, where there is no dist-info to read."""
    assert queried_version(Path(sys.executable)) == handlers.resolve("x.py")[0].smartmdao


@POSIX
def test_asking_an_interpreter_that_cannot_answer_gives_nothing(tmp_path):
    silent = script(tmp_path / "silent", "exit 0")
    slow = script(tmp_path / "slow", "sleep 5")
    assert queried_version(silent) is None
    assert queried_version(slow, timeout=0.2) is None
    assert queried_version(tmp_path / "missing") is None


@POSIX
def test_an_explicit_interpreter_outside_a_venv_is_asked(tmp_path):
    answering = script(tmp_path / "bin" / "python", f"echo {MIN_PROJECT_VERSION}")
    interpreter, _ = resolve("x.py", python=str(answering))
    assert interpreter.smartmdao == MIN_PROJECT_VERSION


# ==============================================================================
# The worker's side of the protocol
# ==============================================================================

def test_the_worker_answers_in_a_shared_protocol(tmp_path):
    source = tmp_path / "m.py"
    source.write_text("from smartmdao import Pipeline\npipeline = Pipeline()\n")
    response = handle({"protocol": [1, 5], "op": "analyze", "args": {"path": str(source)}})
    assert response["protocol"] == PROTOCOL[1]
    assert response["result"]["ok"] is True
    assert response["smartmdao"]


@pytest.mark.parametrize("asked", [[PROTOCOL[1] + 1, PROTOCOL[1] + 3], None, ["a", "b"]])
def test_the_worker_refuses_without_a_common_protocol(asked):
    response = handle({"protocol": asked, "op": "analyze", "args": {}})
    assert response["error"]["kind"] == "protocol"
    assert response["error"]["supported"] == list(PROTOCOL)


def test_the_worker_refuses_an_unknown_op():
    assert handle({"protocol": list(PROTOCOL), "op": "optimize"})["error"]["kind"] == "op"


# ==============================================================================
# The server's side: every failure is data
# ==============================================================================

def script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\ncat > /dev/null\n{body}\n")
    path.chmod(0o755)
    return path


def foreign(path: Path) -> Interpreter:
    return Interpreter(str(path), str(path.parent), EXPLICIT, MIN_PROJECT_VERSION)


@POSIX
@pytest.mark.parametrize("body, expect", [
    ("sleep 5", "did not answer within"),
    ("echo 'python: No module named smartmdao.mcp._worker' >&2; exit 1", "cannot run the worker"),
    ("echo boom >&2; exit 3", "died with exit code 3"),
    ("echo not-json", "not JSON"),
    ('echo \'{"protocol": null, "error": {"kind": "protocol", "message": "no common protocol"}}\'',
     "no common protocol"),
])
def test_a_worker_that_fails_is_reported_not_raised(tmp_path, body, expect):
    result, version = worker.call(foreign(script(tmp_path / "python", body)), "analyze", {}, timeout=0.5)
    assert result["ok"] is False
    assert expect in result["error"]
    assert version is None


def test_an_interpreter_that_cannot_start_is_reported(tmp_path):
    missing = foreign(tmp_path / "gone")
    result, _ = worker.call(missing, "analyze", {}, timeout=5)
    assert "Could not start" in result["error"]


@POSIX
def test_what_the_users_code_printed_comes_back(tmp_path):
    body = 'echo "a progress line" >&2; echo \'{"protocol": 1, "smartmdao": "9.9.9", "result": {"ok": true}}\''
    result, version = worker.call(foreign(script(tmp_path / "python", body)), "analyze", {}, timeout=5)
    assert result == {"ok": True, "stderr": "a progress line\n"}
    assert version == "9.9.9"


def test_a_refused_environment_is_refused_by_every_tool(tmp_path):
    root = tmp_path / "named"
    project(root, version="1.24.0")
    for call in (handlers.analyze_pipeline, handlers.validate_pipeline, handlers.explain_pipeline):
        assert call(str(root / "model.py"), project=str(root))["refused"] == "version"


# ==============================================================================
# Output that would have corrupted a protocol
# ==============================================================================

PRINTING = """
from smartmdao import Pipeline
print("loading model")

pipeline = Pipeline(inputs=["a"])

@pipeline.step(outputs=["b"])
def double(a: float) -> float:
    print("computing", a)
    return a * 2
"""


def test_a_file_that_prints_while_loading_does_not_write_to_stdout(tmp_path, capfd):
    """Over MCP, stdout is the JSON-RPC stream: the client logged a parse failure."""
    from smartmdao.mcp.loader import load_pipeline

    source = tmp_path / "printing.py"
    source.write_text(PRINTING)
    load_pipeline(source)
    out, err = capfd.readouterr()
    assert "loading model" not in out
    assert "loading model" in err


def test_a_factory_that_prints_does_not_write_to_stdout(tmp_path, capfd):
    from smartmdao.mcp.loader import load_pipeline

    source = tmp_path / "factory.py"
    source.write_text(
        "from smartmdao import Pipeline\n"
        "def build() -> Pipeline:\n"
        "    print('building')\n"
        "    return Pipeline()\n"
    )
    load_pipeline(source)
    assert "building" not in capfd.readouterr().out


def test_a_discipline_that_prints_no_longer_breaks_a_run(tmp_path):
    """Until 1.26.0 any print() in a discipline made the run's answer 'not valid JSON'."""
    source = tmp_path / "printing.py"
    source.write_text(PRINTING)
    result = handlers.run_pipeline(str(source), inputs={"a": 2.0}, rung="full")

    assert result["ok"] is True, result.get("error")
    assert result["state"]["b"] == 4.0
    assert "computing 2.0" in result["stderr"]


# ==============================================================================
# The real thing
# ==============================================================================

@pytest.fixture(scope="module")
def foreign_project(tmp_path_factory):
    """
    A project with its own .venv holding SmartMDAO from this checkout and a
    library, `onlyhere`, that the server's environment does not have.
    """
    uv = shutil.which("uv")
    if uv is None:                                   # pragma: no cover - uv is how this repo is run
        pytest.skip("needs uv to build a second environment")

    root = tmp_path_factory.mktemp("foreign")
    library = root / "lib"
    (library / "onlyhere").mkdir(parents=True)
    (library / "pyproject.toml").write_text(textwrap.dedent("""
        [project]
        name = "onlyhere"
        version = "0.1.0"
        [build-system]
        requires = ["hatchling"]
        build-backend = "hatchling.build"
    """))
    (library / "onlyhere" / "__init__.py").write_text("def scale(x):\n    return 3 * x\n")

    proj = root / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname = 'proj'\nversion = '0.1.0'\n")
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    subprocess.run([uv, "venv", "-q", str(proj / ".venv"), "--python", python], check=True)
    subprocess.run(
        [uv, "pip", "install", "-q", "--python", str(environment.venv_python(proj / ".venv")),
         str(REPO), str(library)],
        check=True,
    )
    (proj / "model.py").write_text(textwrap.dedent("""
        from onlyhere import scale          # not installed where the server runs
        from smartmdao import Pipeline

        pipeline = Pipeline(inputs=["a"])

        @pipeline.step(outputs=["b"])
        def triple(a: float) -> float:
            return scale(a)
    """))
    return proj / "model.py"


def test_the_server_really_lacks_the_library():
    with pytest.raises(ImportError):
        import onlyhere  # noqa: F401


def test_a_pipeline_needing_another_environment_is_analysed(foreign_project):
    report = handlers.analyze_pipeline(str(foreign_project))

    assert report["ok"] is True, report.get("error")
    assert report["execution_order"] == ["triple"]
    assert report["interpreter"]["source"] == DISCOVERED
    assert report["interpreter"]["smartmdao"]


def test_validated_and_explained_there_too(foreign_project):
    assert handlers.validate_pipeline(str(foreign_project))["valid"] is True
    assert "triple" in handlers.explain_pipeline(str(foreign_project))["explanation"]


def test_rendered_there_too(foreign_project, tmp_path):
    report = handlers.render_pipeline_diagram(str(foreign_project), str(tmp_path / "xdsm.png"))
    assert report["ok"] is True
    assert Path(report["output_path"]).stat().st_size > 0


def test_and_run_there(foreign_project):
    result = handlers.run_pipeline(str(foreign_project), inputs={"a": 2.0}, rung="full")

    assert result["ok"] is True, result.get("error")
    assert result["state"]["b"] == 6.0
    assert result["interpreter"]["source"] == DISCOVERED


def test_naming_the_interpreter_reports_it_as_explicit(foreign_project):
    python = environment.venv_python(foreign_project.parent / ".venv")
    report = handlers.analyze_pipeline(str(foreign_project), python=str(python))
    assert report["interpreter"]["source"] == EXPLICIT


def test_a_file_that_hangs_while_loading_there_is_killed(foreign_project, monkeypatch):
    """Analysing imports the file; a hang at import must not hang the server."""
    hanging = foreign_project.parent / "hangs.py"
    hanging.write_text("import time\nfrom smartmdao import Pipeline\ntime.sleep(30)\npipeline = Pipeline()\n")
    monkeypatch.setattr(worker, "ANALYSIS_TIMEOUT_SECONDS", 3.0)

    report = handlers.analyze_pipeline(str(hanging))

    assert report["ok"] is False
    assert report["timed_out"] is True
    assert report["interpreter"]["source"] == DISCOVERED
