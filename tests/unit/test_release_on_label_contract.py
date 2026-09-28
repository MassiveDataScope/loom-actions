"""Contract tests for the reusable workflow every trunk-based repository calls.

Publishing is opt-in: a repository that ships an application, not a package,
must get the tag, the notes and the GitHub release without ever building a
distribution or needing an index account.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "release-on-label.yml"
ACTION = Path(__file__).parents[2] / "actions" / "release" / "plan-release" / "action.yml"


def _workflow() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def _inputs() -> dict[str, Any]:
    workflow = cast(dict[Any, Any], _workflow())
    triggers = cast(dict[str, Any], workflow.get("on", workflow.get(True)))
    return cast(dict[str, Any], triggers["workflow_call"]["inputs"])


def _outputs() -> dict[str, Any]:
    workflow = cast(dict[Any, Any], _workflow())
    triggers = cast(dict[str, Any], workflow.get("on", workflow.get(True)))
    return cast(dict[str, Any], triggers["workflow_call"]["outputs"])


def _job(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], cast(dict[str, Any], _workflow()["jobs"])[name])


def _steps(job: str) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], _job(job)["steps"])


class TestBuildingIsOptIn:
    def test_building_is_off_unless_the_caller_asks(self) -> None:
        publish = _inputs()["build-distribution"]
        assert publish["type"] == "boolean"
        assert publish["default"] is False
        assert publish["required"] is False

    def test_nothing_is_built_when_building_is_off(self) -> None:
        assert _job("build")["if"] == "${{ inputs.build-distribution }}"

    def test_no_upload_happens_here(self) -> None:
        """PyPI's trusted publishing rejects a token minted for a reusable workflow."""
        for job in ("plan", "build", "release"):
            for step in _steps(job):
                assert "pypi-publish" not in str(step.get("uses")), job

    def test_a_caller_that_does_not_build_still_gets_a_release(self) -> None:
        release = [s for s in _steps("release") if "action-gh-release" in str(s.get("uses"))]
        assert len(release) == 1
        assert "if" not in release[0]

    def test_building_without_a_package_name_fails_closed(self) -> None:
        guard = [
            s for s in _steps("plan") if s.get("name") == "Require a package name when building"
        ]
        assert len(guard) == 1
        assert guard[0]["if"] == "${{ inputs.build-distribution && inputs.package-name == '' }}"

    def test_the_distribution_is_left_for_the_caller_to_publish(self) -> None:
        upload = [s for s in _steps("build") if "upload-artifact" in str(s.get("uses"))]
        assert len(upload) == 1
        assert upload[0]["with"]["name"] == "distributions"


class TestTrigger:
    def test_the_label_and_the_merge_are_both_required(self) -> None:
        condition = cast(str, _job("plan")["if"])
        assert "merged == true" in condition
        assert "labels.*.name, inputs.release-label)" in condition

    def test_the_release_commit_is_the_merge_commit(self) -> None:
        assert "pull_request.merge_commit_sha" in cast(str, _job("plan")["env"]["MERGE_SHA"])

    def test_a_caller_can_resume_a_halted_run_with_a_sha(self) -> None:
        assert _inputs()["merge-sha"]["default"] == ""
        assert "inputs.merge-sha != ''" in cast(str, _job("plan")["if"])


class TestSafety:
    def test_a_commit_outside_the_base_branch_is_refused(self) -> None:
        guard = [s for s in _steps("plan") if "merge-base --is-ancestor" in str(s.get("run"))]
        assert len(guard) == 1

    def test_the_tag_is_the_last_thing_the_plan_does(self) -> None:
        assert _steps("plan")[-1]["name"] == "Create the immutable version tag"

    def test_an_existing_tag_on_another_commit_refuses(self) -> None:
        tag = _steps("plan")[-1]
        assert "expected ${MERGE_SHA}" in cast(str, tag["run"])

    def test_the_build_reads_the_tag_with_its_history(self) -> None:
        checkout = _steps("build")[0]
        assert checkout["with"]["fetch-depth"] == 0
        assert "needs.plan.outputs.version" in cast(str, checkout["with"]["ref"])

    def test_the_planner_takes_its_token_as_an_input(self) -> None:
        action = cast(dict[str, Any], yaml.safe_load(ACTION.read_text(encoding="utf-8")))
        assert action["inputs"]["github-token"]["required"] is True


class TestWhatACallerCanRelyOn:
    def test_the_inner_action_is_not_tracked_on_master(self) -> None:
        """A caller pins this workflow by SHA; a moving reference inside it would
        put unreviewed action code into that caller's release."""
        plan = next(s for s in _steps("plan") if s.get("id") == "plan")
        _, _, revision = cast(str, plan["uses"]).partition("@")
        assert revision != "master"

    def test_a_caller_learns_whether_a_distribution_exists(self) -> None:
        """True only when the build job reached its last step: every check passed
        and the artifact was stored. A skipped build leaves the output empty."""
        outputs = _outputs()
        assert outputs["distribution-built"]["value"] == "${{ jobs.build.outputs.built == 'true' }}"
        assert _job("build")["outputs"]["built"] == "${{ steps.built.outputs.built }}"
        last = _steps("build")[-1]
        assert last["id"] == "built"
        assert "if" not in last
        assert last["run"].strip().endswith('echo "built=true" >> "$GITHUB_OUTPUT"')
        assert "jobs.plan.outputs.version" in cast(str, outputs["version"]["value"])
