"""``plan-release`` updates a Keep a Changelog file only when a caller names one.

Left empty, ``changelog-file`` runs the steps a caller always ran, with the same
scripts, and writes the same notes byte for byte. Named, the release section the
file gains becomes the notes, so the GitHub Release body and the changelog say
the same thing. The steps are run as the runner runs them, with a ``gh`` stub.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).parents[2]
ACTION_DIR = ROOT / "actions" / "release" / "plan-release"

PLAN = "Plan the release"
NOTES = "Write the release notes"
CHANGELOG = "Update the changelog"

# The scripts as they stood before changelog-file existed (v1.10.1).
PLAN_SCRIPT = (
    'set -euo pipefail\npython3 "${ACTION_PATH}/src/plan_release.py" \\\n'
    '  --merge-sha "${MERGE_SHA}" --slug "${REPOSITORY_SLUG}" \\\n'
    '  --semantic-branch-config "${SEMANTIC_BRANCH_CONFIG}" \\\n'
    '  --tag-prefix="${TAG_PREFIX}" | tee plan.txt\n'
    'PLAN=$(python3 "${ACTION_PATH}/src/plan_release.py" \\\n'
    '  --merge-sha "${MERGE_SHA}" --slug "${REPOSITORY_SLUG}" \\\n'
    '  --semantic-branch-config "${SEMANTIC_BRANCH_CONFIG}" \\\n'
    '  --tag-prefix="${TAG_PREFIX}" --format github)\n'
    'VERSION=$(jq -r .version <<< "${PLAN}")\nPART=$(jq -r .part <<< "${PLAN}")\n'
    'echo "version=${VERSION}" >> "$GITHUB_OUTPUT"\necho "part=${PART}" >> "$GITHUB_OUTPUT"\n'
    "{\n  echo \"## Release ${TAG_PREFIX}${VERSION}\"\n  echo\n  echo '```'\n"
    "  cat plan.txt\n  echo '```'\n} >> \"$GITHUB_STEP_SUMMARY\"\n"
)
NOTES_SCRIPT = (
    'set -euo pipefail\ngit checkout --detach "${MERGE_SHA}"\n'
    'python3 "${ACTION_PATH}/src/build_release_notes.py" \\\n'
    '  --version "${VERSION}" --output "${NOTES_OUTPUT}" --tag-prefix="${TAG_PREFIX}"\n'
)


def _action() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load((ACTION_DIR / "action.yml").read_text("utf-8")))


def _steps() -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], _action()["runs"]["steps"])


def _step(title: str) -> dict[str, Any]:
    return next(s for s in _steps() if s["name"] == title)


def _git(repository: Path, *arguments: str) -> str:
    env = {**os.environ, "GIT_COMMITTER_DATE": "2026-10-04T10:25:43Z"}
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=master")
    _git(repository, "config", "user.email", "release@example.com")
    _git(repository, "config", "user.name", "Release")
    for message in ("chore: initial", "feat(api): one", "test(api): two"):
        (repository / f"{len(list(repository.iterdir()))}.txt").write_text(message, "utf-8")
        _git(repository, "add", ".")
        _git(repository, "commit", "-m", message)
        if message == "chore: initial":
            _git(repository, "tag", "api/v1.10.0")
    return repository, _git(repository, "rev-parse", "HEAD")


def _stub_gh(tmp_path: Path, answer: list[dict[str, Any]]) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!/usr/bin/env bash\ncat <<'JSON'\n{json.dumps(answer)}\nJSON\n", "utf-8")
    gh.chmod(0o755)
    return f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


def _run_step(title: str, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    script = cwd.parent / "step.sh"
    script.write_text(cast(str, _step(title)["run"]), encoding="utf-8")
    return subprocess.run(
        ("bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)),
        cwd=cwd,
        env={"HOME": os.environ.get("HOME", str(cwd)), "PATH": os.environ["PATH"], **env},
        capture_output=True,
        text=True,
        check=False,
    )


class TestTheDefaultIsUnchanged:
    def test_the_input_is_optional_and_empty(self) -> None:
        declared = _action()["inputs"]["changelog-file"]
        assert declared["required"] is False
        assert declared["default"] == ""

    def test_the_plan_and_notes_scripts_are_the_ones_callers_ran(self) -> None:
        assert _step(PLAN)["run"] == PLAN_SCRIPT
        assert _step(NOTES)["run"] == NOTES_SCRIPT

    def test_the_notes_are_written_unless_a_changelog_is_named(self) -> None:
        assert _step(NOTES)["if"] == "${{ inputs.changelog-file == '' }}"
        assert _step(CHANGELOG)["if"] == "${{ inputs.changelog-file != '' }}"

    def test_the_default_notes_are_byte_identical(self, tmp_path: Path) -> None:
        repository, merge = _repository(tmp_path)
        env = {
            "MERGE_SHA": merge,
            "VERSION": "1.11.0",
            "NOTES_OUTPUT": "CHANGELOG_RELEASE.md",
            "TAG_PREFIX": "api/v",
            "ACTION_PATH": str(ACTION_DIR),
        }

        result = _run_step(NOTES, env, repository)

        assert result.returncode == 0, result.stderr
        assert (repository / "CHANGELOG_RELEASE.md").read_text("utf-8") == (
            "# 🚀 Release 1.11.0\n"
            "\n"
            "Changes since api/v1.10.0:\n"
            "\n"
            "- test(api): two\n"
            "- feat(api): one\n"
        )


class TestTheChangelogStep:
    def test_it_runs_after_the_plan_and_instead_of_the_notes(self) -> None:
        titles = [s["name"] for s in _steps()]
        assert titles == [PLAN, CHANGELOG, NOTES]
        assert _step(CHANGELOG)["id"] == "changelog"

    def test_inputs_reach_the_script_through_the_environment_only(self) -> None:
        for each in _steps():
            assert "${{" not in each["run"], each["name"]
        env = _step(CHANGELOG)["env"]
        assert env["CHANGELOG_FILE"] == "${{ inputs.changelog-file }}"
        assert env["VERSION"] == "${{ steps.plan.outputs.version }}"
        assert env["GH_TOKEN"] == "${{ inputs.github-token }}"

    def test_the_action_reports_whether_the_file_changed(self) -> None:
        assert _action()["outputs"]["changelog-changed"]["value"] == (
            "${{ steps.changelog.outputs.changed }}"
        )

    def test_it_writes_the_file_the_notes_and_the_output(self, tmp_path: Path) -> None:
        repository, merge = _repository(tmp_path)
        output = tmp_path / "github_output"
        env = {
            "PATH": _stub_gh(
                tmp_path,
                [
                    {
                        "number": 4,
                        "title": "feat(api): add one",
                        "head_ref": "feat/one",
                        "merged": True,
                    }
                ],
            ),
            "MERGE_SHA": merge,
            "REPOSITORY_SLUG": "acme/repo",
            "VERSION": "1.11.0",
            "NOTES_OUTPUT": "CHANGELOG_RELEASE.md",
            "TAG_PREFIX": "api/v",
            "CHANGELOG_FILE": "apps/api/CHANGELOG.md",
            "GH_TOKEN": "token",
            "ACTION_PATH": str(ACTION_DIR),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_SERVER_URL": "https://github.com",
        }

        result = _run_step(CHANGELOG, env, repository)

        assert result.returncode == 0, result.stderr
        assert output.read_text("utf-8") == "changed=true\n"
        section = (
            "## [1.11.0] - 2026-10-04\n\n### Added\n\n"
            "- **api:** add one ([#4](https://github.com/acme/repo/pull/4))\n"
        )
        written = (repository / "apps" / "api" / "CHANGELOG.md").read_text("utf-8")
        assert section in written
        assert (repository / "CHANGELOG_RELEASE.md").read_text("utf-8") == (
            section + "\n[1.11.0]: https://github.com/acme/repo/compare/api/v1.10.0...api/v1.11.0\n"
        )
        # The checkout stays where it was: the file is committed onto the base branch.
        assert _git(repository, "rev-parse", "HEAD") == merge
