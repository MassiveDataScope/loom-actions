"""``release-on-label`` keeps ``<package-dir>/CHANGELOG.md`` when a caller asks.

With ``changelog: true`` the planner adds the release to the package's
Keep a Changelog 1.1.0 file at the head of the base branch, the GitHub Release
body is that new section, and once the release exists the release job commits
the file through plan-release's ``commit-changelog`` composite, which reads the
branch again and retries a 409. That commit cannot fail the workflow: the tag
and the release already exist, so a failure is a warning that says how to
recover, and ``changelog-committed`` reports it. Left false, the default, no
step touches a file and the release is the one a caller always got. Scripts
run here as the runner runs them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import workflow_steps as wf
import yaml

ROOT = Path(__file__).parents[2]
NAME = "release-on-label"
CHANGELOG_FILE = (
    "${{ inputs.changelog && (inputs.package-dir == '.' && 'CHANGELOG.md' "
    "|| format('{0}/CHANGELOG.md', inputs.package-dir)) || '' }}"
)
UPLOAD = "Store the changelog"
DOWNLOAD = "Download the changelog"
COMMIT = "Commit the changelog"
WARN = "Warn that the changelog was not committed"
COMPOSITE = "MassiveDataScope/loom-actions/actions/release/plan-release"
PIN = re.compile(
    rf"uses: {re.escape(COMPOSITE)}(?P<path>/commit-changelog)?@(?P<sha>[0-9a-f]{{40}})"
)


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

    def test_the_plan_reports_the_file_the_release_job_commits(self) -> None:
        outputs = wf.jobs(NAME)["plan"]["outputs"]
        assert outputs["changelog_file"] == "${{ env.CHANGELOG_FILE }}"
        assert "changelog_blob" not in outputs


class TestTheDefaultIsUnchanged:
    def test_every_new_step_runs_only_for_a_changelog(self) -> None:
        assert wf.step(NAME, "plan", UPLOAD)["if"] == (
            "${{ inputs.changelog && steps.plan.outputs.version != '' }}"
        )
        assert wf.step(NAME, "release", DOWNLOAD)["if"] == "${{ inputs.changelog }}"
        assert wf.step(NAME, "release", COMMIT)["if"] == (
            "${{ inputs.changelog && steps.download-changelog.outcome == 'success' }}"
        )
        assert wf.step(NAME, "release", WARN)["if"].startswith("${{ inputs.changelog && ")

    def test_the_steps_a_caller_ran_keep_their_order(self) -> None:
        plan = [t for t in _titles("plan") if t != UPLOAD]
        release = [t for t in _titles("release") if t not in (DOWNLOAD, COMMIT, WARN)]
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
    def test_the_changelog_is_stored_before_any_tag_exists(self) -> None:
        titles = _titles("plan")
        tag = titles.index("Create the immutable version tag")
        assert titles.index("Plan the release") < titles.index(UPLOAD) < tag

    def test_the_changelog_is_committed_once_the_release_exists(self) -> None:
        titles = _titles("release")
        assert titles.index("Create GitHub release") < titles.index(DOWNLOAD)
        assert titles.index(DOWNLOAD) < titles.index(COMMIT) < titles.index(WARN)
        assert titles.index(WARN) == len(titles) - 1

    def test_the_artifact_carries_the_file_the_plan_wrote(self) -> None:
        upload = wf.step(NAME, "plan", UPLOAD)
        assert upload["with"]["path"] == "${{ env.CHANGELOG_FILE }}"
        assert upload["with"]["if-no-files-found"] == "error"
        download = wf.step(NAME, "release", DOWNLOAD)
        assert download["with"] == {"name": upload["with"]["name"], "path": "changelog"}


class TestACommitFailureFailsNothing:
    """The tag and the release exist by then: a failed commit warns, and says how to recover."""

    def test_the_download_and_the_commit_continue_on_error(self) -> None:
        assert wf.step(NAME, "release", DOWNLOAD)["continue-on-error"] is True
        assert wf.step(NAME, "release", DOWNLOAD)["id"] == "download-changelog"
        assert wf.step(NAME, "release", COMMIT)["continue-on-error"] is True
        assert wf.step(NAME, "release", COMMIT)["id"] == "commit-changelog"

    def test_the_warning_runs_when_either_failed(self) -> None:
        assert wf.step(NAME, "release", WARN)["if"] == (
            "${{ inputs.changelog && (steps.download-changelog.outcome == 'failure'"
            " || steps.commit-changelog.outcome == 'failure') }}"
        )

    def test_the_warning_names_the_release_and_the_way_back(self, tmp_path: Path) -> None:
        summary = tmp_path / "summary.md"
        env = {
            "CHANGELOG_FILE": "apps/api/CHANGELOG.md",
            "BASE_BRANCH": "master",
            "TAG": "api/v0.2.0",
            "RELEASE_SHA": "a" * 40,
            "GITHUB_STEP_SUMMARY": str(summary),
        }

        result = wf.run(NAME, "release", WARN, env, tmp_path)

        assert result.returncode == 0, result.stderr
        (warning,) = [line for line in result.stdout.splitlines() if line.startswith("::")]
        assert warning.startswith("::warning title=Changelog not committed::api/v0.2.0 is released")
        assert "apps/api/CHANGELOG.md was not committed to master" in warning
        assert "re-run all jobs" in warning
        assert f"merge-sha: {'a' * 40}" in warning
        assert "re-run all jobs" in summary.read_text("utf-8")

    def test_the_workflow_reports_whether_the_changelog_was_committed(self) -> None:
        declared = wf.call(NAME)["outputs"]["changelog-committed"]
        assert declared["value"] == "${{ jobs.release.outputs.changelog-committed == 'true' }}"
        assert wf.jobs(NAME)["release"]["outputs"]["changelog-committed"] == (
            "${{ steps.commit-changelog.outcome == 'success' }}"
        )


class TestTheCommit:
    def _commit(self) -> dict[str, Any]:
        return wf.step(NAME, "release", COMMIT)

    def test_it_runs_plan_releases_composite_pinned_with_the_planner(self) -> None:
        text = (ROOT / ".github" / "workflows" / f"{NAME}.yml").read_text("utf-8")
        pins = {m["path"] or "": m["sha"] for m in PIN.finditer(text)}
        assert set(pins) == {"", "/commit-changelog"}
        assert pins["/commit-changelog"] == pins[""]
        assert self._commit()["uses"] == f"{COMPOSITE}/commit-changelog@{pins['']}"

    def test_it_commits_the_built_file_to_the_base_branch(self) -> None:
        assert self._commit()["with"] == {
            "changelog-file": "${{ needs.plan.outputs.changelog_file }}",
            "built-file": "changelog/CHANGELOG.md",
            "version": "${{ needs.plan.outputs.version }}",
            "tag-prefix": "${{ inputs.tag-prefix }}",
            "base-branch": "${{ inputs.base-branch }}",
            "repository-slug": "${{ github.repository }}",
            "github-token": "${{ secrets.GITHUB_TOKEN }}",
        }

    def test_the_composite_here_declares_every_input_passed(self) -> None:
        action = ROOT / "actions" / "release" / "plan-release" / "commit-changelog" / "action.yml"
        declared = set(yaml.safe_load(action.read_text("utf-8"))["inputs"])
        assert set(self._commit()["with"]) == declared

    def test_no_expression_is_interpolated_into_a_script(self) -> None:
        for job in wf.jobs(NAME):
            for each in wf.steps(NAME, job):
                assert "${{" not in cast(str, each.get("run", "")), f"{job}: {each.get('name')}"
