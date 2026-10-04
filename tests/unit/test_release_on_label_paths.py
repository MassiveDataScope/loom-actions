"""``release-on-label`` scopes a monorepo package's release to its own paths.

A ``package-dir`` other than the root is passed to the planner as the release
scope, with the optional ``shared-paths`` it shares with the other packages,
such as ``uv.lock``; another package's pull requests then never raise its
version nor reach its changelog. A root package — no ``package-dir``, or ``.``
— passes no path, and the planner ships every commit, as it always did. The
step runs here as the runner runs it, and its output is read back through the
planner's own parser.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, cast

import pytest
import workflow_steps as wf

sys.path.insert(0, str(Path(__file__).parents[2] / "actions" / "release" / "plan-release" / "src"))

from release_scope import ReleaseScope  # noqa: E402

NAME = "release-on-label"
SCOPE = "Scope the release to the package"


def _plan_step() -> dict[str, Any]:
    return next(s for s in wf.steps(NAME, "plan") if s.get("id") == "plan")


def _scope(tmp_path: Path, package_dir: str, shared_paths: str = "") -> str:
    output = tmp_path / "output"
    env = {
        "PACKAGE_DIR": package_dir,
        "SHARED_PATHS": shared_paths,
        "GITHUB_OUTPUT": str(output),
    }
    result = wf.run(NAME, "plan", SCOPE, env, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    return output.read_text("utf-8")


def _paths(output: str) -> tuple[str, ...]:
    (line,) = output.splitlines()
    key, _, value = line.partition("=")
    assert key == "paths"
    return ReleaseScope.parse("v", value).paths


class TestTheInput:
    def test_shared_paths_is_an_optional_string_defaulting_to_empty(self) -> None:
        declared = wf.call(NAME)["inputs"]["shared-paths"]
        assert declared["type"] == "string"
        assert declared["required"] is False
        assert declared["default"] == ""

    def test_the_planner_reads_the_scope_the_step_reports(self) -> None:
        step = wf.step(NAME, "plan", SCOPE)
        assert step["id"] == "scope"
        assert step["env"] == {
            "PACKAGE_DIR": "${{ inputs.package-dir }}",
            "SHARED_PATHS": "${{ inputs.shared-paths }}",
        }
        assert _plan_step()["with"]["paths"] == "${{ steps.scope.outputs.paths }}"

    def test_the_scope_is_known_before_the_plan(self) -> None:
        titles = [cast(str, s.get("name", "")) for s in wf.steps(NAME, "plan")]
        assert titles.index(SCOPE) == titles.index("Plan the release") - 1


class TestARootPackageIsUnchanged:
    @pytest.mark.parametrize("package_dir", [".", ""])
    def test_it_passes_no_path(self, tmp_path: Path, package_dir: str) -> None:
        assert _scope(tmp_path, package_dir) == "paths=\n"

    def test_shared_paths_do_not_scope_it(self, tmp_path: Path) -> None:
        assert _scope(tmp_path, ".", "uv.lock") == "paths=\n"

    def test_the_planner_then_ships_every_commit(self, tmp_path: Path) -> None:
        assert _paths(_scope(tmp_path, ".")) == ()


class TestAMonorepoPackage:
    def test_it_ships_the_commits_of_its_directory(self, tmp_path: Path) -> None:
        output = _scope(tmp_path, "apps/control-plane")

        assert output == "paths=apps/control-plane\n"
        assert _paths(output) == ("apps/control-plane",)

    def test_it_adds_the_paths_it_shares(self, tmp_path: Path) -> None:
        output = _scope(tmp_path, "apps/control-plane", "uv.lock\r\nlibs/common, pyproject.toml\n")

        assert _paths(output) == ("apps/control-plane", "uv.lock", "libs/common", "pyproject.toml")

    def test_a_newline_cannot_write_another_output(self, tmp_path: Path) -> None:
        output = _scope(tmp_path, "apps/a", "uv.lock\nversion=9.9.9\n")

        assert len(output.splitlines()) == 1
        assert output.startswith("paths=apps/a,uv.lock,version=9.9.9")
