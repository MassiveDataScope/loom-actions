"""``plan-release/preview`` runs the release's planner and changelog on a pull request.

The composite takes the pull request from the ``pull_request`` event by default,
passes every input to the script through ``env:`` only, and appends one package
to a Markdown file a caller posts with ``core/pr-comment-update``. Its step is
run here as the runner runs it, with a ``gh`` stub.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from workflow_steps import steps

ROOT = Path(__file__).parents[2]
PREVIEW_DIR = ROOT / "actions" / "release" / "plan-release" / "preview"
STEP = "Preview the release"


def _action() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load((PREVIEW_DIR / "action.yml").read_text("utf-8")))


def _step() -> dict[str, Any]:
    steps = cast(list[dict[str, Any]], _action()["runs"]["steps"])
    return next(s for s in steps if s["name"] == STEP)


def _git(repository: Path, *arguments: str) -> str:
    env = {**os.environ, "GIT_COMMITTER_DATE": "2026-10-04T10:25:43Z"}
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed.stdout.strip()


def _commit(repository: Path, path: str, message: str) -> None:
    target = repository / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(message, "utf-8")
    _git(repository, "add", path)
    _git(repository, "commit", "-qm", message)


def _test_merge(tmp_path: Path) -> tuple[Path, str]:
    """Return a repository and the test merge of a feat/ branch touching apps/api."""
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "-q", "--initial-branch=master")
    _git(repository, "config", "user.email", "release@example.com")
    _git(repository, "config", "user.name", "Release")
    _commit(repository, "apps/api/pyproject.toml", '[tool.semantic_branch]\nminor = ["feat/.*"]\n')
    _git(repository, "tag", "api/v1.0.0")
    _git(repository, "checkout", "-qb", "feat/one")
    _commit(repository, "apps/api/one.py", "wip")
    _git(repository, "checkout", "-q", "--detach", "master")
    _git(repository, "merge", "-q", "--no-ff", "-m", "Merge feat/one into master", "feat/one")
    return repository, _git(repository, "rev-parse", "HEAD")


def _stub_gh(tmp_path: Path) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!/usr/bin/env bash\necho '{json.dumps([])}'\n", "utf-8")
    gh.chmod(0o755)
    return f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


class TestTheInterface:
    def test_the_pull_request_comes_from_the_event_by_default(self) -> None:
        inputs = _action()["inputs"]
        assert inputs["merge-sha"]["default"] == "${{ github.sha }}"
        assert inputs["pull-request-number"]["default"] == (
            "${{ github.event.pull_request.number }}"
        )
        assert inputs["pull-request-title"]["default"] == "${{ github.event.pull_request.title }}"
        assert inputs["head-ref"]["default"] == "${{ github.event.pull_request.head.ref }}"
        assert inputs["repository-slug"]["default"] == "${{ github.repository }}"

    def test_the_package_inputs_are_the_planners(self) -> None:
        inputs = _action()["inputs"]
        assert inputs["changelog-file"]["required"] is True
        assert inputs["tag-prefix"]["default"] == "v"
        assert inputs["paths"]["default"] == ""
        assert inputs["semantic-branch-config"]["default"] == "pyproject.toml"
        assert inputs["github-token"]["required"] is True

    def test_it_outputs_the_file_the_version_and_whether_the_release_would_fail(self) -> None:
        outputs = _action()["outputs"]
        assert outputs["output"]["value"] == "${{ inputs.output }}"
        assert outputs["version"]["value"] == "${{ steps.preview.outputs.version }}"
        assert outputs["failed"]["value"] == "${{ steps.preview.outputs.failed }}"

    def test_inputs_reach_the_script_through_the_environment_only(self) -> None:
        step = _step()
        assert "${{" not in step["run"]
        assert step["env"]["PULL_REQUEST_TITLE"] == "${{ inputs.pull-request-title }}"
        assert step["env"]["HEAD_REF"] == "${{ inputs.head-ref }}"
        assert step["env"]["RELEASE_PATHS"] == "${{ inputs.paths }}"
        assert step["env"]["GH_TOKEN"] == "${{ inputs.github-token }}"

    def test_values_that_could_start_with_a_dash_are_passed_attached(self) -> None:
        run = _step()["run"]
        for option in ("merge-sha", "pull-request-title", "head-ref", "tag-prefix"):
            assert f"--{option}=" in run, option

    def test_it_runs_no_other_action(self) -> None:
        assert all("uses" not in s for s in _action()["runs"]["steps"])


class TestTheStep:
    def test_it_appends_the_preview_and_writes_the_outputs(self, tmp_path: Path) -> None:
        repository, merge = _test_merge(tmp_path)
        github_output = tmp_path / "github_output"
        script = tmp_path / "step.sh"
        script.write_text(cast(str, _step()["run"]), encoding="utf-8")
        env = {
            "HOME": os.environ.get("HOME", str(tmp_path)),
            "PATH": _stub_gh(tmp_path),
            "GH_TOKEN": "token",
            "MERGE_SHA": merge,
            "REPOSITORY_SLUG": "acme/repo",
            "PULL_REQUEST_NUMBER": "4",
            "PULL_REQUEST_TITLE": "feat(api): add one",
            "HEAD_REF": "feat/one",
            "SEMANTIC_BRANCH_CONFIG": "apps/api/pyproject.toml",
            "TAG_PREFIX": "api/v",
            "RELEASE_PATHS": "apps/api",
            "CHANGELOG_FILE": "apps/api/CHANGELOG.md",
            "OUTPUT": "release-preview.md",
            "ACTION_PATH": str(PREVIEW_DIR),
            "GITHUB_OUTPUT": str(github_output),
            "GITHUB_SERVER_URL": "https://github.com",
        }

        result = subprocess.run(
            ("bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)),
            cwd=repository,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert github_output.read_text("utf-8") == "version=1.1.0\nfailed=false\n"
        preview = (repository / "release-preview.md").read_text("utf-8")
        assert preview.startswith("## Release preview\n")
        assert "### `api/v`: `api/v1.1.0` (minor)" in preview
        assert "- **api:** add one ([#4](https://github.com/acme/repo/pull/4))" in preview


# The plan-release release-on-label runs today: v1.11.0. The preview matches the
# release when both run the same plan-release source; this composite ships after
# that pin, so a follow-up moves release-on-label to the release that ships it and
# updates RELEASED_PLANNER and empties DIVERGED_SINCE_PIN.
RELEASED_PLANNER = "aa40fd0bd251822a2907475a0a8140495f6cb98e"
# src/ files that differ from the pinned planner: the preview itself, an optional
# date for the changelog section, commit_parents, and the command-line arguments
# the scripts now declare once in release_scope. None changes the version or the
# section a release computes; any other difference must be looked at.
DIVERGED_SINCE_PIN = {
    "changelog_file.py",
    "plan_release.py",
    "preview_changelog.py",
    "release_history.py",
    "release_scope.py",
}


class TestThePlannerTheReleaseRuns:
    def test_release_on_label_pins_the_documented_planner(self) -> None:
        uses = [
            s["uses"]
            for s in steps("release-on-label", "plan") + steps("release-on-label", "release")
            if "plan-release" in s.get("uses", "")
        ]
        assert uses
        assert {use.split("@")[1].split()[0] for use in uses} == {RELEASED_PLANNER}

    def test_only_the_documented_files_differ_from_the_pinned_planner(self) -> None:
        source = "actions/release/plan-release/src"
        known = subprocess.run(
            ("git", "-C", str(ROOT), "cat-file", "-e", f"{RELEASED_PLANNER}^{{commit}}"),
            capture_output=True,
            check=False,
        )
        if known.returncode != 0:
            pytest.skip(f"{RELEASED_PLANNER} is not in this shallow checkout")
        diff = subprocess.run(
            ("git", "-C", str(ROOT), "diff", "--name-only", RELEASED_PLANNER, "--", source),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()

        assert {Path(path).name for path in diff} == DIVERGED_SINCE_PIN
