"""Every key a caller passes must be declared by the workflow it calls.

GitHub refuses a run that passes an undeclared input or secret, and one that
grants a called workflow fewer permissions than its jobs request, but only
when the run starts. The fixtures under ``tests/fixtures/callers`` mirror the
monorepo caller the new workflows are built for, so a renamed input or a job
that starts asking for more is caught here instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROOT = Path(__file__).parents[2]
CALLERS = ROOT / "tests" / "fixtures" / "callers"
WORKFLOWS = ROOT / ".github" / "workflows"
PREFIX = "MassiveDataScope/loom-actions/.github/workflows/"
LEVEL = {"none": 0, "read": 1, "write": 2}


def _yaml(path: Path) -> dict[Any, Any]:
    return cast(dict[Any, Any], yaml.safe_load(path.read_text(encoding="utf-8")))


def _calls() -> list[tuple[str, str, dict[str, Any]]]:
    found = []
    for caller in sorted(CALLERS.glob("*.yml")):
        for job_name, job in cast(dict[str, dict[str, Any]], _yaml(caller)["jobs"]).items():
            uses = cast(str, job.get("uses", ""))
            if uses.startswith(PREFIX):
                workflow = uses.removeprefix(PREFIX).split("@")[0]
                found.append((f"{caller.name}:{job_name}", workflow, job))
    return found


def _called(workflow: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    document = _yaml(WORKFLOWS / workflow)
    call = cast(dict[str, Any], document.get("on", document.get(True))["workflow_call"])
    return call, cast(dict[str, dict[str, Any]], document["jobs"])


CALLS = _calls()
IDS = [name for name, _, _ in CALLS]


def test_the_fixtures_call_every_new_workflow() -> None:
    called = {workflow for _, workflow, _ in CALLS}
    assert {"node-ci.yml", "repo-security.yml", "pages.yml", "image-release.yml"} <= called


@pytest.mark.parametrize(("caller", "workflow", "job"), CALLS, ids=IDS)
def test_every_input_passed_is_declared(caller: str, workflow: str, job: dict[str, Any]) -> None:
    declared = _called(workflow)[0].get("inputs", {})
    assert set(job.get("with", {})) - set(declared) == set(), caller


@pytest.mark.parametrize(("caller", "workflow", "job"), CALLS, ids=IDS)
def test_every_required_input_is_passed(caller: str, workflow: str, job: dict[str, Any]) -> None:
    declared = cast(dict[str, dict[str, Any]], _called(workflow)[0].get("inputs", {}))
    required = {name for name, spec in declared.items() if spec.get("required")}
    assert required - set(job.get("with", {})) == set(), caller


@pytest.mark.parametrize(("caller", "workflow", "job"), CALLS, ids=IDS)
def test_every_secret_passed_is_declared(caller: str, workflow: str, job: dict[str, Any]) -> None:
    secrets = job.get("secrets", {})
    assert secrets != "inherit", f"{caller}: secrets are passed explicitly"
    declared = _called(workflow)[0].get("secrets", {})
    assert set(secrets) - set(declared) == set(), caller


@pytest.mark.parametrize(("caller", "workflow", "job"), CALLS, ids=IDS)
def test_the_caller_grants_what_every_called_job_asks(
    caller: str, workflow: str, job: dict[str, Any]
) -> None:
    granted = cast(dict[str, str], job["permissions"])
    for called_name, called in _called(workflow)[1].items():
        for scope, level in cast(dict[str, str], called.get("permissions", {})).items():
            have = LEVEL[granted.get(scope, "none")]
            assert have >= LEVEL[level], f"{caller} -> {called_name}: {scope}: {level}"
