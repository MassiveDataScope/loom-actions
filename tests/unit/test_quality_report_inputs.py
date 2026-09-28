"""``quality-report`` reads its inputs from the environment, never from the script.

The inputs used to be interpolated into the script text, where GitHub
substitutes them before bash parses it. Each check also runs in a shell of its
own, so the value must be read there too. These tests run the step's script
with a ``uv`` that records its arguments and check that every value, however
hostile, arrives as one argument and is never run.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ACTION = Path(__file__).parents[2] / "actions" / "python" / "quality-report" / "action.yml"
HOSTILE = "src dir/it's $(touch pwned) `touch pwned2`"

# A function rather than a file on PATH: each check runs in a login shell,
# which may rebuild PATH, but inherits an exported function.
UV_STUB = """
uv() {
  printf '%s\\n' "$@" > "uv.$1.$(( $(ls uv.run.* 2>/dev/null | wc -l) + 1 )).args"
  return "${UV_EXIT:-0}"
}
export -f uv
"""


def _action() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(ACTION.read_text(encoding="utf-8")))


def _checks() -> dict[str, Any]:
    return next(s for s in _action()["runs"]["steps"] if s.get("id") == "checks")


def _run_checks(cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    script = cwd / ".checks.sh"
    script.write_text(UV_STUB + cast(str, _checks()["run"]), encoding="utf-8")
    base = {
        "PATH": os.environ["PATH"],
        "HOME": str(cwd),
        "TEST_RESULTS_DIR": "",
        "SRC_DIR": "src",
        "TEST_DIR": "tests",
        "INCLUDE_SECURITY": "true",
        "ACTION_PATH": "/action path",
    }
    return subprocess.run(
        ("bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)),
        cwd=cwd,
        env={**base, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def _calls(cwd: Path) -> list[list[str]]:
    files = sorted(cwd.glob("uv.run.*.args"), key=lambda p: int(p.name.split(".")[2]))
    return [p.read_text(encoding="utf-8").splitlines() for p in files]


def _statuses(cwd: Path) -> list[tuple[str, str]]:
    rows = (cwd / "command_status.tsv").read_text(encoding="utf-8").splitlines()
    return [(row.split("\t")[0], row.split("\t")[3]) for row in rows]


def test_no_expression_is_interpolated_into_a_script() -> None:
    for step in _action()["runs"]["steps"]:
        assert "${{" not in cast(str, step.get("run", "")), step.get("name")


@pytest.mark.parametrize(
    ("variable", "expression"),
    [
        ("SRC_DIR", "${{ inputs.src-dir }}"),
        ("TEST_DIR", "${{ inputs.test-dir }}"),
        ("INCLUDE_SECURITY", "${{ inputs.include-security }}"),
        ("TEST_RESULTS_DIR", "${{ inputs.test-results-dir }}"),
        ("ACTION_PATH", "${{ github.action_path }}"),
    ],
)
def test_the_checks_get_their_inputs_from_the_environment(variable: str, expression: str) -> None:
    assert _checks()["env"][variable] == expression


@pytest.mark.parametrize(
    ("variable", "expression"),
    [
        ("COVERAGE_THRESHOLD", "${{ inputs.coverage-threshold }}"),
        ("FAIL_ON_QUALITY", "${{ inputs.fail-on-quality }}"),
        ("FAIL_ON_SECURITY", "${{ inputs.fail-on-security }}"),
        ("ACTION_PATH", "${{ github.action_path }}"),
    ],
)
def test_the_report_gets_its_inputs_from_the_environment(variable: str, expression: str) -> None:
    build = next(s for s in _action()["runs"]["steps"] if s.get("id") == "build")
    assert build["env"][variable] == expression
    assert f"${{{variable}}}" in cast(str, build["run"])


def test_every_check_gets_the_arguments_it_always_did(tmp_path: Path) -> None:
    result = _run_checks(tmp_path, {})
    assert result.returncode == 0, result.stderr
    requirements = ["--with-requirements", "/action path/requirements.txt"]
    assert _calls(tmp_path) == [
        ["run", *requirements, "ruff", "check", "src", "--output-format", "json"],
        ["run", *requirements, "pyright", "src", "--outputjson"],
        [
            "run",
            *requirements,
            "pytest",
            "tests",
            "--cov=src",
            "--cov-report=json:coverage.json",
            "--cov-report=xml:coverage.xml",
            "--junit-xml=junit.xml",
            "-o",
            "junit_family=legacy",
            "-v",
            "--tb=short",
        ],
        ["run", *requirements, "bandit", "-r", "src", "-f", "json", "-o", "bandit.json"],
    ]
    assert _statuses(tmp_path) == [
        ("ruff", "pass"),
        ("pyright", "pass"),
        ("pytest", "pass"),
        ("bandit", "pass"),
    ]


def test_a_lockfile_is_honoured(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").touch()
    assert _run_checks(tmp_path, {}).returncode == 0
    assert all(call[:2] == ["run", "--frozen"] for call in _calls(tmp_path))


def test_a_hostile_value_is_one_argument_and_never_runs(tmp_path: Path) -> None:
    result = _run_checks(tmp_path, {"SRC_DIR": HOSTILE, "TEST_DIR": HOSTILE})
    assert result.returncode == 0, result.stderr
    ruff, pyright, pytest_call, bandit = _calls(tmp_path)
    assert ruff[5] == pyright[4] == bandit[5] == HOSTILE
    assert pytest_call[4:6] == [HOSTILE, f"--cov={HOSTILE}"]
    assert not list(tmp_path.glob("pwned*"))


def test_the_source_dir_is_on_the_python_path_of_the_tests(tmp_path: Path) -> None:
    stub = UV_STUB.replace('"$@"', '"$@" "PYTHONPATH=${PYTHONPATH}"')
    script = tmp_path / ".checks.sh"
    script.write_text(stub + cast(str, _checks()["run"]), encoding="utf-8")
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "SRC_DIR": HOSTILE,
        "TEST_DIR": "tests",
        "INCLUDE_SECURITY": "false",
        "ACTION_PATH": "/a",
        "PYTHONPATH": "/before",
    }
    subprocess.run(("bash", str(script)), cwd=tmp_path, env=env, check=True, capture_output=True)
    assert _calls(tmp_path)[2][-1] == f"PYTHONPATH={HOSTILE}:/before"


def test_bandit_is_skipped_unless_asked(tmp_path: Path) -> None:
    assert _run_checks(tmp_path, {"INCLUDE_SECURITY": "false"}).returncode == 0
    assert [call[3] for call in _calls(tmp_path)] == ["ruff", "pyright", "pytest"]
    assert _statuses(tmp_path)[-1] == ("bandit", "skipped")


def test_a_failing_check_is_recorded_not_raised(tmp_path: Path) -> None:
    assert _run_checks(tmp_path, {"UV_EXIT": "1"}).returncode == 0
    assert {status for _, status in _statuses(tmp_path)} == {"fail"}
