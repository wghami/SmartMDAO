"""
tools/release.py, which CI runs on every push to main after the tests pass.

`gh` is replaced by a script on PATH that records its arguments, so nothing
here touches GitHub; git is real, in a throwaway repository.
"""
import importlib.util
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools" / "release.py"

spec = importlib.util.spec_from_file_location("release", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.mark.parametrize("tag_exists, release_exists, expected", [
    (False, False, release.TAG_AND_RELEASE),
    (True, False, release.RELEASE_ONLY),
    (True, True, release.NOTHING),
])
def test_the_policy(tag_exists, release_exists, expected):
    assert release.decide(tag_exists, release_exists) == expected


def test_a_pyproject_without_a_version_is_an_error(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    with pytest.raises(SystemExit, match="no version"):
        release.version_in(tmp_path / "pyproject.toml")


# ==============================================================================
# End to end, with a fake gh
# ==============================================================================

@pytest.fixture
def repo(tmp_path):
    work = tmp_path / "repo"
    work.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.com"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=work, check=True)
    (work / "pyproject.toml").write_text('[project]\nversion = "2.0.0"\n')
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=work, check=True)
    return work


def fake_gh(tmp_path, released: bool):
    """A `gh` that logs its arguments; `release view` succeeds only if `released`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "gh.log"
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{log}"\n'
        f'if [ "$2" = "view" ]; then exit {0 if released else 1}; fi\n'
        "exit 0\n"
    )
    gh.chmod(0o755)
    return bin_dir, log


def run(repo, bin_dir, *flags):
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
           "GITHUB_OUTPUT": str(repo / "github_output")}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--sha", "abc123", *flags],
        cwd=repo, env=env, capture_output=True, text=True,
    )


def test_a_new_version_is_tagged_at_the_commit_and_released(repo, tmp_path):
    bin_dir, log = fake_gh(tmp_path, released=False)
    result = run(repo, bin_dir)

    assert result.returncode == 0
    created = log.read_text().splitlines()[-1]
    assert created.startswith("release create v2.0.0")
    assert "--target abc123" in created
    assert "--verify-tag" not in created


def test_an_existing_tag_is_released_without_moving_it(repo, tmp_path):
    subprocess.run(["git", "tag", "v2.0.0"], cwd=repo, check=True)
    bin_dir, log = fake_gh(tmp_path, released=False)
    run(repo, bin_dir)

    created = log.read_text().splitlines()[-1]
    assert "--verify-tag" in created
    assert "--target" not in created


def test_nothing_happens_once_released(repo, tmp_path):
    subprocess.run(["git", "tag", "v2.0.0"], cwd=repo, check=True)
    bin_dir, log = fake_gh(tmp_path, released=True)
    result = run(repo, bin_dir)

    assert "nothing to do" in result.stdout
    assert not any("create" in line for line in log.read_text().splitlines())


def test_a_dry_run_creates_nothing(repo, tmp_path):
    bin_dir, log = fake_gh(tmp_path, released=False)
    result = run(repo, bin_dir, "--dry-run")

    assert "tag-and-release" in result.stdout
    assert not any("create" in line for line in log.read_text().splitlines())


# ==============================================================================
# What the publish job is told
# ==============================================================================

def said(repo):
    return (repo / "github_output").read_text().strip()


def test_a_new_release_tells_the_publish_job(repo, tmp_path):
    bin_dir, _ = fake_gh(tmp_path, released=False)
    run(repo, bin_dir)
    assert said(repo) == "released=true"


@pytest.mark.parametrize("released, flags", [(True, ()), (False, ("--dry-run",))])
def test_nothing_new_publishes_nothing(repo, tmp_path, released, flags):
    subprocess.run(["git", "tag", "v2.0.0"], cwd=repo, check=True)
    bin_dir, _ = fake_gh(tmp_path, released=released)
    run(repo, bin_dir, *flags)
    assert said(repo) == "released=false"


def test_a_failed_release_publishes_nothing(repo, tmp_path):
    bin_dir, _ = fake_gh(tmp_path, released=False)
    gh = bin_dir / "gh"
    gh.write_text(gh.read_text().replace("exit 0\n", "exit 1\n"))
    assert run(repo, bin_dir).returncode == 1
    assert said(repo) == "released=false"
