from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "python-service-ci.yml"
PINNED = re.compile(r"^[^@]+@[0-9a-f]{40}$")
SCANNER_JOB_INPUT = {"sonar": "inputs.sonar", "dependencies": "inputs.snyk"}


def _workflow() -> dict[Any, Any]:
    return cast(dict[Any, Any], yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def _call() -> dict[str, Any]:
    workflow = _workflow()
    return cast(dict[str, Any], workflow.get("on", workflow.get(True))["workflow_call"])


def _jobs() -> dict[str, dict[str, Any]]:
    return cast(dict[str, dict[str, Any]], _workflow()["jobs"])


def _steps(job: str) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], _jobs()[job].get("steps", []))


def _all_steps() -> list[tuple[str, dict[str, Any]]]:
    return [(name, step) for name in _jobs() for step in _steps(name)]


class TestScannersAreOptIn:
    @pytest.mark.parametrize("name", ["codecov", "sonar", "snyk"])
    def test_off_unless_the_caller_asks(self, name: str) -> None:
        declared = _call()["inputs"][name]
        assert declared["type"] == "boolean"
        assert declared["default"] is False
        assert declared["required"] is False

    @pytest.mark.parametrize("secret", ["CODECOV_TOKEN", "SONAR_TOKEN", "SNYK_TOKEN"])
    def test_no_secret_is_required(self, secret: str) -> None:
        assert _call()["secrets"][secret]["required"] is False

    @pytest.mark.parametrize(("job", "flag"), SCANNER_JOB_INPUT.items())
    def test_a_scanner_job_runs_only_when_turned_on(self, job: str, flag: str) -> None:
        assert flag in cast(str, _jobs()[job]["if"])

    def test_codecov_steps_run_only_when_turned_on(self) -> None:
        codecov = [s for s in _steps("report") if "codecov" in str(s.get("uses", "")).lower()]
        assert len(codecov) == 2
        assert all("inputs.codecov" in cast(str, s["if"]) for s in codecov)

    @pytest.mark.parametrize(
        ("job", "step", "scanner"),
        [
            ("sonar", "Require the SonarQube configuration", "sonarqube-scan-action"),
            ("dependencies", "Require the Snyk token", "snyk/actions/setup"),
        ],
    )
    def test_a_scanner_turned_on_without_its_secret_fails_first(
        self, job: str, step: str, scanner: str
    ) -> None:
        steps = _steps(job)
        guard = next(i for i, s in enumerate(steps) if s.get("name") == step)
        scan = next(i for i, s in enumerate(steps) if scanner in str(s.get("uses", "")))
        assert "if" not in steps[guard]
        assert "exit 1" in cast(str, steps[guard]["run"])
        assert guard < scan

    @pytest.mark.parametrize("job", SCANNER_JOB_INPUT)
    def test_a_fork_pull_request_skips_secret_scanners(self, job: str) -> None:
        assert "head.repo.full_name == github.repository" in cast(str, _jobs()[job]["if"])


class TestNothingIsPublished:
    def test_no_step_pushes_or_uploads_to_an_index(self) -> None:
        for job, step in _all_steps():
            uses = str(step.get("uses", ""))
            assert "pypi-publish" not in uses, job
            assert "aws-actions" not in uses, job
            assert step.get("with", {}).get("push") in (None, False), job

    def test_no_job_can_mint_a_cloud_identity(self) -> None:
        for name, job in _jobs().items():
            assert "id-token" not in job.get("permissions", {}), name


class TestSupplyChain:
    def test_every_action_is_pinned_by_commit(self) -> None:
        for job, step in _all_steps():
            if "uses" in step:
                assert PINNED.match(cast(str, step["uses"])), f"{job}: {step['uses']}"

    def test_no_expression_is_interpolated_into_a_script(self) -> None:
        for job, step in _all_steps():
            assert "${{" not in cast(str, step.get("run", "")), f"{job}: {step.get('name')}"

    def test_checkouts_do_not_persist_the_token(self) -> None:
        for job, step in _all_steps():
            if "actions/checkout" in str(step.get("uses", "")):
                assert step["with"]["persist-credentials"] is False, job

    def test_every_job_declares_its_permissions_and_a_timeout(self) -> None:
        for name, job in _jobs().items():
            assert "permissions" in job, name
            assert "timeout-minutes" in job, name

    def test_only_the_report_can_write(self) -> None:
        writers = {
            name
            for name, job in _jobs().items()
            if "write" in cast(dict[str, str], job["permissions"]).values()
        }
        assert writers == {"report"}

    def test_every_install_honours_the_lockfile(self) -> None:
        for job, step in _all_steps():
            for line in cast(str, step.get("run", "")).splitlines():
                command = line.strip()
                if re.match(r"uv (sync|run|export)\b", command):
                    assert "--locked" in command, f"{job}: {command}"


class TestGate:
    def test_the_gate_waits_for_every_other_job(self) -> None:
        gate = _jobs()["gate"]
        assert set(gate["needs"]) == set(_jobs()) - {"gate"}
        assert gate["if"] == "${{ always() }}"

    def test_a_failed_or_cancelled_job_fails_the_gate(self) -> None:
        script = cast(str, _steps("gate")[0]["run"])
        assert '"failure"' in script
        assert '"cancelled"' in script

    def test_the_report_blocks_only_on_security(self) -> None:
        quality = next(s for s in _steps("report") if s.get("id") == "quality")
        assert quality["with"]["fail-on-quality"] == "none"
        assert quality["with"]["test-results-dir"] == "."

    def test_the_coverage_gate_is_on_the_run_that_measured_it(self) -> None:
        run = next(s for s in _steps("test") if "pytest" in str(s.get("run", "")))
        assert "--cov-fail-under" in cast(str, run["run"])

    def test_the_comment_is_skipped_for_a_fork(self) -> None:
        comment = next(s for s in _steps("report") if "pr-comment-update" in str(s.get("uses")))
        assert "SAME_REPOSITORY == 'true'" in cast(str, comment["if"])


QUALITY_REPORT = (
    "the-reacher-data/loom-actions/actions/python/quality-report"
    "@90b0ee6af678dfecf92c18a87e2a5f8081644309"
)
PROJECT_PREFIX = "${{ env.PROJECT_PREFIX }}"
WORKING_DIRECTORY = "${{ inputs.working-directory }}"
JOBS = ["lint", "test", "report", "sonar", "dependencies", "image", "branch", "gate"]
PROJECT_JOBS = {"lint", "test", "dependencies"}


def _step(job: str, predicate: str) -> dict[str, Any]:
    return next(s for s in _steps(job) if predicate in str(s.get("uses", "")) + str(s.get("id")))


def _run_python_heredoc(
    script: str, env: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess[str]:
    match = re.search(r"<<'PY'\n(.*?)\n\s*PY\s*$", script, re.DOTALL)
    assert match is not None, "the step runs no Python heredoc"
    return subprocess.run(
        (sys.executable, "-c", textwrap.dedent(match.group(1))),
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        env={**os.environ, **env},
    )


def _run_bash(script: str, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", "-c", script),
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        env={**os.environ, **env},
    )


class TestDefaultsAreUnchanged:
    """With every new input left alone, the caller gets the jobs and paths it had."""

    @pytest.mark.parametrize(
        ("name", "kind", "default"),
        [
            ("working-directory", "string", "."),
            ("semantic-branch-config", "string", ""),
            ("sonar-blocking", "boolean", True),
            ("codecov-flag", "string", ""),
        ],
    )
    def test_the_new_inputs_are_optional(self, name: str, kind: str, default: object) -> None:
        declared = _call()["inputs"][name]
        assert declared["type"] == kind
        assert declared["default"] == default
        assert declared["required"] is False

    def test_the_jobs_are_the_same(self) -> None:
        assert list(_jobs()) == JOBS
        assert _jobs()["gate"]["needs"] == JOBS[:-1]

    def test_the_default_working_directory_adds_no_prefix(self) -> None:
        assert _workflow()["env"]["PROJECT_PREFIX"] == (
            "${{ inputs.working-directory != '.' && inputs.working-directory != '' "
            "&& format('{0}/', inputs.working-directory) || '' }}"
        )


class TestMonorepo:
    """A project in ``working-directory`` is checked there, and read from the root."""

    @pytest.mark.parametrize("job", sorted(PROJECT_JOBS))
    def test_uv_runs_in_the_working_directory(self, job: str) -> None:
        assert _jobs()[job]["defaults"]["run"]["working-directory"] == WORKING_DIRECTORY

    @pytest.mark.parametrize("job", ["report", "sonar", "image", "branch"])
    def test_root_jobs_stay_at_the_root(self, job: str) -> None:
        assert "defaults" not in _jobs()[job]

    def test_the_uv_cache_keys_on_the_project_lockfile(self) -> None:
        setups = [step for _, step in _all_steps() if "astral-sh/setup-uv" in str(step.get("uses"))]
        assert setups
        assert all(s["with"]["cache-dependency-glob"] == f"{PROJECT_PREFIX}uv.lock" for s in setups)

    def test_test_results_are_uploaded_from_the_project(self) -> None:
        upload = _step("test", "upload-artifact")
        paths = cast(str, upload["with"]["path"]).splitlines()
        assert paths == [
            f"{PROJECT_PREFIX}{name}" for name in ("junit.xml", "coverage.xml", "coverage.json")
        ]

    @pytest.mark.parametrize("job", ["report", "sonar"])
    def test_test_results_are_downloaded_into_the_project(self, job: str) -> None:
        download = _step(job, "download-artifact")
        assert download["with"]["path"] == WORKING_DIRECTORY

    def test_the_quality_report_is_the_monorepo_release(self) -> None:
        quality = _step("report", "quality")
        assert quality["uses"] == QUALITY_REPORT
        assert quality["with"]["working-directory"] == WORKING_DIRECTORY
        assert quality["with"]["src-dir"] == "${{ inputs.src-dir }}"
        assert quality["with"]["test-dir"] == "${{ inputs.test-dir }}"

    def test_codecov_reads_the_project_reports(self) -> None:
        codecov = [s for s in _steps("report") if "codecov" in str(s.get("uses", "")).lower()]
        files = sorted(cast(str, s["with"]["files"]) for s in codecov)
        assert files == [f"./{PROJECT_PREFIX}coverage.xml", f"./{PROJECT_PREFIX}junit.xml"]
        for step in codecov:
            name = cast(str, step["with"]["files"]).rsplit("}}", 1)[1]
            assert f"hashFiles(format('{{0}}{name}', env.PROJECT_PREFIX))" in cast(str, step["if"])

    def test_both_codecov_uploads_carry_the_callers_flag(self) -> None:
        """An empty flag is what codecov-action gets when none is passed: its
        ``flags`` input has no default."""
        codecov = [s for s in _steps("report") if "codecov" in str(s.get("uses", "")).lower()]
        assert [s["with"]["flags"] for s in codecov] == ["${{ inputs.codecov-flag }}"] * 2

    def test_sonar_scans_from_the_root_with_prefixed_paths(self) -> None:
        args = cast(str, _step("sonar", "sonarqube-scan-action")["with"]["args"])
        assert f"-Dsonar.sources={PROJECT_PREFIX}${{{{ inputs.src-dir }}}}" in args
        assert f"-Dsonar.tests={PROJECT_PREFIX}${{{{ inputs.test-dir }}}}" in args
        assert f"-Dsonar.python.coverage.reportPaths={PROJECT_PREFIX}coverage.xml" in args
        assert f"-Dsonar.python.xunit.reportPath={PROJECT_PREFIX}junit.xml" in args


class TestBranchRules:
    """The ``branch`` job reads ``[tool.semantic_branch]`` from the file it is given."""

    RULES = '[tool.semantic_branch]\nminor = ["feature/.*"]\npatch = ["fix/.*"]\n'

    def _check(self, tmp_path: Path, head: str, config: str) -> subprocess.CompletedProcess[str]:
        step = next(s for s in _steps("branch") if "HEAD_REF" in s.get("env", {}))
        assert step["env"]["SEMANTIC_BRANCH_CONFIG"] == "${{ inputs.semantic-branch-config }}"
        assert step["env"]["WORKING_DIRECTORY"] == WORKING_DIRECTORY
        return _run_python_heredoc(
            cast(str, step["run"]),
            {
                "HEAD_REF": head,
                "SEMANTIC_BRANCH_CONFIG": config,
                "WORKING_DIRECTORY": "apps/api",
            },
            tmp_path,
        )

    def test_without_a_config_it_reads_the_project_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "apps" / "api").mkdir(parents=True)
        (tmp_path / "apps" / "api" / "pyproject.toml").write_text(self.RULES, encoding="utf-8")
        completed = self._check(tmp_path, "feature/x", "")
        assert completed.returncode == 0, completed.stdout
        assert "'feature/x' is a minor branch" in completed.stdout

    def test_a_config_is_read_from_the_root(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[tool.semantic_branch]\nmajor = ["x/.*"]\n')
        (tmp_path / "rules").mkdir()
        (tmp_path / "rules" / "branches.toml").write_text(self.RULES, encoding="utf-8")
        completed = self._check(tmp_path, "fix/y", "rules/branches.toml")
        assert completed.returncode == 0, completed.stdout
        assert "'fix/y' is a patch branch" in completed.stdout

    def test_an_unclassified_branch_fails(self, tmp_path: Path) -> None:
        (tmp_path / "rules.toml").write_text(self.RULES, encoding="utf-8")
        completed = self._check(tmp_path, "misc/z", "rules.toml")
        assert completed.returncode == 1
        assert "::error title=Unclassified branch::" in completed.stdout

    def test_a_missing_config_fails_with_a_clear_error(self, tmp_path: Path) -> None:
        completed = self._check(tmp_path, "feature/x", "apps/api/pyproject.toml")
        assert completed.returncode == 1
        assert "::error title=Branch rules not found::" in completed.stdout
        assert "Traceback" not in completed.stderr


class TestSonarBlocking:
    """``sonar-blocking: false`` turns every Sonar failure into a notice."""

    def _guard(self) -> dict[str, Any]:
        return next(
            s for s in _steps("sonar") if s.get("name") == "Require the SonarQube configuration"
        )

    @pytest.mark.parametrize("predicate", ["download-artifact", "sonarqube-scan-action"])
    def test_a_failing_step_is_tolerated_only_when_informative(self, predicate: str) -> None:
        assert _step("sonar", predicate)["continue-on-error"] == "${{ !inputs.sonar-blocking }}"

    def test_the_scan_is_skipped_without_its_configuration(self) -> None:
        guard = self._guard()
        assert guard["id"] == "config"
        assert guard["env"]["SONAR_BLOCKING"] == "${{ inputs.sonar-blocking }}"
        for predicate in ("download-artifact", "sonarqube-scan-action"):
            assert "steps.config.outputs.ready == 'true'" in _step("sonar", predicate)["if"]

    def test_a_tolerated_failure_leaves_a_notice(self) -> None:
        notice = next(
            s for s in _steps("sonar") if s.get("name") == "Report an informative failure"
        )
        condition = cast(str, notice["if"])
        assert "!inputs.sonar-blocking" in condition
        assert "steps.download.outcome == 'failure'" in condition
        assert "steps.scan.outcome == 'failure'" in condition
        assert "::notice" in cast(str, notice["run"])

    @pytest.mark.parametrize(
        ("blocking", "token", "returncode", "ready"),
        [
            ("true", "", 1, None),
            ("false", "", 0, "false"),
            ("true", "t", 0, "true"),
            ("false", "t", 0, "true"),
        ],
    )
    def test_the_guard(
        self, tmp_path: Path, blocking: str, token: str, returncode: int, ready: str | None
    ) -> None:
        outputs = tmp_path / "outputs.txt"
        outputs.touch()
        completed = _run_bash(
            cast(str, self._guard()["run"]),
            {
                "SONAR_TOKEN": token,
                "SONAR_PROJECT_KEY": "key",
                "SONAR_BLOCKING": blocking,
                "GITHUB_OUTPUT": str(outputs),
            },
            tmp_path,
        )
        assert completed.returncode == returncode, completed.stdout
        written = outputs.read_text(encoding="utf-8").splitlines()
        assert written == ([] if ready is None else [f"ready={ready}"])
        if ready == "false":
            assert "::notice title=SonarQube is on but not configured::" in completed.stdout


def test_the_builder_is_the_image_release_builder() -> None:
    """The image job builds the caller's Dockerfile; its BuildKit is pinned by
    the same digest image-release publishes with."""
    release = yaml.safe_load((WORKFLOW.parent / "image-release.yml").read_text(encoding="utf-8"))
    release_buildx = [
        s
        for s in release["jobs"]["image"]["steps"]
        if "docker/setup-buildx-action" in str(s.get("uses"))
    ]
    buildx = [s for s in _steps("image") if "docker/setup-buildx-action" in str(s.get("uses"))]
    assert len(buildx) == len(release_buildx) == 1
    assert buildx[0]["uses"] == release_buildx[0]["uses"]
    assert buildx[0]["with"]["driver-opts"] == release_buildx[0]["with"]["driver-opts"]
    assert re.fullmatch(
        r"image=moby/buildkit@sha256:[0-9a-f]{64}", cast(str, buildx[0]["with"]["driver-opts"])
    )
