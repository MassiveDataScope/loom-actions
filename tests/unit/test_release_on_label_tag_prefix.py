"""``release-on-label`` creates, reads and moves the tags of one prefix.

Its ``tag-prefix`` input, ``v`` by default, reaches the planner, the immutable
version tag, the floating major tag, the build checkout and the GitHub Release.
A path-style prefix such as ``control-plane/v`` (the Go modules convention)
names ``control-plane/v0.1.0`` and moves ``control-plane/v0``. The prefix is
checked with the planner's own rule before anything is written, and the tag
scripts are run here as the runner runs them, with a ``gh`` on PATH that logs
every call and answers the refs GitHub would.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, cast

import pytest
import workflow_steps as wf
import yaml

sys.path.insert(0, str(Path(__file__).parents[2] / "actions" / "release" / "plan-release" / "src"))

from release_tags import TagPrefixError, check_tag_prefix  # noqa: E402

NAME = "release-on-label"
COMPOSITE = Path(__file__).parents[2] / "actions" / "release" / "plan-release" / "action.yml"
PREFIX = "${{ inputs.tag-prefix }}"
TAG = "${{ inputs.tag-prefix }}${{ needs.plan.outputs.version }}"
CHECK = "Require a valid tag prefix"
CREATE = "Create the immutable version tag"
MOVE = "Update floating major tag"
MERGE_SHA = "a" * 40
OTHER_SHA = "b" * 40


def _plan_step() -> dict[str, Any]:
    return next(s for s in wf.steps(NAME, "plan") if s.get("id") == "plan")


def _titles(job: str) -> list[str]:
    return [cast(str, s.get("name", "")) for s in wf.steps(NAME, job)]


class TestTheInput:
    def test_it_is_an_optional_string_defaulting_to_v(self) -> None:
        declared = wf.call(NAME)["inputs"]["tag-prefix"]
        assert declared["type"] == "string"
        assert declared["default"] == "v"
        assert declared["required"] is False

    def test_it_defaults_to_the_planners_own_default(self) -> None:
        planner = yaml.safe_load(COMPOSITE.read_text("utf-8"))
        assert (
            wf.call(NAME)["inputs"]["tag-prefix"]["default"]
            == (planner["inputs"]["tag-prefix"]["default"])
        )

    def test_it_reaches_the_planner(self) -> None:
        assert _plan_step()["with"]["tag-prefix"] == PREFIX


class TestEveryTagCarriesThePrefix:
    @pytest.mark.parametrize(("job", "title"), [("plan", CREATE), ("release", MOVE)])
    def test_the_tag_scripts_read_the_prefix_from_the_environment(
        self, job: str, title: str
    ) -> None:
        step = wf.step(NAME, job, title)
        assert step["env"]["TAG_PREFIX"] == PREFIX
        script = cast(str, step["run"])
        assert "${TAG_PREFIX}" in script
        assert "/v${" not in script and '"v${' not in script and " v${" not in script

    def test_the_build_checks_out_the_prefixed_tag(self) -> None:
        assert wf.steps(NAME, "build")[0]["with"]["ref"] == TAG

    def test_the_release_is_made_on_the_prefixed_tag(self) -> None:
        release = wf.step(NAME, "release", "Create GitHub release")
        assert release["with"]["tag_name"] == TAG
        assert release["with"]["name"] == TAG

    def test_no_step_names_a_bare_v_tag(self) -> None:
        for job in wf.jobs(NAME):
            for each in wf.steps(NAME, job):
                for value in (each.get("with") or {}).values():
                    assert not str(value).startswith("v${{"), f"{job}: {each.get('name')}"


class TestThePrefixIsCheckedFirst:
    def test_it_is_checked_before_the_planner_and_any_tag(self) -> None:
        titles = _titles("plan")
        assert titles[0] == CHECK
        assert titles.index(CHECK) < titles.index("Plan the release") < titles.index(CREATE)

    def test_the_check_reads_the_prefix_from_the_environment(self) -> None:
        assert wf.step(NAME, "plan", CHECK)["env"]["TAG_PREFIX"] == PREFIX

    @pytest.mark.parametrize(
        "prefix",
        [
            "v",
            "api-v",
            "control-plane/v",
            "apps/api/v",
            "pkg_1.v",
            "0v",
            "",
            "-v",
            ".v",
            "/v",
            "a..b",
            "a//b",
            "a/.b",
            "a.lock/v",
            "a b",
            "a\nb",
            "v*",
            "v~",
            "v:",
            "v^",
            "v?",
            "v[",
            "v\\",
            "v@{",
            "$(id)",
            "é",
        ],
    )
    def test_it_agrees_with_the_planners_rule(self, tmp_path: Path, prefix: str) -> None:
        try:
            check_tag_prefix(prefix)
            allowed = True
        except TagPrefixError:
            allowed = False
        result = wf.run(NAME, "plan", CHECK, {"TAG_PREFIX": prefix}, tmp_path)
        assert (result.returncode == 0) is allowed, result.stdout + result.stderr


def _stub_gh(tmp_path: Path, refs: list[dict[str, Any]]) -> tuple[dict[str, str], Path]:
    """Put a ``gh`` on PATH that logs each call and answers every GET with *refs*."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh.log"
    answer = tmp_path / "refs.json"
    answer.write_text(json.dumps(refs), encoding="utf-8")
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        f'case " $* " in *" --method "*) ;; *) cat "{answer}" ;; esac\n',
        encoding="utf-8",
    )
    gh.chmod(0o755)
    path = f"{bin_dir}:{os.environ['PATH']}"
    return {"PATH": path, "GH_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}, log


def _ref(name: str, sha: str) -> dict[str, Any]:
    return {"ref": f"refs/tags/{name}", "object": {"sha": sha, "type": "commit"}}


class TestTheVersionTag:
    def _create(
        self, tmp_path: Path, prefix: str, version: str, refs: list[dict[str, Any]]
    ) -> tuple[int, list[str], str]:
        env, log = _stub_gh(tmp_path, refs)
        env |= {"TAG_PREFIX": prefix, "VERSION": version, "MERGE_SHA": MERGE_SHA}
        result = wf.run(NAME, "plan", CREATE, env, tmp_path)
        calls = log.read_text("utf-8").splitlines() if log.exists() else []
        return result.returncode, calls, result.stdout + result.stderr

    def test_the_default_prefix_creates_v_tags_as_before(self, tmp_path: Path) -> None:
        code, calls, _ = self._create(tmp_path, "v", "1.2.3", [])
        assert code == 0
        assert calls == [
            "api repos/o/r/git/matching-refs/tags/v1.2.3",
            f"api --method POST repos/o/r/git/refs -f ref=refs/tags/v1.2.3 -f sha={MERGE_SHA}",
        ]

    def test_a_path_style_prefix_creates_its_own_tag(self, tmp_path: Path) -> None:
        refs = [
            _ref("control-plane/v0.1.0-rc.1", OTHER_SHA),
            _ref("control-plane/v0.1.01", OTHER_SHA),
        ]
        code, calls, _ = self._create(tmp_path, "control-plane/v", "0.1.0", refs)
        assert code == 0
        assert calls == [
            "api repos/o/r/git/matching-refs/tags/control-plane/v0.1.0",
            "api --method POST repos/o/r/git/refs "
            f"-f ref=refs/tags/control-plane/v0.1.0 -f sha={MERGE_SHA}",
        ]

    def test_the_same_tag_on_the_release_commit_is_kept(self, tmp_path: Path) -> None:
        refs = [_ref("control-plane/v0.1.0", MERGE_SHA)]
        code, calls, out = self._create(tmp_path, "control-plane/v", "0.1.0", refs)
        assert code == 0
        assert len(calls) == 1
        assert "Tag control-plane/v0.1.0 already points at the release commit." in out

    def test_the_same_tag_on_another_commit_refuses(self, tmp_path: Path) -> None:
        refs = [_ref("control-plane/v0.1.0", OTHER_SHA)]
        code, calls, out = self._create(tmp_path, "control-plane/v", "0.1.0", refs)
        assert code == 1
        assert len(calls) == 1
        assert f"Tag control-plane/v0.1.0 points to {OTHER_SHA}, expected {MERGE_SHA}." in out

    def test_another_packages_tag_of_that_version_is_ignored(self, tmp_path: Path) -> None:
        """matching-refs is a prefix match; only the exact ref counts."""
        code, calls, _ = self._create(tmp_path, "api-v", "1.2.3", [_ref("api-v1.2.3.4", OTHER_SHA)])
        assert code == 0
        assert calls[-1].startswith("api --method POST")


class TestTheMajorTag:
    def _move(
        self, tmp_path: Path, prefix: str, version: str, refs: list[dict[str, Any]]
    ) -> tuple[int, list[str]]:
        env, log = _stub_gh(tmp_path, refs)
        env |= {"TAG_PREFIX": prefix, "VERSION": version, "RELEASE_SHA": MERGE_SHA}
        result = wf.run(NAME, "release", MOVE, env, tmp_path)
        return result.returncode, log.read_text("utf-8").splitlines()

    def test_the_default_prefix_moves_v_major_as_before(self, tmp_path: Path) -> None:
        code, calls = self._move(tmp_path, "v", "1.2.3", [_ref("v1", OTHER_SHA)])
        assert code == 0
        assert calls == [
            "api repos/o/r/git/matching-refs/tags/v1",
            f"api --method PATCH repos/o/r/git/refs/tags/v1 -f sha={MERGE_SHA} -F force=true",
        ]

    def test_a_path_style_prefix_moves_its_own_major(self, tmp_path: Path) -> None:
        refs = [_ref("control-plane/v0", OTHER_SHA), _ref("control-plane/v0.1.0", MERGE_SHA)]
        code, calls = self._move(tmp_path, "control-plane/v", "0.1.0", refs)
        assert code == 0
        assert calls == [
            "api repos/o/r/git/matching-refs/tags/control-plane/v0",
            "api --method PATCH repos/o/r/git/refs/tags/control-plane/v0 "
            f"-f sha={MERGE_SHA} -F force=true",
        ]

    def test_a_missing_major_is_created_with_the_prefix(self, tmp_path: Path) -> None:
        refs = [_ref("control-plane/v0.1.0", MERGE_SHA)]
        code, calls = self._move(tmp_path, "control-plane/v", "0.1.0", refs)
        assert code == 0
        assert calls[-1] == (
            "api --method POST repos/o/r/git/refs "
            f"-f ref=refs/tags/control-plane/v0 -f sha={MERGE_SHA}"
        )
