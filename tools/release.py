#!/usr/bin/env python3
"""
Keep the version, the git tag and the GitHub Release in step.

Run by CI on every push to main, after the tests pass. If the version in
pyproject.toml has no `v<version>` tag, it tags the commit and creates the
Release; if the tag exists but no Release does, it creates the Release; if
both exist, it does nothing. Bumping the version in a PR is the only manual
step left.

Why it exists: the rule "tag the merge commit and push the tag" depended on
memory, and memory lost - GitHub's latest Release stayed at v1.14.0 through
nine tagged versions, and v1.7.0 to v1.13.0 were never tagged at all. The docs
cite behaviour by release ("fixed in 1.12.0"), and downstream projects pin by
tag, so a version that is not a release is a reference nobody can check.

Standard library plus the `gh` CLI, which every GitHub runner has.

    tools/release.py --sha <commit>            act
    tools/release.py --sha <commit> --dry-run  say what it would do
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

NOTHING = "nothing"
RELEASE_ONLY = "release"
TAG_AND_RELEASE = "tag-and-release"


def decide(tag_exists: bool, release_exists: bool) -> str:
    """The whole policy, pure so it can be tested without GitHub."""
    if release_exists:
        return NOTHING
    return RELEASE_ONLY if tag_exists else TAG_AND_RELEASE


def version_in(pyproject: Path) -> str:
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M)
    if not match:
        raise SystemExit(f"release: no version in {pyproject}")
    return match.group(1)


def _succeeds(*command: str) -> bool:
    return subprocess.run(command, capture_output=True).returncode == 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Tag and release the version on main, if it is new.")
    parser.add_argument("--sha", required=True, help="the commit a new tag should point at")
    parser.add_argument("--pyproject", default="pyproject.toml")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    tag = f"v{version_in(Path(args.pyproject))}"
    tag_exists = _succeeds("git", "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}")
    release_exists = _succeeds("gh", "release", "view", tag)
    action = decide(tag_exists, release_exists)

    if action == NOTHING:
        print(f"{tag} is already tagged and released; nothing to do.")
        return 0

    # `gh release create` makes the tag on GitHub when it does not exist yet,
    # pointing at --target; --verify-tag refuses to invent one when it should.
    command = ["gh", "release", "create", tag, "--title", tag, "--generate-notes"]
    command += ["--verify-tag"] if action == RELEASE_ONLY else ["--target", args.sha]

    print(f"{tag}: {action} -> {' '.join(command)}")
    if args.dry_run:
        return 0
    return subprocess.run(command).returncode


if __name__ == "__main__":
    sys.exit(main())
