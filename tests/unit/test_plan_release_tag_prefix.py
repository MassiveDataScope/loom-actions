"""``plan-release`` plans and writes notes from the tags of one release line.

Its ``tag-prefix`` input, ``v`` by default, reaches both planner runs and the
notes through the environment. The composite's own scripts are run here as the
runner runs them, over a repository holding the tags of two packages, with a
``gh`` on PATH that answers the pull request of every commit.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

COMPOSITE = Path(__file__).parents[2] / "actions" / "release" / "plan-release"
FLAG = '--tag-prefix="${TAG_PREFIX}"'
RULES = '[tool.semantic_branch]\nminor = ["feat/.*"]\npatch = ["fix/.*"]\n'


def _action() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load((COMPOSITE / "action.yml").read_text("utf-8")))


def _step(step_id: str | None, name: str | None = None) -> dict[str, Any]:
    return next(
        step
        for step in _action()["runs"]["steps"]
        if (step_id and step.get("id") == step_id) or (name and step.get("name") == name)
    )


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments), check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _commit(repository: Path, message: str) -> str:
    (repository / message.replace(":", "_").replace(" ", "_")).write_text("x", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", message)
    return _git(repository, "rev-parse", "HEAD")


def _monorepo(tmp_path: Path) -> tuple[Path, str]:
    """Return a repository where the api and the worker each tagged a release."""
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "-q", "--initial-branch=master")
    _git(repository, "config", "user.email", "release@example.com")
    _git(repository, "config", "user.name", "Release")
    (repository / "pyproject.toml").write_text(RULES, encoding="utf-8")
    _commit(repository, "chore: initial")
    _git(repository, "tag", "api-v0.4.0")
    _git(repository, "tag", "v1.0.0")
    _commit(repository, "fix: worker")
    _git(repository, "tag", "worker-v2.0.0")
    _git(repository, "tag", "v2.0.0")
    return repository, _commit(repository, "feat: api")


def _gh(bin_dir: Path) -> str:
    """Put a ``gh`` on PATH that names a ``feat/`` branch for every commit."""
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("#!/usr/bin/env bash\necho feat/change\n", encoding="utf-8")
    gh.chmod(0o755)
    return f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


def _run(
    step: dict[str, Any], tmp_path: Path, repository: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    script = tmp_path / f".{step['name'].replace(' ', '_')}.sh"
    script.write_text(cast(str, step["run"]), encoding="utf-8")
    return subprocess.run(
        ("bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)),
        cwd=repository,
        env={"HOME": os.environ.get("HOME", str(tmp_path)), **env},
        capture_output=True,
        text=True,
        check=False,
    )


def _release(tmp_path: Path, prefix: str | None) -> tuple[str, str, str, str]:
    """Run both steps; return the outputs, the summary, the notes and the step output."""
    repository, merge = _monorepo(tmp_path)
    outputs, summary = tmp_path / "outputs.txt", tmp_path / "summary.md"
    env = {
        "PATH": _gh(tmp_path / "bin"),
        "MERGE_SHA": merge,
        "REPOSITORY_SLUG": "o/r",
        "NOTES_OUTPUT": "NOTES.md",
        "GH_TOKEN": "unused",
        "SEMANTIC_BRANCH_CONFIG": "pyproject.toml",
        "TAG_PREFIX": prefix
        if prefix is not None
        else _action()["inputs"]["tag-prefix"]["default"],
        "ACTION_PATH": str(COMPOSITE),
        "GITHUB_OUTPUT": str(outputs),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    planned = _run(_step("plan"), tmp_path, repository, env)
    assert planned.returncode == 0, planned.stderr
    version = dict(line.split("=", 1) for line in outputs.read_text("utf-8").splitlines())
    notes_step = _step(None, "Write the release notes")
    written = _run(notes_step, tmp_path, repository, {**env, "VERSION": version["version"]})
    assert written.returncode == 0, written.stderr
    notes = (repository / "NOTES.md").read_text("utf-8")
    return outputs.read_text("utf-8"), summary.read_text("utf-8"), notes, planned.stdout


class TestTheContract:
    def test_the_prefix_is_optional_and_v_by_default(self) -> None:
        declared = _action()["inputs"]["tag-prefix"]
        assert declared["required"] is False
        assert declared["default"] == "v"

    def test_both_planner_runs_and_the_notes_read_the_same_prefix(self) -> None:
        plan = _step("plan")
        notes = _step(None, "Write the release notes")
        for step in (plan, notes):
            assert step["env"]["TAG_PREFIX"] == "${{ inputs.tag-prefix }}"
        assert cast(str, plan["run"]).count(FLAG) == 2
        assert cast(str, notes["run"]).count(FLAG) == 1

    def test_no_expression_is_interpolated_into_a_script(self) -> None:
        for step in _action()["runs"]["steps"]:
            assert "${{" not in cast(str, step.get("run", "")), step.get("name")


class TestTheSteps:
    def test_the_default_plans_from_the_v_tags(self, tmp_path: Path) -> None:
        outputs, summary, notes, plan = _release(tmp_path, None)

        assert outputs == "version=2.1.0\npart=minor\n"
        assert summary.startswith("## Release v2.1.0\n")
        assert plan.startswith("last tag : v2.0.0\n")
        assert "Changes since v2.0.0:\n\n- feat: api\n" in notes

    def test_a_package_plans_from_its_own_tags_only(self, tmp_path: Path) -> None:
        outputs, summary, notes, plan = _release(tmp_path, "api-v")

        assert outputs == "version=0.5.0\npart=minor\n"
        assert summary.startswith("## Release api-v0.5.0\n")
        assert plan.startswith("last tag : api-v0.4.0\n")
        assert "Changes since api-v0.4.0:\n\n- feat: api\n- fix: worker\n" in notes

    @pytest.mark.parametrize("prefix", ["", "-v", "v$(touch pwned)", "a..b"])
    def test_an_unsafe_prefix_fails_before_anything_is_written(
        self, tmp_path: Path, prefix: str
    ) -> None:
        repository, merge = _monorepo(tmp_path)
        outputs = tmp_path / "outputs.txt"
        env = {
            "PATH": _gh(tmp_path / "bin"),
            "MERGE_SHA": merge,
            "REPOSITORY_SLUG": "o/r",
            "SEMANTIC_BRANCH_CONFIG": "pyproject.toml",
            "TAG_PREFIX": prefix,
            "ACTION_PATH": str(COMPOSITE),
            "GITHUB_OUTPUT": str(outputs),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        }

        result = _run(_step("plan"), tmp_path, repository, env)

        assert result.returncode != 0
        assert "is not allowed" in result.stderr
        assert not outputs.exists()
        assert not (repository / "pwned").exists()
