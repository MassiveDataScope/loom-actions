"""Unit tests for the notes a release ships.

This module had none, which is why the tag-on-HEAD defect fixed in its sibling
survived here: a release re-run for an already tagged commit read its own tag as
the previous release, saw an empty range, and refused.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "actions" / "release" / "plan-release" / "src"))

from build_release_notes import (  # noqa: E402
    ReleaseNotesError,
    build_release_notes,
    main,
    release_entries,
    render_release_notes,
)
from release_history import latest_release_tag  # noqa: E402
from release_scope import ReleaseScope  # noqa: E402


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments), check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=master")
    _git(repository, "config", "user.email", "release@example.com")
    _git(repository, "config", "user.name", "Release")
    _commit(repository, "chore: initial")
    return repository


def _commit(repository: Path, message: str) -> str:
    (repository / message.replace(":", "_").replace(" ", "_")).write_text("x", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


class TestReleaseEntries:
    def test_lists_every_commit_since_the_last_tag(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _git(repository, "tag", "v1.10.0")
        _commit(repository, "feat: one")
        _commit(repository, "fix: two")

        entries = release_entries(repository, "v1.10.0")

        assert entries == ("fix: two", "feat: one")

    def test_lists_the_whole_history_without_a_tag(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _commit(repository, "feat: one")

        assert release_entries(repository, None) == ("feat: one", "chore: initial")

    def test_lists_only_the_commits_touching_the_scope(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _git(repository, "tag", "app-a/v1.0.0")
        (repository / "apps" / "app-a").mkdir(parents=True)
        (repository / "apps" / "app-a" / "src.py").write_text("x", "utf-8")
        _git(repository, "add", ".")
        _git(repository, "commit", "-m", "fix(app-a): one")
        _commit(repository, "feat: elsewhere")

        entries = release_entries(
            repository, "app-a/v1.0.0", ReleaseScope("app-a/v", ("apps/app-a",))
        )

        assert entries == ("fix(app-a): one",)


class TestRenderReleaseNotes:
    def test_names_the_range_the_release_ships(self) -> None:
        rendered = render_release_notes("1.11.0", "v1.10.0", ("feat: one",))

        assert "# 🚀 Release 1.11.0" in rendered
        assert "Changes since v1.10.0:" in rendered
        assert "- feat: one" in rendered

    def test_refuses_a_release_that_ships_nothing(self) -> None:
        with pytest.raises(ReleaseNotesError, match="nothing to release"):
            render_release_notes("1.11.0", "v1.10.0", ())

    def test_a_scoped_refusal_names_the_paths_it_read(self) -> None:
        scope = ReleaseScope("app-a/v", ("apps/app-a", "uv.lock"))

        with pytest.raises(ReleaseNotesError) as refused:
            render_release_notes("0.1.1", "app-a/v0.1.0", (), scope)

        assert str(refused.value) == scope.no_commits("app-a/v0.1.0")
        assert "touching apps/app-a, uv.lock" in str(refused.value)


class TestBuildReleaseNotes:
    def test_a_rerun_for_a_tagged_commit_still_writes_its_notes(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _git(repository, "tag", "v1.10.0")
        _commit(repository, "feat: one")
        _git(repository, "tag", "v1.11.0")

        notes = build_release_notes(repository, "1.11.0")

        assert "Changes since v1.10.0:" in notes
        assert "- feat: one" in notes


class TestTagPrefix:
    """The notes of a monorepo package list what shipped since its own last tag."""

    def test_reads_only_the_tags_with_the_prefix(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _git(repository, "tag", "api-v1.10.0")
        _commit(repository, "fix: worker")
        _git(repository, "tag", "worker-v3.0.0")
        _git(repository, "tag", "v7.0.0")
        _commit(repository, "feat: api")

        assert latest_release_tag(repository, "HEAD", "api-v") == "api-v1.10.0"
        assert latest_release_tag(repository, "HEAD") == "v7.0.0"

    def test_a_rerun_for_a_prefixed_tag_still_writes_its_notes(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _git(repository, "tag", "api-v1.10.0")
        _commit(repository, "fix: worker")
        _git(repository, "tag", "v7.0.0")
        _commit(repository, "feat: api")
        _git(repository, "tag", "api-v1.11.0")

        notes = build_release_notes(repository, "1.11.0", ReleaseScope("api-v"))

        assert "Changes since api-v1.10.0:" in notes
        assert "- feat: api\n- fix: worker\n" in notes

    def test_refuses_an_unsafe_prefix(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repository = _repository(tmp_path)
        output = tmp_path / "notes.md"
        arguments = ["--repository", str(repository), "--version", "1.0.0"]

        with pytest.raises(SystemExit) as exited:
            main([*arguments, "--output", str(output), "--tag-prefix=v`id`"])

        assert exited.value.code == 1
        assert "release notes failed: tag prefix 'v`id`' is not allowed" in capsys.readouterr().err
        assert not output.exists()

    def test_default_prefix_notes_are_identical(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _git(repository, "tag", "v1.10.0")
        _commit(repository, "feat: one")
        _git(repository, "tag", "api-v9.0.0")
        _commit(repository, "fix: two")
        written = []
        for extra in ([], ["--tag-prefix", "v"]):
            output = tmp_path / f"notes{len(written)}.md"
            arguments = ["--repository", str(repository), "--version", "1.11.0"]
            assert main([*arguments, "--output", str(output), *extra]) == 0
            written.append(output.read_bytes())

        assert written[0] == written[1]
        assert b"Changes since v1.10.0:" in written[0]
