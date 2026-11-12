"""(f) A caller without an App gets the release of v1.13.6, but for a closed list.

The baseline is ``release-on-label.yml`` at ``f7962d31`` (v1.13.6), kept in
``tests/fixtures`` so a shallow checkout compares too. Taking out the new
steps of ``plan``, every step of ``plan``, ``build`` and ``release`` keeps its
``uses``, ``with``, ``run`` and order, and its ``if`` but for the tag step of
``plan``. Every new step is skipped for a caller that passes neither
``client-id`` nor ``changelog-verify``: the first term of its ``if`` is false.
The only other differences are the ones of the contract's «Compatibilidad»
list: two new jobs, the ``needs`` and ``if`` of ``build`` and ``release``, the
new inputs, secret and outputs, and the new ``notes_b64`` output of ``plan``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import workflow_steps as wf
import yaml
from gh_expressions import conjuncts, evaluate

NAME = "release-on-label"
ROOT = Path(__file__).parents[2]
BASELINE_SHA = "f7962d31"
BASELINE = ROOT / "tests" / "fixtures" / "release-on-label-v1.13.6.yml"
LEGACY_CALLER = ROOT / "tests" / "fixtures" / "callers" / "release.yml"

# «Compatibilidad», point 6: the new steps of plan, by id. changelog-base and
# changelog-build build the file verify-changelog checks from MERGE_SHA^1.
NEW_PLAN_STEPS = ("resolve-pr", "changelog-base", "changelog-build", "verify-changelog", "notes")
PLANNER_ID = "plan"
TAG_STEP = "Create the immutable version tag"
NEW_JOBS = ("tag-app", "release-app")
NEW_INPUTS = {
    "client-id",
    "environment",
    "changelog-verify",
    "allowed-actors",
    "allowed-head-prefixes",
}


def _baseline() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(BASELINE.read_text("utf-8")))


def _current() -> dict[str, Any]:
    return wf.load(NAME)


def _call(document: dict[Any, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], document.get("on", document.get(True))["workflow_call"])


def _shape(step: dict[str, Any]) -> tuple[Any, ...]:
    return (step.get("name"), step.get("id"), step.get("uses"), step.get("with"), step.get("run"))


def _old_steps(job: str) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], _baseline()["jobs"][job]["steps"])


def _kept_steps(job: str) -> list[dict[str, Any]]:
    return [s for s in _current()["jobs"][job]["steps"] if s.get("id") not in NEW_PLAN_STEPS]


def _legacy_inputs() -> dict[str, object]:
    """The inputs the caller without an App resolves to: its own, else the defaults."""
    declared = cast(dict[str, dict[str, Any]], _call(_current())["inputs"])
    given = yaml.safe_load(LEGACY_CALLER.read_text("utf-8"))["jobs"]["release"]["with"]
    return {f"inputs.{k}": given.get(k, spec.get("default")) for k, spec in declared.items()}


def test_the_baseline_is_v1_13_6() -> None:
    known = subprocess.run(
        ("git", "-C", str(ROOT), "cat-file", "-e", f"{BASELINE_SHA}^{{commit}}"),
        capture_output=True,
        check=False,
    )
    if known.returncode != 0:
        pytest.skip(f"{BASELINE_SHA} is not in this shallow checkout")
    text = subprocess.run(
        ("git", "-C", str(ROOT), "show", f"{BASELINE_SHA}:.github/workflows/{NAME}.yml"),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert BASELINE.read_text("utf-8") == text


def test_the_new_ids_and_the_planner_id_are_fixed() -> None:
    plan_ids = [s.get("id") for s in _current()["jobs"]["plan"]["steps"]]
    assert [i for i in plan_ids if i in NEW_PLAN_STEPS] == list(NEW_PLAN_STEPS)
    assert PLANNER_ID in [s.get("id") for s in _old_steps("plan")]
    assert plan_ids.index("resolve-pr") == 0
    tail = plan_ids[plan_ids.index(PLANNER_ID) + 1 :]
    assert [i for i in tail if i in NEW_PLAN_STEPS] == list(NEW_PLAN_STEPS[1:])


@pytest.mark.parametrize("job", ["plan", "build", "release"])
def test_the_steps_keep_their_uses_with_run_and_order(job: str) -> None:
    assert [_shape(s) for s in _kept_steps(job)] == [_shape(s) for s in _old_steps(job)]


@pytest.mark.parametrize("job", ["plan", "build", "release"])
def test_the_steps_keep_their_conditions_but_the_tag(job: str) -> None:
    for old, new in zip(_old_steps(job), _kept_steps(job), strict=True):
        if job == "plan" and old.get("name") == TAG_STEP:
            assert new["if"] == "${{ inputs.client-id == '' && steps.plan.outputs.version != '' }}"
            assert old["if"] == "${{ steps.plan.outputs.version != '' }}"
        else:
            assert new.get("if") == old.get("if"), old.get("name")
        rest = {k: v for k, v in new.items() if k != "if"}
        assert rest == {k: v for k, v in old.items() if k != "if"}, old.get("name")


def test_the_plan_job_keeps_its_condition_permissions_and_environment() -> None:
    old, new = _baseline()["jobs"]["plan"], _current()["jobs"]["plan"]
    assert new["if"] == old["if"]
    assert new["permissions"] == old["permissions"]
    assert new["env"] == old["env"]
    assert new["runs-on"] == old["runs-on"]
    assert "needs" not in new
    outputs = dict(new["outputs"])
    assert outputs.pop("notes_b64") == "${{ steps.notes.outputs.notes_b64 }}"
    assert outputs == old["outputs"]


def test_build_and_release_change_only_needs_and_condition() -> None:
    old, new = _baseline()["jobs"], _current()["jobs"]
    for job in ("build", "release"):
        rest_old = {k: v for k, v in old[job].items() if k not in ("if", "needs", "steps")}
        rest_new = {k: v for k, v in new[job].items() if k not in ("if", "needs", "steps")}
        assert rest_new == rest_old, job
    assert old["build"]["needs"] == "plan"
    assert new["build"]["needs"] == ["plan", "tag-app"]
    assert new["release"]["needs"] == old["release"]["needs"] == ["plan", "build"]


def test_the_release_keeps_the_clauses_of_v1_13_6() -> None:
    old = conjuncts(_baseline()["jobs"]["release"]["if"])
    new = conjuncts(_current()["jobs"]["release"]["if"])
    assert old == [
        "always()",
        "needs.plan.result == 'success'",
        "needs.build.result != 'failure'",
        "needs.plan.outputs.version != ''",
    ]
    assert new == [
        "!cancelled()",
        "needs.plan.result == 'success'",
        "(needs.build.result == 'success' || needs.build.result == 'skipped')",
        "needs.plan.outputs.version != ''",
        "inputs.client-id == ''",
    ]


def test_the_build_keeps_the_clauses_of_v1_13_6() -> None:
    old = conjuncts(_baseline()["jobs"]["build"]["if"])
    new = conjuncts(_current()["jobs"]["build"]["if"])
    assert new[-len(old) :] == old


@pytest.mark.parametrize("build", ["success", "skipped", "failure", "cancelled"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_without_an_app_the_release_runs_as_before_unless_cancelled(
    build: str, cancelled: bool
) -> None:
    """always() and != 'failure' differ from !cancelled() and success|skipped only on a cancel."""
    context: dict[str, object] = {
        "always()": True,
        "cancelled()": cancelled,
        "needs.plan.result": "success",
        "needs.plan.outputs.version": "1.2.0",
        "needs.build.result": build,
        **_legacy_inputs(),
    }
    before = evaluate(_baseline()["jobs"]["release"]["if"], context)
    now = evaluate(_current()["jobs"]["release"]["if"], context)
    if cancelled or build == "cancelled":
        assert now is False
    else:
        assert now is before


@pytest.mark.parametrize("version", ["", "1.2.0"])
@pytest.mark.parametrize("built", [True, False])
def test_without_an_app_the_build_runs_as_before(version: str, built: bool) -> None:
    context: dict[str, object] = {
        "cancelled()": False,
        "needs.plan.result": "success",
        "needs.plan.outputs.version": version,
        "needs.tag-app.result": "skipped",
        **_legacy_inputs(),
        "inputs.build-distribution": built,
    }
    before = evaluate(_baseline()["jobs"]["build"]["if"], context)
    assert evaluate(_current()["jobs"]["build"]["if"], context) is before


@pytest.mark.parametrize("step_id", NEW_PLAN_STEPS)
def test_every_new_step_is_skipped_without_an_app(step_id: str) -> None:
    step = next(s for s in _current()["jobs"]["plan"]["steps"] if s.get("id") == step_id)
    first = conjuncts(step["if"])[0]
    assert first in ("inputs.client-id != ''", "inputs.changelog-verify")
    assert "||" not in step["if"]
    for version in ("", "1.2.0"):
        context = {**_legacy_inputs(), "steps.plan.outputs.version": version}
        assert evaluate(first, context) is False
        assert evaluate(step["if"], context) is False


@pytest.mark.parametrize("job", NEW_JOBS)
def test_the_new_jobs_are_skipped_without_an_app(job: str) -> None:
    context: dict[str, object] = {
        "cancelled()": False,
        "needs.plan.result": "success",
        "needs.plan.outputs.version": "1.2.0",
        "needs.tag-app.result": "skipped",
        "needs.build.result": "success",
        **_legacy_inputs(),
    }
    assert evaluate(_current()["jobs"][job]["if"], context) is False


def test_nothing_else_changed() -> None:
    old, new = _baseline(), _current()
    assert {k: v for k, v in new.items() if k not in ("on", True, "jobs")} == {
        k: v for k, v in old.items() if k not in ("on", True, "jobs")
    }
    assert "concurrency" not in new
    assert list(new["jobs"]) == ["plan", "tag-app", "build", "release", "release-app"]
    assert [j for j in new["jobs"] if j not in NEW_JOBS] == list(old["jobs"])
    old_call, new_call = _call(old), _call(new)
    assert set(new_call) == {"inputs", "outputs", "secrets"}
    assert set(new_call["inputs"]) - set(old_call["inputs"]) == NEW_INPUTS
    for name, spec in old_call["inputs"].items():
        assert new_call["inputs"][name] == spec, name
    for name in NEW_INPUTS:
        assert new_call["inputs"][name]["required"] is False
    assert set(new_call["outputs"]) - set(old_call["outputs"]) == {"release-sha"}
    for name, spec in old_call["outputs"].items():
        assert new_call["outputs"][name] == spec, name
    assert set(new_call["secrets"]) == {"APP_PRIVATE_KEY"}
