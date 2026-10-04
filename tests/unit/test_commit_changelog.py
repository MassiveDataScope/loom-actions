"""Unit tests for the commit of a release's changelog to its base branch.

The plan job builds the file on the head of the base branch; the commit lands
after the GitHub Release, when the branch may have moved. Each attempt reads
the file the branch holds and carries the release onto it, so a change made
meanwhile is kept and a version already listed is never added twice. GitHub
answers 409 when the file changed between that read and the write; the commit
is then retried, up to three times, after a growing pause.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[2] / "actions" / "release" / "plan-release" / "src"))

from changelog_file import INTRO, update_changelog  # noqa: E402
from commit_changelog import (  # noqa: E402
    CommitConflict,
    ContentsApi,
    RemoteFile,
    commit_release,
    main,
)
from release_history import HistoryError  # noqa: E402

URL = "https://github.com/acme/repo"
BASE = (
    f"{INTRO}\n## [Unreleased]\n\n## [0.1.0] - 2026-10-04\n\n### Fixed\n\n- repair it\n\n"
    f"[unreleased]: {URL}/compare/v0.1.0...HEAD\n[0.1.0]: {URL}/releases/tag/v0.1.0\n"
)
SECTION = "## [0.2.0] - 2026-10-05\n\n### Added\n\n- add it\n"
LINKS = (f"[unreleased]: {URL}/compare/v0.2.0...HEAD", f"[0.2.0]: {URL}/compare/v0.1.0...v0.2.0")
BUILT = update_changelog(BASE, "0.2.0", SECTION, LINKS).text
MESSAGE = "docs(release): changelog for v0.2.0"


@dataclass
class FakeBranch:
    """A base branch whose file moves on, and which refuses a write over a stale blob."""

    files: list[RemoteFile]
    conflicts: int = 0
    written: list[tuple[str, str | None, str]] = field(default_factory=list)

    def read(self) -> RemoteFile:
        return self.files[0]

    def write(self, text: str, sha: str | None, message: str) -> str:
        if self.conflicts:
            self.conflicts -= 1
            if len(self.files) > 1:
                self.files.pop(0)
            raise CommitConflict("is at b but expected a (HTTP 409)")
        self.written.append((text, sha, message))
        return "c0ffee"


class TestCommitRelease:
    def test_commits_the_release_over_the_blob_it_read(self) -> None:
        branch = FakeBranch([RemoteFile(BASE, "a" * 40)])

        assert commit_release(BUILT, "0.2.0", branch, MESSAGE, sleep=pytest.fail) is True
        assert branch.written == [(BUILT, "a" * 40, MESSAGE)]

    def test_a_file_the_branch_does_not_hold_is_created(self) -> None:
        branch = FakeBranch([RemoteFile("", None)])

        assert commit_release(BUILT, "0.2.0", branch, MESSAGE, sleep=pytest.fail) is True
        ((text, sha, _),) = branch.written
        assert sha is None
        assert text.startswith(f"{INTRO}\n## [Unreleased]\n\n## [0.2.0] - 2026-10-05\n")

    def test_a_branch_that_already_lists_the_release_gets_no_commit(self) -> None:
        branch = FakeBranch([RemoteFile(BUILT, "b" * 40)])

        assert commit_release(BUILT, "0.2.0", branch, MESSAGE, sleep=pytest.fail) is False
        assert branch.written == []

    def test_a_conflict_rereads_the_branch_and_keeps_its_change(self) -> None:
        moved = BASE.replace("## [Unreleased]\n", "## [Unreleased]\n\n- planned by hand\n")
        branch = FakeBranch([RemoteFile(BASE, "a" * 40), RemoteFile(moved, "b" * 40)], 1)
        pauses: list[float] = []

        assert commit_release(BUILT, "0.2.0", branch, MESSAGE, sleep=pauses.append) is True

        ((text, sha, _),) = branch.written
        assert sha == "b" * 40
        assert "- planned by hand\n\n## [0.2.0] - 2026-10-05\n" in text
        assert pauses == [1]

    def test_a_conflict_with_a_rerun_that_committed_first_ends_without_a_commit(self) -> None:
        branch = FakeBranch([RemoteFile(BASE, "a" * 40), RemoteFile(BUILT, "b" * 40)], 1)

        assert commit_release(BUILT, "0.2.0", branch, MESSAGE, sleep=lambda _s: None) is False
        assert branch.written == []

    def test_gives_up_after_three_retries(self) -> None:
        branch = FakeBranch([RemoteFile(BASE, "a" * 40)], conflicts=4)
        pauses: list[float] = []

        with pytest.raises(CommitConflict, match="HTTP 409"):
            commit_release(BUILT, "0.2.0", branch, MESSAGE, sleep=pauses.append)

        assert pauses == [1, 2, 4]
        assert branch.written == []


def _stub_gh(tmp_path: Path, script: str) -> dict[str, str]:
    """Put a ``gh`` on PATH running *script*, with every call logged to ``$LOG``."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh.log"
    gh = bin_dir / "gh"
    gh.write_text(
        f'#!/usr/bin/env bash\nLOG="{log}"\nprintf "%s\\n" "$*" >> "$LOG"\n{script}\n', "utf-8"
    )
    gh.chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "LOG": str(log)}


def _content(text: str) -> str:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    # The contents API wraps the base64 at 60 characters.
    wrapped = "\n".join(encoded[i : i + 60] for i in range(0, len(encoded), 60))
    return json.dumps({"type": "file", "encoding": "base64", "sha": "a" * 40, "content": wrapped})


API = ContentsApi("acme/repo", "apps/api/CHANGELOG.md", "master")


class TestContentsApi:
    def test_reads_the_file_and_its_blob_on_the_branch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        answer = _content("# Changelog ünïcode\n")
        stub = _stub_gh(tmp_path, f"cat <<'JSON'\n{answer}\nJSON")
        monkeypatch.setenv("PATH", stub["PATH"])

        assert API.read() == RemoteFile("# Changelog ünïcode\n", "a" * 40)
        assert Path(stub["LOG"]).read_text("utf-8") == (
            "api --method GET repos/acme/repo/contents/apps/api/CHANGELOG.md -f ref=master\n"
        )

    def test_a_file_the_branch_does_not_hold_reads_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = _stub_gh(tmp_path, 'echo "gh: Not Found (HTTP 404)" >&2; exit 1')
        monkeypatch.setenv("PATH", stub["PATH"])

        assert API.read() == RemoteFile("", None)

    def test_writes_the_file_over_the_blob_on_the_branch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = tmp_path / "body.json"
        stub = _stub_gh(tmp_path, f'cat > "{body}"; echo c0ffee')
        monkeypatch.setenv("PATH", stub["PATH"])

        assert API.write("# Changelog ünïcode\n", "a" * 40, MESSAGE) == "c0ffee"

        assert Path(stub["LOG"]).read_text("utf-8") == (
            "api --method PUT repos/acme/repo/contents/apps/api/CHANGELOG.md "
            "--input - --jq .commit.sha\n"
        )
        sent = json.loads(body.read_text("utf-8"))
        assert base64.b64decode(sent.pop("content")).decode("utf-8") == "# Changelog ünïcode\n"
        assert sent == {"message": MESSAGE, "branch": "master", "sha": "a" * 40}

    def test_a_new_file_is_written_without_a_blob(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = tmp_path / "body.json"
        stub = _stub_gh(tmp_path, f'cat > "{body}"; echo c0ffee')
        monkeypatch.setenv("PATH", stub["PATH"])

        API.write("# Changelog\n", None, MESSAGE)

        assert "sha" not in json.loads(body.read_text("utf-8"))

    def test_a_409_is_a_conflict_and_anything_else_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        status = tmp_path / "status"
        stub = _stub_gh(
            tmp_path, f'cat > /dev/null; echo "gh: refused (HTTP $(cat "{status}"))" >&2; exit 1'
        )
        monkeypatch.setenv("PATH", stub["PATH"])

        status.write_text("409", "utf-8")
        with pytest.raises(CommitConflict):
            API.write("x", "a" * 40, MESSAGE)
        status.write_text("403", "utf-8")
        with pytest.raises(HistoryError, match="HTTP 403") as failed:
            API.write("x", "a" * 40, MESSAGE)
        assert not isinstance(failed.value, CommitConflict)


class TestMain:
    def test_commits_the_built_file_and_says_so(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        built = tmp_path / "CHANGELOG.md"
        built.write_text(BUILT, "utf-8")
        branch = FakeBranch([RemoteFile(BASE, "a" * 40)])
        monkeypatch.setattr("commit_changelog.ContentsApi", lambda *_args: branch)

        code = main(
            [
                *("--slug", "acme/repo", "--branch", "master"),
                *("--changelog-file", "apps/api/CHANGELOG.md", "--built", str(built)),
                *("--version", "0.2.0", "--tag-prefix=api/v"),
            ]
        )

        assert code == 0
        assert branch.written == [(BUILT, "a" * 40, "docs(release): changelog for api/v0.2.0")]
        assert capsys.readouterr().out == "committed apps/api/CHANGELOG.md to master\n"

    def test_an_unsafe_file_fails_before_anything_is_read(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exited:
            main(
                [
                    *("--slug", "acme/repo", "--branch", "master"),
                    *("--changelog-file", "../CHANGELOG.md", "--built", str(tmp_path / "x")),
                    *("--version", "0.2.0", "--tag-prefix=v"),
                ]
            )

        assert exited.value.code == 1
        assert capsys.readouterr().err.startswith("changelog commit failed: changelog file")


COMPOSITE = Path(__file__).parents[2] / "actions" / "release" / "plan-release" / "commit-changelog"


class TestTheComposite:
    def _step(self) -> dict[str, Any]:
        action = yaml.safe_load((COMPOSITE / "action.yml").read_text("utf-8"))
        (step,) = action["runs"]["steps"]
        return cast(dict[str, Any], step)

    def test_inputs_reach_the_script_through_the_environment_only(self) -> None:
        step = self._step()

        assert "${{" not in step["run"]
        assert step["env"]["GH_TOKEN"] == "${{ inputs.github-token }}"
        assert step["env"]["ACTION_PATH"] == "${{ github.action_path }}"

    def test_it_creates_the_file_a_branch_does_not_hold(self, tmp_path: Path) -> None:
        body = tmp_path / "body.json"
        stub = _stub_gh(
            tmp_path,
            'case "$*" in\n'
            '  *"--method GET"*) echo "gh: Not Found (HTTP 404)" >&2; exit 1 ;;\n'
            f'  *) cat > "{body}"; echo c0ffee ;;\n'
            "esac",
        )
        built = tmp_path / "changelog" / "CHANGELOG.md"
        built.parent.mkdir()
        built.write_text(BUILT, "utf-8")
        script = tmp_path / "step.sh"
        script.write_text(self._step()["run"], "utf-8")
        env = {
            "PATH": stub["PATH"],
            "HOME": str(tmp_path),
            "GH_TOKEN": "token",
            "CHANGELOG_FILE": "apps/api/CHANGELOG.md",
            "BUILT_FILE": str(built),
            "VERSION": "0.2.0",
            "TAG_PREFIX": "api/v",
            "BASE_BRANCH": "master",
            "REPOSITORY_SLUG": "acme/repo",
            "ACTION_PATH": str(COMPOSITE),
        }

        result = subprocess.run(
            ("bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)),
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout == "committed apps/api/CHANGELOG.md to master\n"
        sent = json.loads(body.read_text("utf-8"))
        assert sent["message"] == "docs(release): changelog for api/v0.2.0"
        assert "sha" not in sent
        text = base64.b64decode(sent["content"]).decode("utf-8")
        assert "## [0.2.0] - 2026-10-05" in text
