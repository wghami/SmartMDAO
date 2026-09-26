"""
The documentation gate in tools/docs_gate.py, which CI, the git pre-push hook
and the Claude Code session hooks all call.

Tested against a real throwaway repository rather than mocks: the gate's whole
job is reading a git diff correctly, and a mocked diff would test the mock.
"""
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
GATE = REPO / "tools" / "docs_gate.py"

spec = importlib.util.spec_from_file_location("docs_gate", GATE)
docs_gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(docs_gate)


# ==============================================================================
# The rule, which is pure
# ==============================================================================

def test_no_library_change_needs_no_docs():
    assert docs_gate.check(["tests/test_x.py", "README.md"]).ok


def test_a_library_change_without_docs_fails():
    verdict = docs_gate.check(["smartmdao/core.py"])
    assert not verdict.ok
    assert "smartmdao/core.py" in verdict.explain()
    assert "docs/roadmap.md" in verdict.explain()        # the checklist is shown


def test_a_library_change_with_any_doc_passes():
    assert docs_gate.check(["smartmdao/core.py", "docs/known-issues.md"]).ok


def test_readme_or_notebooks_alone_do_not_count_as_docs():
    """The Definition of Done says docs/, and the gate says the same thing."""
    assert not docs_gate.check(["smartmdao/core.py", "README.md", "notebooks/01.ipynb"]).ok


@pytest.mark.parametrize("line", [
    "Docs: not needed — pure refactor, no behaviour change",
    "Docs: not needed - pure refactor, no behaviour change",
    "Docs: not needed -- pure refactor, no behaviour change",
    "  docs: NOT needed – typo fix in a log message",
])
def test_a_waiver_with_a_reason_passes(line):
    verdict = docs_gate.check(["smartmdao/core.py"], f"Some PR body\n\n{line}\n")
    assert verdict.ok
    assert verdict.waiver


@pytest.mark.parametrize("line", [
    "Docs: not needed",                 # no reason at all
    "Docs: not needed — n/a",           # a reason too short to be one
    "Docs are not needed for this",     # not the marker
])
def test_a_waiver_without_a_real_reason_does_not(line):
    """An empty waiver is the same as no gate."""
    assert not docs_gate.check(["smartmdao/core.py"], line).ok


def test_the_explanation_truncates_a_long_list():
    verdict = docs_gate.check([f"smartmdao/m{i}.py" for i in range(15)])
    assert "and 5 more" in verdict.explain()


# ==============================================================================
# The callers, against a real repository
# ==============================================================================

def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    """main with one commit, then a feature branch checked out."""
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "smartmdao").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "smartmdao" / "core.py").write_text("x = 1\n")
    (tmp_path / "docs" / "roadmap.md").write_text("# Roadmap\n")
    (tmp_path / "pyproject.toml").write_text('version = "1.0.0"\n')
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")
    git(tmp_path, "tag", "v1.0.0")
    git(tmp_path, "checkout", "-q", "-b", "feature")
    return tmp_path


def run(repo, *flags, stdin="", env=None):
    import os
    return subprocess.run(
        [sys.executable, str(GATE), *flags], cwd=repo, input=stdin,
        capture_output=True, text=True, env={**os.environ, **(env or {})},
    )


def commit(repo, path, text, message="change"):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def test_ci_fails_a_library_only_branch(repo):
    commit(repo, "smartmdao/core.py", "x = 2\n")
    result = run(repo, "--ci", "--base", "main")
    assert result.returncode == 1
    assert "::error title=Docs gate::" in result.stdout


def test_ci_passes_when_the_pr_body_waives_it(repo):
    commit(repo, "smartmdao/core.py", "x = 2\n")
    body = "Refactor.\n\nDocs: not needed — internal rename, no behaviour change\n"
    assert run(repo, "--ci", "--base", "main", env={"PR_BODY": body}).returncode == 0


def test_ci_passes_when_docs_changed(repo):
    commit(repo, "smartmdao/core.py", "x = 2\n")
    commit(repo, "docs/roadmap.md", "# Roadmap\n- [x] done\n")
    assert run(repo, "--ci", "--base", "main").returncode == 0


def test_ci_fails_rather_than_passing_when_git_cannot_read_the_diff(repo):
    """CI is the enforcing layer; it must never wave a PR through on an error."""
    result = run(repo, "--ci", "--base", "no-such-ref")
    assert result.returncode == 1
    assert "git failed" in result.stdout


def test_a_local_hook_does_not_wedge_the_session_on_a_git_error(repo):
    result = run(repo, "--claude-stop", "--base", "no-such-ref", stdin="{}")
    assert result.returncode == 0
    assert "not enforcing here" in result.stderr


def test_pre_push_accepts_a_waiver_in_a_commit_message(repo):
    commit(repo, "smartmdao/core.py", "x = 2\n",
           message="refactor\n\nDocs: not needed — renames a private helper only")
    assert run(repo, "--pre-push", "--base", "main").returncode == 0


def test_pre_push_blocks_a_library_only_push(repo):
    commit(repo, "smartmdao/core.py", "x = 2\n")
    result = run(repo, "--pre-push", "--base", "main")
    assert result.returncode == 1
    assert "no file under docs/ did" in result.stderr


def test_stop_hook_sees_uncommitted_work(repo):
    """Work in progress is not committed yet, and that is when it is forgotten."""
    (repo / "smartmdao" / "core.py").write_text("x = 3\n")
    result = run(repo, "--claude-stop", "--base", "main", stdin="{}")
    assert result.returncode == 2
    assert "Documentation reminder" in result.stderr


def test_stop_hook_sees_untracked_files(repo):
    (repo / "smartmdao" / "new_module.py").write_text("y = 1\n")
    assert run(repo, "--claude-stop", "--base", "main", stdin="{}").returncode == 2


def test_stop_hook_reminds_once_then_lets_the_turn_end(repo):
    """stop_hook_active is set when the turn continues because of this hook."""
    (repo / "smartmdao" / "core.py").write_text("x = 3\n")
    payload = json.dumps({"stop_hook_active": True})
    assert run(repo, "--claude-stop", "--base", "main", stdin=payload).returncode == 0


def test_stop_hook_is_silent_when_docs_moved_too(repo):
    (repo / "smartmdao" / "core.py").write_text("x = 3\n")
    (repo / "docs" / "roadmap.md").write_text("# Roadmap\n- [x] done\n")
    assert run(repo, "--claude-stop", "--base", "main", stdin="{}").returncode == 0


def test_stop_hook_reminds_to_tag_an_untagged_version_on_main(repo):
    git(repo, "checkout", "-q", "main")
    commit(repo, "pyproject.toml", 'version = "1.1.0"\n')
    result = run(repo, "--claude-stop", "--base", "main", stdin="{}")
    assert result.returncode == 2
    assert "v1.1.0" in result.stderr


def test_an_untagged_version_on_a_branch_is_expected(repo):
    """Tags go on the merge commit, so a bumped branch is untagged until merged."""
    commit(repo, "pyproject.toml", 'version = "1.1.0"\n')
    commit(repo, "docs/roadmap.md", "# Roadmap\n- 1.1.0\n")
    assert run(repo, "--claude-stop", "--base", "main", stdin="{}").returncode == 0


def test_pretool_ignores_commands_that_are_not_pr_creation(repo):
    commit(repo, "smartmdao/core.py", "x = 2\n")
    payload = json.dumps({"tool_input": {"command": "git status"}})
    assert run(repo, "--claude-pretool", "--base", "main", stdin=payload).returncode == 0


def test_pretool_blocks_pr_creation_without_docs(repo):
    commit(repo, "smartmdao/core.py", "x = 2\n")
    payload = json.dumps({"tool_input": {"command": 'gh pr create --title t --body "fixes"'}})
    result = run(repo, "--claude-pretool", "--base", "main", stdin=payload)
    assert result.returncode == 2
    assert "would not pass the CI docs gate" in result.stderr


def test_pretool_reads_a_waiver_in_the_inline_body(repo):
    commit(repo, "smartmdao/core.py", "x = 2\n")
    command = ('gh pr create --title t --body "$(cat <<EOF\nRefactor.\n\n'
               'Docs: not needed — private rename, nothing user-visible\nEOF\n)"')
    payload = json.dumps({"tool_input": {"command": command}})
    assert run(repo, "--claude-pretool", "--base", "main", stdin=payload).returncode == 0


def test_pretool_reads_a_waiver_in_a_body_file(repo):
    commit(repo, "smartmdao/core.py", "x = 2\n")
    body = repo / "body.md"
    body.write_text("Docs: not needed — only a comment in the source changed\n")
    payload = json.dumps({"tool_input": {"command": f"gh pr create --title t --body-file {body}"}})
    assert run(repo, "--claude-pretool", "--base", "main", stdin=payload).returncode == 0
