"""``plan-release`` limits a release to the commits touching ``paths`` when a caller names them.

Every step that reads the range — the plan, the changelog and the notes — gets
the input through the ``RELEASE_PATHS`` variable, which the scripts read as the
default of ``--paths``, so the scripts callers always ran stay byte for byte the
same and an empty input changes nothing. The steps run here as the runner runs
them, with a ``gh`` stub.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

COMPOSITE = Path(__file__).parents[2] / "actions" / "release" / "plan-release"
RULES = '[tool.semantic_branch]\nminor = ["feat/.*"]\npatch = ["fix/.*"]\n'
SCOPED = ("Plan the release", "Update the changelog", "Write the release notes")


def _action() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load((COMPOSITE / "action.yml").read_text("utf-8")))


def _step(name: str) -> dict[str, Any]:
    return next(step for step in _action()["runs"]["steps"] if step["name"] == name)


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments), check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _touch(repository: Path, path: str, message: str) -> str:
    (repository / path).parent.mkdir(parents=True, exist_ok=True)
    (repository / path).write_text(message, encoding="utf-8")
    _git(repository, "add", path)
    _git(repository, "commit", "-qm", message)
    return _git(repository, "rev-parse", "HEAD")


def _monorepo(tmp_path: Path) -> tuple[Path, str, str]:
    """Return a monorepo where app-a was released, then app-a and app-b each changed."""
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "-q", "--initial-branch=master")
    _git(repository, "config", "user.email", "release@example.com")
    _git(repository, "config", "user.name", "Release")
    _touch(repository, "apps/app-a/pyproject.toml", RULES)
    _git(repository, "tag", "app-a/v0.1.0")
    mine = _touch(repository, "apps/app-a/src.py", "fix(app-a): one")
    return repository, mine, _touch(repository, "apps/app-b/src.py", "fix(app-b): two")


def _gh(bin_dir: Path) -> str:
    """Put a ``gh`` on PATH that names a ``fix/`` branch for every commit."""
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    answer = '[{"number": 1, "title": "chore: change", "head_ref": "fix/change", "merged": true}]'
    gh.write_text(f"#!/usr/bin/env bash\necho '{answer}'\n", encoding="utf-8")
    gh.chmod(0o755)
    return f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


def _run(
    name: str, tmp_path: Path, repository: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    script = tmp_path / f".{name.replace(' ', '_')}.sh"
    script.write_text(cast(str, _step(name)["run"]), encoding="utf-8")
    return subprocess.run(
        ("bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)),
        cwd=repository,
        env={"HOME": os.environ.get("HOME", str(tmp_path)), **env},
        capture_output=True,
        text=True,
        check=False,
    )


def _env(tmp_path: Path, merge: str, paths: str) -> dict[str, str]:
    return {
        "PATH": _gh(tmp_path / "bin"),
        "MERGE_SHA": merge,
        "REPOSITORY_SLUG": "o/r",
        "NOTES_OUTPUT": "NOTES.md",
        "GH_TOKEN": "unused",
        "SEMANTIC_BRANCH_CONFIG": "apps/app-a/pyproject.toml",
        "TAG_PREFIX": "app-a/v",
        "RELEASE_PATHS": paths,
        "ACTION_PATH": str(COMPOSITE),
        "GITHUB_OUTPUT": str(tmp_path / "outputs.txt"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
    }


class TestTheContract:
    def test_the_input_is_optional_and_empty(self) -> None:
        declared = _action()["inputs"]["paths"]
        assert declared["required"] is False
        assert declared["default"] == ""

    def test_every_step_reading_the_range_gets_the_paths_through_the_environment(
        self,
    ) -> None:
        for name in SCOPED:
            assert _step(name)["env"]["RELEASE_PATHS"] == "${{ inputs.paths }}", name
            assert "paths" not in cast(str, _step(name)["run"]), name


class TestTheSteps:
    def test_a_package_plans_and_writes_notes_from_its_own_commits_only(
        self, tmp_path: Path
    ) -> None:
        repository, mine, merge = _monorepo(tmp_path)
        env = _env(tmp_path, merge, "apps/app-a\nuv.lock")

        planned = _run("Plan the release", tmp_path, repository, env)
        assert planned.returncode == 0, planned.stderr
        written = _run("Write the release notes", tmp_path, repository, {**env, "VERSION": "0.1.1"})
        assert written.returncode == 0, written.stderr

        assert (tmp_path / "outputs.txt").read_text("utf-8") == "version=0.1.1\npart=patch\n"
        assert planned.stdout.endswith(f"ships    :\n  {mine[:8]}  patch  fix/change\n")
        notes = (repository / "NOTES.md").read_text("utf-8")
        assert notes.endswith("Changes since app-a/v0.1.0:\n\n- fix(app-a): one\n")

    def test_empty_paths_ship_every_commit_as_before(self, tmp_path: Path) -> None:
        repository, _, merge = _monorepo(tmp_path)

        planned = _run("Plan the release", tmp_path, repository, _env(tmp_path, merge, ""))

        assert planned.returncode == 0, planned.stderr
        assert planned.stdout.count("fix/change") == 2

    def test_no_commit_touching_the_paths_is_nothing_to_release(self, tmp_path: Path) -> None:
        repository, _, merge = _monorepo(tmp_path)

        planned = _run("Plan the release", tmp_path, repository, _env(tmp_path, merge, "apps/c"))

        assert planned.returncode != 0
        assert planned.stderr == (
            "release plan failed: nothing to release: "
            "no commits touching apps/c since app-a/v0.1.0\n"
        )
        assert not (tmp_path / "outputs.txt").exists()

    @pytest.mark.parametrize("paths", ["/etc", "apps/../..", "--all", ":(top)"])
    def test_an_unsafe_path_fails_before_anything_is_written(
        self, tmp_path: Path, paths: str
    ) -> None:
        repository, _, merge = _monorepo(tmp_path)

        planned = _run("Plan the release", tmp_path, repository, _env(tmp_path, merge, paths))

        assert planned.returncode != 0
        assert f"release plan failed: path '{paths}' is not allowed" in planned.stderr
        assert not (tmp_path / "outputs.txt").exists()
