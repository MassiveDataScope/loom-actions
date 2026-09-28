"""The gate must reach the same verdict on test results the caller supplied.

``quality-report`` records a ``reused`` row for pytest when ``test-results-dir``
is set, so these tests pin the property that makes the input safe: a failed test
and coverage under the threshold still block, because both are read from the
supplied files and not from the exit code of a pytest the action ran.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

CLEAN_RUFF = "[]\n"

CLEAN_PYRIGHT = """{
  "generalDiagnostics": [],
  "summary": {"errorCount": 0, "warningCount": 0, "informationCount": 0}
}
"""

CLEAN_BANDIT = '{"results": []}\n'

PASSING_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="2" failures="0" errors="0" skipped="0">
    <testcase classname="tests.test_add" name="test_adds"/>
    <testcase classname="tests.test_add" name="test_adds_negative"/>
  </testsuite>
</testsuites>
"""

FAILING_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="2" failures="1" errors="0" skipped="0">
    <testcase classname="tests.test_add" name="test_adds"/>
    <testcase classname="tests.test_add" name="test_adds_negative">
      <failure message="assert 0 == -2">boom</failure>
    </testcase>
  </testsuite>
</testsuites>
"""

COVERED_JSON = """{
  "totals": {"percent_covered": 95.0},
  "files": {"src/sample_pkg/__init__.py": {"summary": {"percent_covered": 95.0}}}
}
"""

UNCOVERED_JSON = """{
  "totals": {"percent_covered": 41.0},
  "files": {"src/sample_pkg/__init__.py": {"summary": {"percent_covered": 41.0}}}
}
"""

REUSED_COMMANDS = (
    "ruff\truff check src --output-format json\t0\tpass\truff.json\n"
    "pyright\tpyright src --outputjson\t0\tpass\tpyright.json\n"
    "pytest\ttest results supplied by the caller\t0\treused\t"
    "junit.xml,coverage.json,coverage.xml\n"
    "bandit\tbandit -r src -f json -o bandit.json\t0\tpass\tbandit.json\n"
)


def _run_builder(tmp_path: Path, junit: str, coverage: str) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[2]
    builder = repo_root / "actions/python/quality-report/src/builder.py"
    template = repo_root / "actions/python/quality-report/src/templates/report.md.j2"

    (tmp_path / "ruff.json").write_text(CLEAN_RUFF, encoding="utf-8")
    (tmp_path / "pyright.json").write_text(CLEAN_PYRIGHT, encoding="utf-8")
    (tmp_path / "bandit.json").write_text(CLEAN_BANDIT, encoding="utf-8")
    (tmp_path / "junit.xml").write_text(junit, encoding="utf-8")
    (tmp_path / "coverage.json").write_text(coverage, encoding="utf-8")
    (tmp_path / "command_status.tsv").write_text(REUSED_COMMANDS, encoding="utf-8")
    (tmp_path / "gh_outputs.txt").write_text("", encoding="utf-8")

    cmd = [
        sys.executable,
        str(builder),
        "--ruff",
        str(tmp_path / "ruff.json"),
        "--pyright",
        str(tmp_path / "pyright.json"),
        "--junit",
        str(tmp_path / "junit.xml"),
        "--coverage",
        str(tmp_path / "coverage.json"),
        "--bandit",
        str(tmp_path / "bandit.json"),
        "--commands",
        str(tmp_path / "command_status.tsv"),
        "--template",
        str(template),
        "--output",
        str(tmp_path / "quality_report.md"),
        "--summary",
        str(tmp_path / "quality_summary.json"),
        "--outputs",
        str(tmp_path / "gh_outputs.txt"),
        "--coverage-threshold",
        "80",
        "--fail-on-quality",
        "any",
        "--fail-on-security",
        "high",
    ]
    return subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True, check=False)


def _summary(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "quality_summary.json").read_text(encoding="utf-8"))


def test_supplied_results_that_pass_do_not_block(tmp_path: Path) -> None:
    result = _run_builder(tmp_path, PASSING_JUNIT, COVERED_JSON)

    summary = _summary(tmp_path)
    assert result.returncode == 0
    assert summary["gates"]["blocking"] is False
    assert summary["summary"]["tests_total"] == 2


def test_a_failed_test_in_supplied_results_still_blocks(tmp_path: Path) -> None:
    result = _run_builder(tmp_path, FAILING_JUNIT, COVERED_JSON)

    summary = _summary(tmp_path)
    assert result.returncode == 1
    assert summary["gates"]["quality_blocking"] is True
    assert summary["summary"]["tests_failed"] == 1


def test_coverage_below_the_threshold_in_supplied_results_still_blocks(tmp_path: Path) -> None:
    result = _run_builder(tmp_path, PASSING_JUNIT, UNCOVERED_JSON)

    summary = _summary(tmp_path)
    assert result.returncode == 1
    assert summary["gates"]["quality_blocking"] is True
    assert summary["summary"]["coverage"] == 41.0


def _tool_row(tmp_path: Path, tool: str) -> str:
    report = (tmp_path / "quality_report.md").read_text(encoding="utf-8")
    return next(line for line in report.splitlines() if line.startswith(f"| `{tool}` |"))


def test_supplied_results_are_shown_as_reused_not_failed(tmp_path: Path) -> None:
    _run_builder(tmp_path, PASSING_JUNIT, COVERED_JSON)

    row = _tool_row(tmp_path, "pytest")
    assert "reused" in row
    assert "fail" not in row


def test_a_failed_test_in_supplied_results_is_shown_in_the_gate(tmp_path: Path) -> None:
    """The row says where the results came from; the verdict is the gate's."""
    _run_builder(tmp_path, FAILING_JUNIT, COVERED_JSON)

    report = (tmp_path / "quality_report.md").read_text(encoding="utf-8")
    assert "| Quality gate | ❌ fail |" in report
    assert "reused" in _tool_row(tmp_path, "pytest")
