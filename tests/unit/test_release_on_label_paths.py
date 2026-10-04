"""``release-on-label`` scopes a monorepo package's release to its own paths, when asked.

Scoping is opt-in: with ``scope-to-package: true`` the ``package-dir`` and the
optional ``shared-paths``, such as ``uv.lock``, are passed to the planner as the
release scope, and another package's pull requests never raise the package's
version nor reach its changelog. Left false, the default, no path is passed
whatever ``package-dir`` is, and the planner ships every commit, as it always
did: periplo releases ``package-dir: apps/api`` from an image built at the root,
so its release must keep reading every commit. The step runs here as the runner
runs it, and its output is read back through the planner's own parser.
"""

from __future__ import annotations

import subprocess
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


def _run_scope(
    tmp_path: Path, package_dir: str, shared_paths: str = "", scoped: bool = True
) -> subprocess.CompletedProcess[str]:
    env = {
        "PACKAGE_DIR": package_dir,
        "SHARED_PATHS": shared_paths,
        "SCOPE_TO_PACKAGE": "true" if scoped else "false",
        "GITHUB_OUTPUT": str(tmp_path / "output"),
    }
    return wf.run(NAME, "plan", SCOPE, env, tmp_path)


def _scope(tmp_path: Path, package_dir: str, shared_paths: str = "", scoped: bool = True) -> str:
    result = _run_scope(tmp_path, package_dir, shared_paths, scoped)
    assert result.returncode == 0, result.stdout + result.stderr
    return (tmp_path / "output").read_text("utf-8")


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

    def test_scope_to_package_is_an_optional_boolean_defaulting_to_false(self) -> None:
        declared = wf.call(NAME)["inputs"]["scope-to-package"]
        assert declared["type"] == "boolean"
        assert declared["required"] is False
        assert declared["default"] is False

    def test_the_planner_reads_the_scope_the_step_reports(self) -> None:
        step = wf.step(NAME, "plan", SCOPE)
        assert step["id"] == "scope"
        assert step["env"] == {
            "PACKAGE_DIR": "${{ inputs.package-dir }}",
            "SHARED_PATHS": "${{ inputs.shared-paths }}",
            "SCOPE_TO_PACKAGE": "${{ inputs.scope-to-package }}",
        }
        assert _plan_step()["with"]["paths"] == "${{ steps.scope.outputs.paths }}"

    def test_the_scope_is_known_before_the_plan(self) -> None:
        titles = [cast(str, s.get("name", "")) for s in wf.steps(NAME, "plan")]
        assert titles.index(SCOPE) == titles.index("Plan the release") - 1


class TestUnscopedIsUnchanged:
    """Without scope-to-package every caller ships every commit, as before."""

    @pytest.mark.parametrize("package_dir", [".", "", "apps/api", "apps/control-plane"])
    def test_it_passes_no_path_whatever_the_package_dir(
        self, tmp_path: Path, package_dir: str
    ) -> None:
        assert _scope(tmp_path, package_dir, scoped=False) == "paths=\n"

    def test_the_planner_then_ships_every_commit(self, tmp_path: Path) -> None:
        assert _paths(_scope(tmp_path, "apps/api", scoped=False)) == ()

    def test_shared_paths_without_scope_to_package_are_refused(self, tmp_path: Path) -> None:
        result = _run_scope(tmp_path, "apps/api", "uv.lock", scoped=False)

        assert result.returncode != 0
        assert "shared-paths is read only with scope-to-package: true" in result.stdout

    @pytest.mark.parametrize("package_dir", [".", ""])
    def test_scoping_the_root_package_is_refused(self, tmp_path: Path, package_dir: str) -> None:
        result = _run_scope(tmp_path, package_dir)

        assert result.returncode != 0
        assert "scope-to-package needs a package-dir other than the root" in result.stdout


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


RELEASED = "steps.plan.outputs.version != ''"


class TestNothingToRelease:
    """A scoped merge touching none of the package's paths plans an empty version.

    The planner then succeeds, says why in the step summary and writes no notes;
    the workflow writes no tag, builds nothing and publishes no release, and the
    run is green.
    """

    @pytest.mark.parametrize("title", ["Store release notes", "Create the immutable version tag"])
    def test_the_plan_job_writes_nothing_after_the_plan(self, title: str) -> None:
        assert wf.step(NAME, "plan", title)["if"] == f"${{{{ {RELEASED} }}}}"

    def test_no_build_and_no_release_run(self) -> None:
        jobs = wf.jobs(NAME)
        assert jobs["build"]["if"] == (
            "${{ inputs.build-distribution && needs.plan.outputs.version != '' }}"
        )
        assert jobs["release"]["if"] == (
            "${{ always() && needs.plan.result == 'success' && needs.build.result != 'failure'"
            " && needs.plan.outputs.version != '' }}"
        )
