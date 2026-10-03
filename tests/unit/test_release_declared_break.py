"""loom-actions' own release raises a declared break to a major, as plan-release does.

The ``prepare-release-pr`` job of ``release.yml`` takes the part from the merged
branch name. A commit since the last release that declares a break, by a ``!``
before the colon of its subject or a ``BREAKING CHANGE:`` footer, must raise it
to a major. The steps are run as the runner runs them, against a throwaway
repository holding loom-actions' own branch rules.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import workflow_steps as wf

ROOT = Path(__file__).parents[2]
PLANNER_SRC = ROOT / "actions" / "release" / "plan-release" / "src"
VERSIONING_CLI = ROOT / "actions" / "release" / "versioning-branch-semantic" / "src" / "cli.py"
VERSIONING_ACTION = ROOT / "actions" / "release" / "versioning-branch-semantic" / "action.yml"

JOB = "prepare-release-pr"
DETECT = "Detect a declared break since the last release"
VERSION = "Compute version from branch semantic rules"


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments), check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _released_repository(tmp_path: Path) -> Path:
    """Return a repository released as v1.10.0 under loom-actions' own rules."""
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=master")
    _git(repository, "config", "user.email", "release@example.com")
    _git(repository, "config", "user.name", "Release")
    rules = (ROOT / "pyproject.toml").read_text("utf-8").split("[tool.semantic_branch]", 1)[1]
    (repository / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "1.10.0"\n\n[tool.semantic_branch]{rules}', "utf-8"
    )
    _git(repository, "add", "pyproject.toml")
    _git(repository, "commit", "-m", "chore(release): prepare v1.10.0 [automated]")
    _git(repository, "tag", "v1.10.0")
    return repository


def _merge(repository: Path, message: str) -> str:
    (repository / "change.txt").write_text(message, "utf-8")
    _git(repository, "add", "change.txt")
    _git(repository, "commit", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


def _detect(repository: Path, merge_sha: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    outputs = tmp_path / "outputs.txt"
    outputs.write_text("", "utf-8")
    env = {"MERGE_SHA": merge_sha, "PLANNER_SRC": str(PLANNER_SRC), "GITHUB_OUTPUT": str(outputs)}
    return wf.run("release", JOB, DETECT, env, repository)


def _released_version(repository: Path, branch: str, message: str, tmp_path: Path) -> str:
    merge_sha = _merge(repository, message)
    detected = _detect(repository, merge_sha, tmp_path)
    assert detected.returncode == 0, detected.stderr
    breaking = (tmp_path / "outputs.txt").read_text("utf-8").strip().removeprefix("breaking=")
    computed = subprocess.run(
        (
            sys.executable,
            str(VERSIONING_CLI),
            "--branch",
            branch,
            "--prerelease",
            "false",
            "--config",
            "pyproject.toml",
            "--semantic-branch-config",
            "",
            "--breaking",
            breaking,
        ),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return computed.stdout.splitlines()[0].removeprefix("version=")


class TestWiring:
    def test_the_break_is_read_before_the_version_is_computed(self) -> None:
        titles = [s.get("name") for s in wf.steps("release", JOB)]
        assert titles.index(DETECT) < titles.index(VERSION)

    def test_the_break_reaches_the_version_step(self) -> None:
        version = wf.step("release", JOB, VERSION)
        assert version["uses"] == "./actions/release/versioning-branch-semantic"
        assert version["with"]["breaking"] == "${{ steps.break.outputs.breaking }}"

    def test_the_step_reads_the_merge_commit_and_the_shared_planner(self) -> None:
        detect = wf.step("release", JOB, DETECT)
        assert detect["id"] == "break"
        assert detect["env"] == {
            "MERGE_SHA": "${{ github.event.pull_request.merge_commit_sha }}",
            "PLANNER_SRC": "actions/release/plan-release/src",
        }

    def test_the_composite_passes_the_break_to_its_cli(self) -> None:
        text = VERSIONING_ACTION.read_text("utf-8")
        assert "BREAKING: ${{ inputs.breaking }}" in text
        assert '--breaking "${BREAKING}"' in text


class TestReleasedVersion:
    def test_a_feature_branch_ships_a_minor(self, tmp_path: Path) -> None:
        repository = _released_repository(tmp_path)
        version = _released_version(
            repository, "feature/x", "feat(release-on-label): add it (#63)", tmp_path
        )
        assert version == "1.11.0"

    @pytest.mark.parametrize(
        "message",
        [
            "feat(release-on-label)!: per-app tag prefix (#63)",
            "feat!: per-app tag prefix",
        ],
    )
    def test_a_feature_branch_with_a_marked_subject_ships_a_major(
        self, tmp_path: Path, message: str
    ) -> None:
        repository = _released_repository(tmp_path)
        assert _released_version(repository, "feature/x", message, tmp_path) == "2.0.0"

    @pytest.mark.parametrize("footer", ["BREAKING CHANGE", "BREAKING-CHANGE"])
    def test_a_fix_branch_with_a_break_footer_ships_a_major(
        self, tmp_path: Path, footer: str
    ) -> None:
        repository = _released_repository(tmp_path)
        message = f"fix: drop the parameter\n\n{footer}: callers pass it no more"
        assert _released_version(repository, "fix/x", message, tmp_path) == "2.0.0"

    def test_a_fix_branch_without_a_break_ships_a_patch(self, tmp_path: Path) -> None:
        repository = _released_repository(tmp_path)
        message = "fix: say BREAKING CHANGE in prose only"
        assert _released_version(repository, "fix/x", message, tmp_path) == "1.10.1"

    def test_a_break_merged_since_the_last_release_still_counts(self, tmp_path: Path) -> None:
        repository = _released_repository(tmp_path)
        _merge(repository, "feat!: drop the old input")
        assert _released_version(repository, "fix/x", "fix: repair it", tmp_path) == "2.0.0"

    def test_a_break_already_released_does_not_count_again(self, tmp_path: Path) -> None:
        repository = _released_repository(tmp_path)
        _merge(repository, "feat!: drop the old input")
        project = repository / "pyproject.toml"
        project.write_text(project.read_text("utf-8").replace("1.10.0", "2.0.0", 1), "utf-8")
        _git(repository, "commit", "-am", "chore(release): prepare v2.0.0 [automated]")
        _git(repository, "tag", "v2.0.0")
        assert _released_version(repository, "fix/x", "fix: repair it", tmp_path) == "2.0.1"

    def test_a_merge_sha_that_does_not_exist_fails_the_step(self, tmp_path: Path) -> None:
        repository = _released_repository(tmp_path)
        detected = _detect(repository, "0" * 40, tmp_path)
        assert detected.returncode != 0
        assert "break detection failed" in detected.stderr
