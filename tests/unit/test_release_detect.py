"""The ``detect`` job of loom-actions' own release tells a release branch apart.

It decides whether a merge opens a release pull request or publishes one, so
its outputs are checked by running the step as the runner does.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import workflow_steps as wf


def _detect(head_ref: str, tmp_path: Path) -> list[str]:
    outputs = tmp_path / "outputs.txt"
    env = {"PR_HEAD_REF": head_ref, "PR_NUMBER": "42", "GITHUB_OUTPUT": str(outputs)}
    result = wf.run("release", "detect", "Detect merged PR metadata", env, tmp_path)
    assert result.returncode == 0, result.stderr
    return outputs.read_text("utf-8").splitlines()


def test_a_release_branch_publishes_its_version(tmp_path: Path) -> None:
    assert _detect("release/1.6.0", tmp_path) == [
        "branch=release/1.6.0",
        "pr_number=42",
        "is_release_branch=true",
        "release_version=1.6.0",
    ]


@pytest.mark.parametrize("head_ref", ["feature/release-on-label-monorepo", "fix/x"])
def test_any_other_branch_prepares_a_release(tmp_path: Path, head_ref: str) -> None:
    assert _detect(head_ref, tmp_path) == [
        f"branch={head_ref}",
        "pr_number=42",
        "is_release_branch=false",
        "release_version=",
    ]


def test_a_branch_name_is_data_not_script(tmp_path: Path) -> None:
    head_ref = 'feature/$(touch pwned)"`id`'
    assert _detect(head_ref, tmp_path)[0] == f"branch={head_ref}"
    assert not (tmp_path / "pwned").exists()
