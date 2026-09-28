"""``release-on-label`` releases a package that lives in a subdirectory.

A monorepo caller passes ``package-dir`` and ``semantic-branch-config``; with
the defaults, a caller at the root (loom-py) gets the lock check, the build,
the wheel name check and the artifact exactly as before. ``plan-release`` is
pinned by the commit of a release, so a caller's SHA pin on this workflow also
fixes the planner it runs.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import workflow_steps as wf
import yaml

ROOT = Path(__file__).parents[2]
NAME = "release-on-label"
PLAN_RELEASE = "the-reacher-data/loom-actions/actions/release/plan-release"
COMPOSITE = "actions/release/plan-release/action.yml"
PACKAGE_DIR = "${{ inputs.package-dir }}"
PINNED_LINE = re.compile(
    rf"uses: {re.escape(PLAN_RELEASE)}@(?P<sha>[0-9a-f]{{40}}) # (?P<tag>v\d+\.\d+\.\d+)$"
)


def _plan_step() -> dict[str, Any]:
    return next(s for s in wf.steps(NAME, "plan") if s.get("id") == "plan")


def _pin() -> re.Match[str]:
    text = (ROOT / ".github" / "workflows" / f"{NAME}.yml").read_text("utf-8")
    found = [m for line in text.splitlines() if (m := PINNED_LINE.search(line.strip()))]
    assert len(found) == 1, "plan-release is not pinned by a release commit"
    return found[0]


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(("git", "-C", str(ROOT), *args), capture_output=True, text=True)


def _composite_at(sha: str) -> dict[str, Any]:
    shown = _git("show", f"{sha}:{COMPOSITE}")
    if shown.returncode != 0:
        pytest.skip(f"commit {sha} is not in this clone")
    return cast(dict[str, Any], yaml.safe_load(shown.stdout))


def _composite_here() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load((ROOT / COMPOSITE).read_text("utf-8")))


class TestThePlannerIsPinned:
    def test_it_is_pinned_by_a_commit_that_names_its_release(self) -> None:
        pin = _pin()
        assert _plan_step()["uses"] == f"{PLAN_RELEASE}@{pin['sha']}"

    def test_the_named_release_is_that_commit(self) -> None:
        pin = _pin()
        tagged = _git("rev-parse", "--verify", "--quiet", f"refs/tags/{pin['tag']}^{{commit}}")
        if tagged.returncode != 0:
            pytest.skip(f"tag {pin['tag']} is not in this clone")
        assert tagged.stdout.strip() == pin["sha"]

    def test_the_pinned_planner_declares_every_input_passed(self) -> None:
        declared = set(_composite_at(_pin()["sha"])["inputs"])
        assert set(_plan_step()["with"]) - declared == set()

    def test_the_pinned_planner_takes_the_rules_file(self) -> None:
        declared = _composite_at(_pin()["sha"])["inputs"]["semantic-branch-config"]
        assert declared["default"] == "pyproject.toml"


class TestDefaultsAreUnchanged:
    """Left alone, the new inputs reproduce what a root caller had."""

    @pytest.mark.parametrize(
        ("name", "kind", "default"),
        [
            ("package-dir", "string", "."),
            ("semantic-branch-config", "string", "pyproject.toml"),
            ("check-distribution", "boolean", False),
        ],
    )
    def test_the_new_inputs_are_optional(self, name: str, kind: str, default: object) -> None:
        declared = wf.call(NAME)["inputs"][name]
        assert declared["type"] == kind
        assert declared["default"] == default
        assert declared["required"] is False

    def test_the_rules_file_defaults_to_the_planners_own_default(self) -> None:
        mine = wf.call(NAME)["inputs"]["semantic-branch-config"]["default"]
        assert mine == _composite_here()["inputs"]["semantic-branch-config"]["default"]

    def test_the_rules_file_reaches_the_planner(self) -> None:
        assert _plan_step()["with"]["semantic-branch-config"] == (
            "${{ inputs.semantic-branch-config }}"
        )

    def test_the_root_package_uploads_the_path_it_always_did(self) -> None:
        upload = wf.step(NAME, "build", "Store distributions")
        assert upload["with"]["path"] == (
            "${{ inputs.package-dir == '.' && 'dist/' || format('{0}/dist/', inputs.package-dir) }}"
        )
        assert upload["with"]["name"] == "distributions"

    def test_nothing_is_checked_unless_the_caller_asks(self) -> None:
        assert wf.step(NAME, "build", "Check the distributions")["if"] == (
            "${{ inputs.check-distribution }}"
        )

    def test_the_jobs_are_the_same(self) -> None:
        assert list(wf.jobs(NAME)) == ["plan", "build", "release"]

    def test_no_expression_is_interpolated_into_a_script(self) -> None:
        for job in wf.jobs(NAME):
            for each in wf.steps(NAME, job):
                assert "${{" not in cast(str, each.get("run", "")), f"{job}: {each.get('name')}"


class TestPackageDir:
    @pytest.mark.parametrize(
        "title",
        ["Build package", "Require the built version to match the tag", "Check the distributions"],
    )
    def test_the_package_steps_run_in_the_package_dir(self, title: str) -> None:
        assert wf.step(NAME, "build", title)["working-directory"] == PACKAGE_DIR

    def test_the_check_runs_before_the_distributions_are_stored(self) -> None:
        titles = [s.get("name") for s in wf.steps(NAME, "build")]
        assert titles.index("Check the distributions") < titles.index("Store distributions")


def _stub_uv(bin_dir: Path, log: Path, build: str = "") -> dict[str, str]:
    """Put a ``uv`` on PATH that logs its arguments and, for ``run``, runs *build*."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s|%s\\n" "$PWD" "$*" >> "{log}"\n'
        f'if [ "$1" = run ]; then {build or ":"}; fi\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def _repository(root: Path, package: str) -> Path:
    project = root / package
    project.mkdir(parents=True, exist_ok=True)
    (project / "pyproject.toml").write_text("[project]\nname = 'acme-api'\n", encoding="utf-8")
    (root / "README.md").write_text("root\n", encoding="utf-8")
    for command in (
        ("init", "-q"),
        ("add", "-A"),
        ("-c", "user.name=t", "-c", "user.email=t@localhost", "commit", "-qm", "init"),
    ):
        subprocess.run(("git", "-C", str(root), *command), check=True, capture_output=True)
    return project


class TestTheBuild:
    """The build step, run as the runner runs it in ``package-dir``."""

    @pytest.mark.parametrize("package", [".", "apps/api"])
    def test_it_checks_the_lock_and_builds_in_the_package(
        self, tmp_path: Path, package: str
    ) -> None:
        repo = tmp_path / "repo"
        project = _repository(repo, package)
        log = tmp_path / "uv.log"
        env = _stub_uv(tmp_path / "bin", log, "mkdir -p dist && : > dist/acme_api-1.2.3.whl")
        result = wf.run(NAME, "build", "Build package", env, project)
        assert result.returncode == 0, result.stdout + result.stderr
        assert log.read_text("utf-8").splitlines() == [
            f"{project.resolve()}|lock --check",
            f"{project.resolve()}|run --frozen --with build==1.3.0 python -m build",
        ]

    def test_a_build_that_edits_a_tracked_file_outside_the_package_fails(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        project = _repository(repo, "apps/api")
        env = _stub_uv(tmp_path / "bin", tmp_path / "uv.log", f"echo x >> {repo}/README.md")
        result = wf.run(NAME, "build", "Build package", env, project)
        assert result.returncode == 1
        assert "Package build modified tracked files." in result.stdout


class TestTheWheelName:
    def _check(self, tmp_path: Path, package: str, wheel: str) -> subprocess.CompletedProcess[str]:
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / wheel).touch()
        env = {"PACKAGE_NAME": package, "VERSION": "1.2.3"}
        return wf.run(NAME, "build", "Require the built version to match the tag", env, tmp_path)

    @pytest.mark.parametrize(
        ("package", "wheel"),
        [
            ("periplo", "periplo-1.2.3-py3-none-any.whl"),
            ("loom-kernel", "loom_kernel-1.2.3-py3-none-any.whl"),
        ],
    )
    def test_the_tagged_version_passes(self, tmp_path: Path, package: str, wheel: str) -> None:
        assert self._check(tmp_path, package, wheel).returncode == 0

    def test_a_version_that_never_saw_the_tag_fails(self, tmp_path: Path) -> None:
        result = self._check(tmp_path, "periplo", "periplo-0.1.dev3-py3-none-any.whl")
        assert result.returncode == 1
        assert "do not carry version 1.2.3" in result.stdout


class TestTheDistributionCheck:
    def test_it_runs_a_pinned_twine_strictly_over_every_distribution(self, tmp_path: Path) -> None:
        (tmp_path / "dist").mkdir()
        for name in ("acme_api-1.2.3-py3-none-any.whl", "acme_api-1.2.3.tar.gz"):
            (tmp_path / "dist" / name).touch()
        log = tmp_path / "uv.log"
        check = wf.step(NAME, "build", "Check the distributions")
        env = {**_stub_uv(tmp_path / "bin", log), **check["env"]}
        result = wf.run(NAME, "build", "Check the distributions", env, tmp_path)
        assert result.returncode == 0, result.stderr
        assert log.read_text("utf-8").splitlines() == [
            f"{tmp_path.resolve()}|tool run --from twine==7.0.0 twine check --strict "
            "dist/acme_api-1.2.3-py3-none-any.whl dist/acme_api-1.2.3.tar.gz"
        ]

    def test_a_failing_check_fails_the_step(self, tmp_path: Path) -> None:
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "x.whl").touch()
        check = wf.step(NAME, "build", "Check the distributions")
        env = {**_stub_uv(tmp_path / "bin", tmp_path / "uv.log"), **check["env"]}
        bad = tmp_path / "bin" / "uv"
        bad.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
        assert wf.run(NAME, "build", "Check the distributions", env, tmp_path).returncode == 1


def test_the_last_build_step_reports_the_distribution(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs.txt"
    result = wf.run(
        NAME, "build", "Report the distribution", {"GITHUB_OUTPUT": str(outputs)}, tmp_path
    )
    assert result.returncode == 0
    assert outputs.read_text("utf-8") == "built=true\n"
