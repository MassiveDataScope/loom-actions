"""Unit tests for the history every release script reads: tags, range and pull requests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "actions" / "release" / "plan-release" / "src"))

from release_history import (  # noqa: E402
    HistoryError,
    PullRequest,
    gh_commit_pull_requests,
    latest_release_tag,
    merged_pull_requests,
    range_log,
)
from release_scope import ReleaseScope  # noqa: E402


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments), check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "-q", "--initial-branch=master")
    _git(repository, "config", "user.email", "release@example.com")
    _git(repository, "config", "user.name", "Release")
    _touch(repository, "README.md", "chore: initial")
    return repository


def _touch(repository: Path, path: str, message: str) -> str:
    target = repository / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{target.read_text('utf-8') if target.exists() else ''}{message}\n", "utf-8")
    _git(repository, "add", "--", path)
    _git(repository, "commit", "-qm", message)
    return _git(repository, "rev-parse", "HEAD")


class TestLatestReleaseTag:
    def test_reads_the_highest_tag_by_version_not_by_string(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _git(repository, "tag", "v1.9.9")
        _touch(repository, "a", "fix: one")
        _git(repository, "tag", "v1.9.10")
        _touch(repository, "a", "fix: two")

        assert latest_release_tag(repository, "HEAD") == "v1.9.10"

    def test_a_tag_on_the_revision_itself_is_not_a_previous_release(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _git(repository, "tag", "v1.10.0")
        head = _touch(repository, "a", "feat: one")
        _git(repository, "tag", "v1.11.0")

        assert latest_release_tag(repository, "HEAD") == "v1.10.0"
        assert latest_release_tag(repository, head) == "v1.10.0"

    def test_reads_only_the_tags_of_its_prefix(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _git(repository, "tag", "api-v1.0.0")
        _touch(repository, "a", "fix: one")
        _git(repository, "tag", "v7.0.0")
        _git(repository, "tag", "api-v2-v5.0.0")
        _touch(repository, "a", "fix: two")

        assert latest_release_tag(repository, "HEAD", "api-v") == "api-v1.0.0"
        assert latest_release_tag(repository, "HEAD") == "v7.0.0"

    def test_an_unknown_revision_is_a_history_error(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)

        with pytest.raises(HistoryError, match="failed"):
            latest_release_tag(repository, "0" * 40)


class TestRangeLog:
    def test_lists_the_commits_since_the_tag_newest_first(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _git(repository, "tag", "v1.0.0")
        first = _touch(repository, "a", "feat: one")
        second = _touch(repository, "a", "fix: two")

        assert range_log(repository, "v1.0.0", "HEAD") == (second, first)
        assert range_log(repository, "v1.0.0", "HEAD", pretty="%s") == ("fix: two", "feat: one")

    def test_a_scope_keeps_only_the_commits_touching_its_paths(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        mine = _touch(repository, "apps/a/src.py", "fix(a): one")
        _touch(repository, "apps/b/src.py", "fix(b): two")

        assert range_log(repository, None, "HEAD", ReleaseScope("a/v", ("apps/a",))) == (mine,)

    def test_a_path_is_literal_never_a_glob(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _touch(repository, "apps/a/src.py", "fix(a): one")
        star = _touch(repository, "apps/*/src.py", "fix(star): two")

        scope = ReleaseScope("a/v", ("apps/*",))

        assert range_log(repository, None, "HEAD", scope) == (star,)


def _stub_gh(tmp_path: Path, answer: object) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "gh.log"
    gh = bin_dir / "gh"
    gh.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "{log}"\n'
        f"cat <<'JSON'\n{json.dumps(answer)}\nJSON\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    return {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "LOG": str(log)}


class TestPullRequests:
    def test_the_reader_returns_every_pull_request_with_its_branch_and_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        answer = [
            {"number": 1, "title": 'feat: a "quoted"\ttitle', "head_ref": "feat/a", "merged": True},
            {"number": 2, "title": "fix: closed", "head_ref": "fix/b", "merged": False},
        ]
        stub = _stub_gh(tmp_path, answer)
        monkeypatch.setenv("PATH", stub["PATH"])

        read = gh_commit_pull_requests("acme/repo")

        assert read("abc") == (
            PullRequest(1, 'feat: a "quoted"\ttitle', "feat/a", True),
            PullRequest(2, "fix: closed", "fix/b", False),
        )
        arguments = Path(stub["LOG"]).read_text("utf-8").splitlines()
        assert arguments[:3] == ["api", "repos/acme/repo/commits/abc/pulls", "--jq"]
        assert "head_ref: .head.ref" in arguments[3]
        assert "merged: (.merged_at != null)" in arguments[3]

    def test_only_merged_pull_requests_shipped_the_commit(self) -> None:
        merged = PullRequest(1, "feat: a", "feat/a", True)
        closed = PullRequest(2, "feat: b", "feat/b", False)

        assert merged_pull_requests(lambda _sha: (closed, merged), "abc") == (merged,)
        assert merged_pull_requests(lambda _sha: (closed,), "abc") == ()
