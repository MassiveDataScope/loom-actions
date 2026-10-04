"""``release-on-label`` keeps ``<package-dir>/CHANGELOG.md`` when a caller asks.

With ``changelog: true`` the planner adds the release to the package's
Keep a Changelog 1.1.0 file at the head of the base branch, the GitHub Release
body is that new section, and the release job commits the file to the base
branch through the contents API, guarded by the blob the file was built on.
Left false, the default, no step touches a file and the release is the one a
caller always got. The scripts run here as the runner runs them, with a ``gh``
on PATH that logs each call and the body it was sent.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path
from typing import Any, cast

import workflow_steps as wf

NAME = "release-on-label"
CHANGELOG_FILE = (
    "${{ inputs.changelog && (inputs.package-dir == '.' && 'CHANGELOG.md' "
    "|| format('{0}/CHANGELOG.md', inputs.package-dir)) || '' }}"
)
STORE = "Find the changelog the release builds on"
UPLOAD = "Store the changelog"
DOWNLOAD = "Download the changelog"
COMMIT = "Commit the changelog"
CHANGED = "${{ inputs.changelog && needs.plan.outputs.changelog_changed == 'true' }}"


def _plan_step() -> dict[str, Any]:
    return next(s for s in wf.steps(NAME, "plan") if s.get("id") == "plan")


def _titles(job: str) -> list[str]:
    return [cast(str, s.get("name", "")) for s in wf.steps(NAME, job)]


class TestTheInput:
    def test_it_is_an_optional_boolean_defaulting_to_false(self) -> None:
        declared = wf.call(NAME)["inputs"]["changelog"]
        assert declared["type"] == "boolean"
        assert declared["default"] is False
        assert declared["required"] is False

    def test_it_names_the_changelog_of_the_package_or_none(self) -> None:
        plan = wf.jobs(NAME)["plan"]
        assert plan["env"]["CHANGELOG_FILE"] == CHANGELOG_FILE
        assert _plan_step()["with"]["changelog-file"] == "${{ env.CHANGELOG_FILE }}"

    def test_the_plan_reports_what_the_release_job_commits(self) -> None:
        outputs = wf.jobs(NAME)["plan"]["outputs"]
        assert outputs["changelog_file"] == "${{ env.CHANGELOG_FILE }}"
        assert outputs["changelog_changed"] == "${{ steps.plan.outputs.changelog-changed }}"
        assert outputs["changelog_blob"] == "${{ steps.changelog.outputs.blob }}"


class TestTheDefaultIsUnchanged:
    def test_every_new_step_runs_only_for_a_changelog(self) -> None:
        assert wf.step(NAME, "plan", STORE)["if"] == "${{ inputs.changelog }}"
        assert wf.step(NAME, "plan", UPLOAD)["if"] == (
            "${{ inputs.changelog && steps.plan.outputs.changelog-changed == 'true' }}"
        )
        assert wf.step(NAME, "release", DOWNLOAD)["if"] == CHANGED
        assert wf.step(NAME, "release", COMMIT)["if"] == CHANGED

    def test_the_steps_a_caller_ran_keep_their_order(self) -> None:
        plan = [t for t in _titles("plan") if t not in (STORE, UPLOAD)]
        release = [t for t in _titles("release") if t not in (DOWNLOAD, COMMIT)]
        assert plan[-3:] == [
            "Plan the release",
            "Store release notes",
            "Create the immutable version tag",
        ]
        assert release == [
            "Download release notes",
            "Update floating major tag",
            "Create GitHub release",
        ]

    def test_the_release_body_is_still_the_notes_file(self) -> None:
        step = wf.step(NAME, "release", "Create GitHub release")
        assert step["with"]["body_path"] == "CHANGELOG_RELEASE.md"

    def test_the_jobs_and_their_permissions_are_the_same(self) -> None:
        jobs = wf.jobs(NAME)
        assert list(jobs) == ["plan", "build", "release"]
        assert jobs["plan"]["permissions"] == {"contents": "write", "pull-requests": "read"}
        assert jobs["release"]["permissions"] == {"contents": "write"}


class TestTheOrder:
    def test_the_changelog_is_found_and_stored_before_any_tag_exists(self) -> None:
        titles = _titles("plan")
        tag = titles.index("Create the immutable version tag")
        assert titles.index("Plan the release") < titles.index(STORE) < tag
        assert titles.index(STORE) < titles.index(UPLOAD) < tag

    def test_the_changelog_is_committed_once_the_release_exists(self) -> None:
        titles = _titles("release")
        assert titles.index("Create GitHub release") < titles.index(DOWNLOAD)
        assert titles.index(DOWNLOAD) < titles.index(COMMIT) == len(titles) - 1

    def test_the_artifact_carries_the_file_the_plan_wrote(self) -> None:
        upload = wf.step(NAME, "plan", UPLOAD)
        assert upload["with"]["name"] == "changelog"
        assert upload["with"]["path"] == "${{ env.CHANGELOG_FILE }}"
        assert upload["with"]["if-no-files-found"] == "error"
        download = wf.step(NAME, "release", DOWNLOAD)
        assert download["with"] == {"name": "changelog", "path": "changelog"}


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments), check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


class TestFindingTheBlob:
    def _repository(self, tmp_path: Path, tracked: bool) -> Path:
        repository = tmp_path / "repo"
        (repository / "apps" / "api").mkdir(parents=True)
        _git(repository, "init", "-q", "--initial-branch=master")
        (repository / "README.md").write_text("x\n", "utf-8")
        if tracked:
            (repository / "apps" / "api" / "CHANGELOG.md").write_text("# Changelog\n", "utf-8")
        _git(repository, "add", "-A")
        _git(repository, "-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "-qm", "i")
        return repository

    def _find(self, tmp_path: Path, repository: Path) -> str:
        output = tmp_path / "out"
        env = {"CHANGELOG_FILE": "apps/api/CHANGELOG.md", "GITHUB_OUTPUT": str(output)}
        # The planner has already rewritten the file in the working tree.
        (repository / "apps" / "api" / "CHANGELOG.md").write_text("# Changelog\n\nnew\n", "utf-8")
        result = wf.run(NAME, "plan", STORE, env, repository)
        assert result.returncode == 0, result.stdout + result.stderr
        return output.read_text("utf-8")

    def test_it_is_the_committed_blob_not_the_rewritten_file(self, tmp_path: Path) -> None:
        repository = self._repository(tmp_path, tracked=True)
        blob = _git(repository, "rev-parse", "HEAD:apps/api/CHANGELOG.md")

        assert self._find(tmp_path, repository) == f"blob={blob}\n"

    def test_it_is_empty_for_a_file_the_branch_does_not_hold(self, tmp_path: Path) -> None:
        repository = self._repository(tmp_path, tracked=False)

        assert self._find(tmp_path, repository) == "blob=\n"


def _stub_gh(bin_dir: Path, log: Path) -> dict[str, str]:
    bin_dir.mkdir(parents=True, exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "{log}"\ncat >> "{log}"\necho "c0ffee"\n',
        encoding="utf-8",
    )
    gh.chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


class TestTheCommit:
    def _commit(self, tmp_path: Path, blob: str) -> tuple[subprocess.CompletedProcess[str], Path]:
        work = tmp_path / "work"
        (work / "changelog").mkdir(parents=True)
        (work / "changelog" / "CHANGELOG.md").write_text("# Changelog\n\n- ünïcode\n", "utf-8")
        log = tmp_path / "gh.log"
        env = {
            **_stub_gh(tmp_path / "bin", log),
            "GH_TOKEN": "token",
            "GITHUB_REPOSITORY": "acme/repo",
            "CHANGELOG_FILE": "apps/control-plane/CHANGELOG.md",
            "BASE_BRANCH": "master",
            "BLOB_SHA": blob,
            "TAG_PREFIX": "control-plane/v",
            "VERSION": "0.1.0",
        }
        return wf.run(NAME, "release", COMMIT, env, work), log

    def _sent(self, log: Path) -> tuple[str, dict[str, str]]:
        call, body = log.read_text("utf-8").split("\n", 1)
        return call, json.loads(body)

    def test_it_puts_the_file_on_the_base_branch_over_the_blob_it_was_built_on(
        self, tmp_path: Path
    ) -> None:
        result, log = self._commit(tmp_path, "d" * 40)

        assert result.returncode == 0, result.stdout + result.stderr
        call, body = self._sent(log)
        assert call == (
            "api --method PUT repos/acme/repo/contents/apps/control-plane/CHANGELOG.md "
            "--input - --jq .commit.sha"
        )
        assert body["branch"] == "master"
        assert body["sha"] == "d" * 40
        assert body["message"] == "docs(release): changelog for control-plane/v0.1.0"
        assert base64.b64decode(body["content"]).decode("utf-8") == "# Changelog\n\n- ünïcode\n"

    def test_a_file_the_branch_does_not_hold_is_created(self, tmp_path: Path) -> None:
        result, log = self._commit(tmp_path, "")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "sha" not in self._sent(log)[1]

    def test_no_expression_is_interpolated_into_a_script(self) -> None:
        for job in wf.jobs(NAME):
            for each in wf.steps(NAME, job):
                assert "${{" not in cast(str, each.get("run", "")), f"{job}: {each.get('name')}"
