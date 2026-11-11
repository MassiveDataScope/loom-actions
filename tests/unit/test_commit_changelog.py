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

from changelog_file import INTRO, ChangelogError, update_changelog  # noqa: E402
from commit_changelog import (  # noqa: E402
    CommitConflict,
    ContentsApi,
    RemoteFile,
    commit_release,
    main,
    render_release,
    verify_release,
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


# Modes render and verify (contract L-a). A release that never writes its base
# branch commits the changelog on the pull request's head by hand; render builds
# the file it expects, without writing, and verify checks a ref already holds
# the planned section. The heading date is the one part they treat apart: render
# reuses the date of a section the ref already lists, else today's in UTC, and
# verify requires an ISO 8601 date without comparing its value, so a merge on
# another UTC day than the render never breaks the check.


def _dated(text: str, date: str) -> str:
    return text.replace("## [0.2.0] - 2026-10-05", f"## [0.2.0] - {date}")


class TestRenderRelease:
    def test_renders_what_commit_would_write(self) -> None:
        branch = FakeBranch([RemoteFile(BASE, "a" * 40)])
        commit_release(BUILT, "0.2.0", branch, MESSAGE, sleep=lambda _: pytest.fail("no retry"))
        ((committed, _, _),) = branch.written

        assert render_release(BUILT, BASE, "0.2.0", today="2026-10-05") == committed

    def test_a_ref_without_the_section_is_dated_today(self) -> None:
        rendered = render_release(BUILT, BASE, "0.2.0", today="2026-11-11")

        assert rendered == _dated(BUILT, "2026-11-11")

    def test_a_ref_listing_the_section_keeps_its_date(self) -> None:
        onto = _dated(BUILT, "2026-10-01")

        assert render_release(BUILT, onto, "0.2.0", today="2026-11-11") == onto

    def test_a_stale_section_is_replaced_and_keeps_its_date(self) -> None:
        onto = _dated(BUILT, "2026-10-01").replace("- add it\n", "- added by hand\n")

        rendered = render_release(BUILT, onto, "0.2.0", today="2026-11-11")

        assert rendered == _dated(BUILT, "2026-10-01")

    def test_a_section_without_an_iso_date_is_dated_today(self) -> None:
        onto = _dated(BUILT, "someday")

        assert render_release(BUILT, onto, "0.2.0", today="2026-11-11") == _dated(
            BUILT, "2026-11-11"
        )

    def test_a_built_file_without_the_release_is_refused(self) -> None:
        with pytest.raises(ChangelogError, match="does not list 0.2.0"):
            render_release(BASE, BASE, "0.2.0", today="2026-11-11")


class TestVerifyRelease:
    def test_the_identical_file_passes(self) -> None:
        verify_release(BUILT, BUILT, "0.2.0")

    def test_a_ref_without_the_section_fails(self) -> None:
        with pytest.raises(ChangelogError, match=r"does not list 0\.2\.0"):
            verify_release(BUILT, BASE, "0.2.0")

    def test_an_altered_section_fails(self) -> None:
        altered = BUILT.replace("- add it\n", "- add it, and more\n")

        with pytest.raises(ChangelogError, match="differs from the planned one"):
            verify_release(BUILT, altered, "0.2.0")

    def test_an_altered_link_fails(self) -> None:
        altered = BUILT.replace("compare/v0.1.0...v0.2.0", "compare/v0.0.9...v0.2.0")

        with pytest.raises(ChangelogError, match="differs from the planned one"):
            verify_release(BUILT, altered, "0.2.0")

    def test_a_section_of_another_version_fails(self) -> None:
        other = BUILT.replace("[0.2.0]", "[0.3.0]")

        with pytest.raises(ChangelogError, match=r"does not list 0\.2\.0"):
            verify_release(BUILT, other, "0.2.0")

    def test_only_the_date_differing_passes(self) -> None:
        verify_release(BUILT, _dated(BUILT, "2027-01-01"), "0.2.0")

    @pytest.mark.parametrize("date", ["2026-10-5", "2026-13-01", "2026/10/05", "someday", ""])
    def test_a_date_that_is_not_iso_fails(self, date: str) -> None:
        onto = BUILT.replace("## [0.2.0] - 2026-10-05", f"## [0.2.0] - {date}".rstrip())

        with pytest.raises(ChangelogError, match="ISO 8601"):
            verify_release(BUILT, onto, "0.2.0")

    def test_a_second_section_of_the_version_below_the_real_one_fails(self) -> None:
        body, _, references = BUILT.partition("\n\n[unreleased]:")
        twice = f"{body}\n\n{SECTION}\n[unreleased]:{references}"

        with pytest.raises(ChangelogError, match=r"lists 0\.2\.0 2 times"):
            verify_release(BUILT, twice, "0.2.0")

    def test_a_link_of_the_version_defined_in_the_body_fails(self) -> None:
        hijacked = BUILT.replace(
            "## [Unreleased]\n", "## [Unreleased]\n\n[0.2.0]: https://attacker.example\n"
        )

        with pytest.raises(ChangelogError, match="link of 0.2.0 outside the link references"):
            verify_release(BUILT, hijacked, "0.2.0")

    def test_a_link_of_the_version_defined_in_the_body_fails_in_any_case(self) -> None:
        hijacked = BUILT.replace(
            "### Added\n", "### Added\n\n   [0.2.0]: https://attacker.example\n"
        )

        with pytest.raises(ChangelogError, match="link of 0.2.0 outside the link references"):
            verify_release(BUILT, hijacked, "0.2.0")

    def test_the_section_below_older_versions_fails(self) -> None:
        moved = BUILT.replace(SECTION + "\n", "").replace(
            "- repair it\n", f"- repair it\n\n{SECTION}"
        )
        assert moved.index("## [0.2.0]") > moved.index("## [0.1.0]")

        with pytest.raises(ChangelogError, match="differs from the expected file"):
            verify_release(BUILT, moved, "0.2.0")

    def test_an_altered_unreleased_link_fails(self) -> None:
        altered = BUILT.replace("compare/v0.2.0...HEAD", "compare/v0.1.0...HEAD")

        with pytest.raises(ChangelogError, match="differs from the expected file"):
            verify_release(BUILT, altered, "0.2.0")

    def test_crlf_line_endings_fail(self) -> None:
        with pytest.raises(ChangelogError, match="differs from the expected file"):
            verify_release(BUILT, BUILT.replace("\n", "\r\n"), "0.2.0")

    def test_an_extra_blank_line_fails(self) -> None:
        with pytest.raises(ChangelogError, match="differs from the expected file"):
            verify_release(
                BUILT, BUILT.replace("## [Unreleased]\n", "## [Unreleased]\n\n"), "0.2.0"
            )


def _arguments(built: Path, *extra: str) -> list[str]:
    return [
        *("--slug", "acme/repo", "--branch", "master"),
        *("--changelog-file", "apps/api/CHANGELOG.md", "--built", str(built)),
        *("--version", "0.2.0", "--tag-prefix=api/v"),
        *extra,
    ]


@dataclass
class ReadOnlyRefs:
    """Branches read through the contents API, failing any write."""

    files: dict[str, str]
    opened: list[tuple[str, str, str]] = field(default_factory=list)

    def __call__(self, slug: str, path: str, ref: str) -> FakeBranch:
        self.opened.append((slug, path, ref))
        return FakeBranch([RemoteFile(self.files[ref], "a" * 40)], conflicts=99)


class TestMainModes:
    @pytest.fixture
    def built(self, tmp_path: Path) -> Path:
        path = tmp_path / "built" / "CHANGELOG.md"
        path.parent.mkdir()
        path.write_text(BUILT, "utf-8")
        return path

    def test_render_writes_the_expected_file_from_the_ref_and_commits_nothing(
        self,
        tmp_path: Path,
        built: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        refs = ReadOnlyRefs({"release/1.2": BASE})
        monkeypatch.setattr("commit_changelog.ContentsApi", refs)
        monkeypatch.setattr("commit_changelog._today", lambda: "2026-11-11")
        out = tmp_path / "expected" / "CHANGELOG.md"

        code = main(
            _arguments(built, "--mode", "render", "--ref", "release/1.2", "--out", str(out))
        )

        assert code == 0
        assert refs.opened == [("acme/repo", "apps/api/CHANGELOG.md", "release/1.2")]
        assert out.read_text("utf-8") == _dated(BUILT, "2026-11-11")
        assert capsys.readouterr().out == "changed=true\n"

    def test_the_ref_defaults_to_the_base_branch(
        self,
        tmp_path: Path,
        built: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        refs = ReadOnlyRefs({"master": BUILT})
        monkeypatch.setattr("commit_changelog.ContentsApi", refs)
        out = tmp_path / "CHANGELOG.md"

        assert main(_arguments(built, "--mode", "render", "--ref", "", "--out", str(out))) == 0

        assert refs.opened == [("acme/repo", "apps/api/CHANGELOG.md", "master")]
        assert out.read_text("utf-8") == BUILT
        assert capsys.readouterr().out == "changed=false\n"

    def test_render_without_an_output_file_fails(
        self, built: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exited:
            main(_arguments(built, "--mode", "render"))

        assert exited.value.code == 1
        assert "changelog render failed: render needs --out" in capsys.readouterr().err

    def test_verify_passes_on_a_ref_holding_the_section(
        self,
        built: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        refs = ReadOnlyRefs({"c0ffee": _dated(BUILT, "2026-10-01")})
        monkeypatch.setattr("commit_changelog.ContentsApi", refs)

        assert main(_arguments(built, "--mode", "verify", "--ref", "c0ffee")) == 0

        assert capsys.readouterr().out == "changed=false\n"

    def test_verify_fails_and_leaves_the_expected_file(
        self,
        tmp_path: Path,
        built: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        refs = ReadOnlyRefs({"c0ffee": BASE})
        monkeypatch.setattr("commit_changelog.ContentsApi", refs)
        monkeypatch.setattr("commit_changelog._today", lambda: "2026-11-11")
        out = tmp_path / "CHANGELOG.md"

        with pytest.raises(SystemExit) as exited:
            main(_arguments(built, "--mode", "verify", "--ref", "c0ffee", "--out", str(out)))

        assert exited.value.code == 1
        captured = capsys.readouterr()
        assert captured.out == "changed=true\n"
        assert captured.err.startswith("changelog verify failed: ")
        assert "'apps/api/CHANGELOG.md' on 'c0ffee'" in captured.err
        assert "does not list 0.2.0" in captured.err
        assert out.read_text("utf-8") == _dated(BUILT, "2026-11-11")

    def test_a_ref_with_control_characters_reaches_stderr_escaped(
        self,
        built: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        refs = ReadOnlyRefs({"x\x1b[2Jy": BASE})
        monkeypatch.setattr("commit_changelog.ContentsApi", refs)

        with pytest.raises(SystemExit):
            main(_arguments(built, "--mode", "verify", "--ref", "x\x1b[2Jy"))

        err = capsys.readouterr().err
        assert "\x1b" not in err
        assert "'x\\x1b[2Jy'" in err

    def test_an_unknown_mode_is_refused(self, built: Path) -> None:
        with pytest.raises(SystemExit) as exited:
            main(_arguments(built, "--mode", "push"))

        assert exited.value.code == 2


class TestTheCompositeModes:
    def _action(self) -> dict[str, Any]:
        return cast(dict[str, Any], yaml.safe_load((COMPOSITE / "action.yml").read_text("utf-8")))

    def test_new_inputs_are_optional_and_default_to_commit(self) -> None:
        inputs = self._action()["inputs"]

        assert inputs["mode"]["default"] == "commit"
        assert inputs["ref"]["default"] == ""
        assert inputs["out"]["default"] == ""
        assert not any(inputs[name].get("required") for name in ("mode", "ref", "out"))

    def test_changed_is_the_steps_output(self) -> None:
        action = self._action()
        (step,) = action["runs"]["steps"]

        assert action["outputs"]["changed"]["value"] == (
            f"${{{{ steps.{step['id']}.outputs.changed }}}}"
        )
        assert step["env"]["MODE"] == "${{ inputs.mode }}"
        assert step["env"]["REF"] == "${{ inputs.ref }}"
        assert step["env"]["OUT"] == "${{ inputs.out }}"

    def _run(
        self, tmp_path: Path, served: str, **env: str
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
        answer = _content(served)
        stub = _stub_gh(
            tmp_path,
            'case "$*" in\n'
            f"  *\"--method GET\"*) cat <<'JSON'\n{answer}\nJSON\n  ;;\n"
            '  *) echo "unexpected write" >&2; exit 1 ;;\n'
            "esac",
        )
        built = tmp_path / "changelog" / "CHANGELOG.md"
        built.parent.mkdir()
        built.write_text(BUILT, "utf-8")
        script = tmp_path / "step.sh"
        script.write_text(self._action()["runs"]["steps"][0]["run"], "utf-8")
        output = tmp_path / "github_output"
        output.write_text("", "utf-8")
        result = subprocess.run(
            ("bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)),
            cwd=tmp_path,
            env={
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
                "GITHUB_OUTPUT": str(output),
                **env,
            },
            capture_output=True,
            text=True,
            check=False,
        )
        return result, output, Path(stub["LOG"])

    def test_render_reads_the_ref_and_writes_out_without_a_put(self, tmp_path: Path) -> None:
        onto = _dated(BUILT, "2026-10-01").replace("- add it\n", "- stale\n")
        out = tmp_path / "expected.md"

        result, output, log = self._run(
            tmp_path, onto, MODE="render", REF="release/1.2", OUT=str(out)
        )

        assert result.returncode == 0, result.stderr
        assert out.read_text("utf-8") == _dated(BUILT, "2026-10-01")
        assert output.read_text("utf-8") == "changed=true\n"
        assert log.read_text("utf-8") == (
            "api --method GET repos/acme/repo/contents/apps/api/CHANGELOG.md -f ref=release/1.2\n"
        )

    def test_verify_fails_on_an_altered_section_without_a_put(self, tmp_path: Path) -> None:
        altered = BUILT.replace("- add it\n", "- add it, and more\n")

        result, output, log = self._run(tmp_path, altered, MODE="verify", REF="c0ffee", OUT="")

        assert result.returncode == 1
        assert "changelog verify failed" in result.stderr
        assert output.read_text("utf-8") == "changed=true\n"
        assert "PUT" not in log.read_text("utf-8")

    def test_verify_passes_when_only_the_date_differs(self, tmp_path: Path) -> None:
        result, output, _ = self._run(
            tmp_path, _dated(BUILT, "2027-01-01"), MODE="verify", REF="c0ffee", OUT=""
        )

        assert result.returncode == 0, result.stderr
        assert output.read_text("utf-8") == "changed=false\n"
