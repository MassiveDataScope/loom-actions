"""With ``changelog-verify`` the plan checks the CHANGELOG the release commit holds.

A release through an App never commits to its base branch: the person commits
the section on the release branch, and ``plan`` fails, before any write, unless
the merge commit holds exactly the section the planner would write. The
expected file is built on the CHANGELOG of the merge commit's first parent,
``MERGE_SHA^1``, which does not list the version yet. Built on the merge commit
or on the base branch after the merge, it would already list the version, the
planner would keep that section, and ``verify`` would compare the section with
itself and always pass (security review of L-a, H1). So the step taking the
first parent's file refuses one that lists the version already.

Here the step scripts run on a real repository, the expected file is built
with the planner's own ``build_changelog`` and ``verify`` runs the
``commit_changelog.py`` the pinned composite runs, against a ``gh`` serving the
merged file.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import gh_stub
import pytest
import workflow_steps as wf
from gh_expressions import evaluate

ROOT = Path(__file__).parents[2]
SRC = ROOT / "actions" / "release" / "plan-release" / "src"
sys.path.insert(0, str(SRC))

from changelog_file import build_changelog  # noqa: E402
from release_history import PullRequest  # noqa: E402
from release_scope import ReleaseScope  # noqa: E402

NAME = "release-on-label"
COMPOSITE = "MassiveDataScope/loom-actions/actions/release/plan-release"
VERIFY_PIN = "6cd8d478598e1f45dab1824b148de8c8d3742df6"
URL = "https://github.com/o/r"
WHEN = "2026-11-11T10:00:00+00:00"
VERSION = "0.2.0"
BASE_CHANGELOG = (
    "# Changelog\n\n"
    "All notable changes to this project will be documented in this file.\n\n"
    "The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),\n"
    "and this project adheres to "
    "[Semantic Versioning](https://semver.org/spec/v2.0.0.html).\n\n"
    "## [Unreleased]\n\n"
    "## [0.1.0] - 2026-11-01\n\n### Added\n\n- first\n\n"
    f"[unreleased]: {URL}/compare/v0.1.0...HEAD\n"
    f"[0.1.0]: {URL}/releases/tag/v0.1.0\n"
)


def _step(step_id: str) -> dict[str, Any]:
    return next(s for s in wf.steps(NAME, "plan") if s.get("id") == step_id)


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_COMMITTER_DATE": WHEN, "GIT_AUTHOR_DATE": WHEN},
    ).stdout.strip()


def _commit(repository: Path, path: str, text: str, message: str) -> str:
    (repository / path).parent.mkdir(parents=True, exist_ok=True)
    (repository / path).write_text(text, "utf-8")
    _git(repository, "add", path)
    _git(repository, "commit", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


class Release:
    """A repository where release/0.2.0 merged into master, holding its section."""

    def __init__(self, tmp_path: Path, changelog: str = "CHANGELOG.md") -> None:
        self.path, self.changelog = tmp_path / "repo", changelog
        self.path.mkdir()
        _git(self.path, "init", "--initial-branch=master")
        _git(self.path, "config", "user.email", "release@example.com")
        _git(self.path, "config", "user.name", "Release")
        _commit(self.path, changelog, BASE_CHANGELOG, "chore: initial")
        _git(self.path, "tag", "v0.1.0")
        _git(self.path, "checkout", "-b", "release/0.2.0")
        feature = _commit(self.path, "src/x.py", "x = 1\n", "feat: add x")
        self.pulls = {feature: (PullRequest(2, "feat: add x", "release/0.2.0", True),)}
        # What the person commits on the release branch: the preview's section.
        self.expected = self._build(feature, changelog)
        _commit(self.path, changelog, self.expected, "docs: changelog for 0.2.0")
        _git(self.path, "checkout", "master")
        _git(self.path, "merge", "--no-ff", "-m", "Merge release/0.2.0", "release/0.2.0")
        self.merge = _git(self.path, "rev-parse", "HEAD")
        self.pulls[self.merge] = self.pulls[feature]

    def _build(self, sha: str, changelog: str) -> str:
        return build_changelog(
            self.path,
            sha,
            VERSION,
            lambda commit: self.pulls.get(commit, ()),
            changelog_file=changelog,
            scope=ReleaseScope("v"),
            repository_url=URL,
        ).text

    def take_first_parent(self, merge: str | None = None) -> tuple[Any, dict[str, str]]:
        output = self.path / ".github-output"
        output.write_text("", "utf-8")
        env = {
            "MERGE_SHA": merge or self.merge,
            "PACKAGE_DIR": str(Path(self.changelog).parent),
            "VERSION": VERSION,
            "GITHUB_OUTPUT": str(output),
        }
        result = wf.run(NAME, "plan", _step("changelog-base")["name"], env, self.path)
        lines = output.read_text("utf-8").splitlines()
        return result, dict(line.split("=", 1) for line in lines)

    def build_on(self, built_file: str) -> str:
        """What changelog-build leaves: the planner run on the first parent's file."""
        update = build_changelog(
            self.path,
            self.merge,
            VERSION,
            lambda commit: self.pulls.get(commit, ()),
            changelog_file=built_file,
            scope=ReleaseScope("v"),
            repository_url=URL,
        )
        assert update.changed, "the first parent's file must not list the version"
        (self.path / built_file).write_text(update.text, "utf-8")
        return update.text

    def verify(self, tmp_path: Path, built_file: str, merged: str) -> Any:
        """Run verify as the pinned composite does, the ref serving *merged*."""
        content = base64.b64encode(merged.encode()).decode()
        stub = gh_stub.install(
            tmp_path,
            [
                (
                    "contents/",
                    [gh_stub.answer({"encoding": "base64", "content": content, "sha": "f"})],
                )
            ],
        )
        return subprocess.run(
            (
                sys.executable,
                str(SRC / "commit_changelog.py"),
                "--slug=o/r",
                "--branch=master",
                f"--changelog-file={self.changelog}",
                f"--built={self.path / built_file}",
                f"--version={VERSION}",
                "--tag-prefix=v",
                "--mode=verify",
                f"--ref={self.merge}",
                "--out=",
            ),
            capture_output=True,
            text=True,
            env={**os.environ, **stub.env},
            check=False,
        )


class TestTheWiring:
    @pytest.mark.parametrize("step_id", ["changelog-base", "changelog-build", "verify-changelog"])
    def test_it_runs_only_with_changelog_verify_and_a_version(self, step_id: str) -> None:
        condition = _step(step_id)["if"]
        assert condition == "${{ inputs.changelog-verify && steps.plan.outputs.version != '' }}"
        context = {"inputs.changelog-verify": True, "steps.plan.outputs.version": ""}
        assert evaluate(condition, context) is False

    def test_it_verifies_with_l_a_pinned(self) -> None:
        step = _step("verify-changelog")
        assert step["uses"] == f"{COMPOSITE}/commit-changelog@{VERIFY_PIN}"
        assert step["with"] == {
            "mode": "verify",
            "ref": "${{ env.MERGE_SHA }}",
            "changelog-file": "${{ steps.changelog-base.outputs.changelog-file }}",
            "built-file": "${{ steps.changelog-base.outputs.built-file }}",
            "version": "${{ steps.plan.outputs.version }}",
            "tag-prefix": "${{ inputs.tag-prefix }}",
            "base-branch": "${{ inputs.base-branch }}",
            "repository-slug": "${{ github.repository }}",
            "github-token": "${{ secrets.GITHUB_TOKEN }}",
        }

    def test_the_expected_file_is_built_by_the_pinned_planner_on_the_first_parent(self) -> None:
        build, planner = _step("changelog-build"), _step("plan")
        assert build["uses"] == planner["uses"]
        assert build["with"] == planner["with"] | {
            "changelog-file": "${{ steps.changelog-base.outputs.built-file }}",
            "notes-output": "${{ steps.changelog-base.outputs.notes-file }}",
        }

    def test_it_runs_after_the_planner_and_before_any_write(self) -> None:
        ids = [s.get("id") for s in wf.steps(NAME, "plan")]
        titles = [s.get("name") for s in wf.steps(NAME, "plan")]
        tag = titles.index("Create the immutable version tag")
        order = [
            ids.index(i) for i in ("plan", "changelog-base", "changelog-build", "verify-changelog")
        ]
        assert order == sorted(order)
        assert order[-1] < tag

    def test_the_checkout_holds_the_first_parent(self) -> None:
        checkout = next(s for s in wf.steps(NAME, "plan") if "checkout" in str(s.get("uses")))
        assert checkout["with"]["fetch-depth"] == 0


class TestTheFirstParent:
    @pytest.mark.parametrize("changelog", ["CHANGELOG.md", "apps/api/CHANGELOG.md"])
    def test_it_takes_the_file_before_the_merge(self, tmp_path: Path, changelog: str) -> None:
        release = Release(tmp_path, changelog)
        result, outputs = release.take_first_parent()
        assert result.returncode == 0, result.stdout + result.stderr
        assert outputs["changelog-file"] == changelog
        built = outputs["built-file"]
        assert built != changelog
        assert (release.path / built).read_text("utf-8") == BASE_CHANGELOG
        assert outputs["notes-file"]

    def test_it_refuses_a_first_parent_that_lists_the_version(self, tmp_path: Path) -> None:
        """The planner on such a tree keeps the merged section, and verify proves nothing."""
        release = Release(tmp_path)
        after = _commit(release.path, "src/y.py", "y = 1\n", "fix: y")
        result, _ = release.take_first_parent(merge=after)
        assert result.returncode == 1
        assert f"already lists {VERSION}" in result.stdout

    def test_without_the_guard_verify_would_pass_a_tampered_file(self, tmp_path: Path) -> None:
        """H1: built on the merge commit, the expected section is the merged one."""
        release = Release(tmp_path)
        tampered = release.expected.replace("add x", "add y")
        (release.path / "CHANGELOG.md").write_text(tampered, "utf-8")
        built = build_changelog(
            release.path,
            release.merge,
            VERSION,
            lambda commit: release.pulls.get(commit, ()),
            changelog_file="CHANGELOG.md",
            scope=ReleaseScope("v"),
            repository_url=URL,
        )
        assert built.changed is False
        assert built.text == tampered

    def test_a_first_parent_without_a_changelog_builds_one(self, tmp_path: Path) -> None:
        release = Release(tmp_path)
        _git(release.path, "rm", "-q", "CHANGELOG.md")
        _git(release.path, "commit", "-q", "-m", "chore: drop")
        merge = _commit(release.path, "src/z.py", "z\n", "fix: z")
        result, outputs = release.take_first_parent(merge=merge)
        assert result.returncode == 0, result.stdout + result.stderr
        assert not (release.path / outputs["built-file"]).exists()
        assert (release.path / outputs["built-file"]).parent.is_dir()


class TestTheVerification:
    def _built(self, tmp_path: Path) -> tuple[Release, str]:
        release = Release(tmp_path)
        result, outputs = release.take_first_parent()
        assert result.returncode == 0, result.stdout + result.stderr
        release.build_on(outputs["built-file"])
        return release, outputs["built-file"]

    def test_the_merged_section_the_planner_would_write_passes(self, tmp_path: Path) -> None:
        release, built = self._built(tmp_path)
        result = release.verify(tmp_path, built, release.expected)
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.parametrize(
        "tamper",
        [
            pytest.param(lambda text: text.replace("add x", "add y"), id="altered-entry"),
            pytest.param(
                lambda text: text.replace(
                    "## [0.2.0]", "## [0.2.0] - 2026-11-11\n\n### Added\n\n- extra\n\n## [0.2.0]", 1
                ),
                id="duplicated-section",
            ),
            pytest.param(
                lambda text: text.replace(
                    "[0.2.0]: https://github.com/o/r/", "[0.2.0]: https://evil.example/o/r/"
                ),
                id="foreign-link",
            ),
        ],
    )
    def test_a_tampered_merged_changelog_fails(self, tmp_path: Path, tamper: Any) -> None:
        release, built = self._built(tmp_path)
        tampered = tamper(release.expected)
        assert tampered != release.expected
        result = release.verify(tmp_path, built, tampered)
        assert result.returncode == 1
        assert "changelog verify failed" in result.stderr


def test_the_expected_file_never_reaches_the_release() -> None:
    """The verified file lives aside: the upload and the notes keep the planner's files."""
    upload = next(s for s in wf.steps(NAME, "plan") if s.get("name") == "Store release notes")
    assert upload["with"]["path"] == "CHANGELOG_RELEASE.md"
    script = _step("changelog-base")["run"]
    assert ".release-verify" in script
    assert json.dumps(_step("notes")).count("CHANGELOG_RELEASE.md") == 1
