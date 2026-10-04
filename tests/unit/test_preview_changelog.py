"""Unit tests for the release preview a pull request shows before it merges.

The preview runs the release's own planner and changelog on the test merge
GitHub checks out for a pull request, with the pull request read as merged
under the title it has now. So it must show exactly what a labelled merge
would release: the version, from the last ``<prefix>`` tag, the branch class
and the declared breaks, and the Keep a Changelog section, or the very error
the release would stop on.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "actions" / "release" / "plan-release" / "src"))

from changelog_file import ChangelogError, build_changelog  # noqa: E402
from plan_release import plan_release  # noqa: E402
from preview_changelog import (  # noqa: E402
    OpenPullRequest,
    PreviewError,
    PullRequestMerge,
    ReleasePreview,
    main,
    preview_header,
    preview_release,
    with_pull_request_merged,
)
from release_history import CommitPullRequests, HistoryError, PullRequest  # noqa: E402
from release_scope import ReleaseScope  # noqa: E402

URL = "https://github.com/acme/mono"
BASE = "4771e69" + "1" * 33
TODAY = "2026-10-04"
RULES = (
    "[tool.semantic_branch]\n"
    'major = ["breaking/.*"]\n'
    'minor = ["feat/.*"]\n'
    'patch = ["fix/.*"]\n'
    'release_ignore = ["chore/.*"]\n'
)
APP_A = ReleaseScope("app-a/v", ("apps/app-a", "uv.lock"))
APP_B = ReleaseScope("app-b/v", ("apps/app-b", "uv.lock"))


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_COMMITTER_DATE": "2026-10-04T10:00:00Z"},
    )
    return completed.stdout.strip()


def _touch(repository: Path, path: str, message: str) -> str:
    target = repository / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{target.read_text('utf-8') if target.exists() else ''}{message}\n", "utf-8")
    _git(repository, "add", "--", path)
    _git(repository, "commit", "-qm", message)
    return _git(repository, "rev-parse", "HEAD")


def _monorepo(tmp_path: Path) -> Path:
    """Return a monorepo whose apps were released as app-a/v0.1.0 and app-b/v0.1.0."""
    repository = tmp_path / "mono"
    repository.mkdir()
    _git(repository, "init", "-q", "--initial-branch=master")
    _git(repository, "config", "user.email", "release@example.com")
    _git(repository, "config", "user.name", "Release")
    (repository / "pyproject.toml").write_text(RULES, "utf-8")
    _touch(repository, "apps/app-a/src.py", "chore: start app-a")
    _touch(repository, "apps/app-b/src.py", "chore: start app-b")
    _git(repository, "tag", "app-a/v0.1.0")
    _git(repository, "tag", "app-b/v0.1.0")
    return repository


def _branch(repository: Path, name: str, changes: Mapping[str, str]) -> tuple[str, ...]:
    """Commit *changes*, path to message, on a new branch *name* off master."""
    _git(repository, "checkout", "-qb", name, "master")
    shas = tuple(_touch(repository, path, message) for path, message in changes.items())
    _git(repository, "checkout", "-q", "master")
    return shas


def _test_merge(repository: Path, branch: str) -> str:
    """Return the merge of *branch* into master, as refs/pull/N/merge holds it."""
    _git(repository, "checkout", "-q", "--detach", "master")
    _git(repository, "merge", "-q", "--no-ff", "-m", f"Merge {branch} into master", branch)
    merge = _git(repository, "rev-parse", "HEAD")
    _git(repository, "checkout", "-q", "master")
    return merge


def _reader(merged: Mapping[str, tuple[PullRequest, ...]]) -> CommitPullRequests:
    return lambda sha: merged.get(sha, ())


def _preview(
    repository: Path,
    merge: str,
    pull_request: OpenPullRequest,
    merged: Mapping[str, tuple[PullRequest, ...]] | None = None,
    scope: ReleaseScope = APP_A,
    read: CommitPullRequests | None = None,
) -> ReleasePreview:
    return preview_release(
        repository,
        merge,
        pull_request,
        read or _reader(merged or {}),
        config=Path("pyproject.toml"),
        scope=scope,
        changelog_file=f"apps/{scope.tag_prefix.removesuffix('/v')}/CHANGELOG.md",
        repository_url=URL,
        date=TODAY,
    )


class TestPullRequestMerge:
    def test_its_commits_are_those_the_second_parent_brings_in(self, tmp_path: Path) -> None:
        repository = _monorepo(tmp_path)
        shas = _branch(
            repository, "feat/a", {"apps/app-a/one.py": "feat: one", "apps/app-a/two": "fix: two"}
        )
        base = _touch(repository, "apps/app-b/later.py", "fix(app-b): merged meanwhile")
        merge = _test_merge(repository, "feat/a")

        assert PullRequestMerge.read(repository, merge) == PullRequestMerge(
            merge, base, frozenset(shas)
        )

    def test_base_commits_the_branch_merged_in_are_not_its_own(self, tmp_path: Path) -> None:
        repository = _monorepo(tmp_path)
        (first,) = _branch(repository, "feat/a", {"apps/app-a/one.py": "feat: one"})
        _touch(repository, "apps/app-b/later.py", "fix(app-b): merged meanwhile")
        _git(repository, "checkout", "-q", "feat/a")
        _git(repository, "merge", "-q", "--no-ff", "-m", "Merge master into feat/a", "master")
        second = _touch(repository, "apps/app-a/two.py", "feat: two")
        _git(repository, "checkout", "-q", "master")
        merge = _test_merge(repository, "feat/a")

        assert PullRequestMerge.read(repository, merge).commits == {first, second}

    def test_a_commit_that_is_not_a_merge_is_refused(self, tmp_path: Path) -> None:
        repository = _monorepo(tmp_path)

        with pytest.raises(PreviewError, match="not a merge of the pull request"):
            PullRequestMerge.read(repository, "HEAD")


class TestWithPullRequestMerged:
    def test_its_commits_read_as_the_pull_request_merged_and_others_as_before(self) -> None:
        other = (PullRequest(4, "fix: other", "fix/other", True),)
        read = with_pull_request_merged(
            _reader({"b": other, "a": (PullRequest(5, "old", "feat/x", False),)}),
            OpenPullRequest(5, "feat: new", "feat/x"),
            frozenset({"a"}),
        )

        assert read("a") == (PullRequest(5, "feat: new", "feat/x", True),)
        assert read("b") == other


class TestPreviewRelease:
    def test_a_feature_releases_the_next_minor_with_its_entry(self, tmp_path: Path) -> None:
        repository = _monorepo(tmp_path)
        _branch(repository, "feat/greeting", {"apps/app-a/greet.py": "wip"})
        merge = _test_merge(repository, "feat/greeting")
        pull_request = OpenPullRequest(5, "feat(app-a): greet the user", "feat/greeting")

        preview = _preview(repository, merge, pull_request)

        assert (preview.version, preview.part, preview.last_tag) == (
            "0.2.0",
            "minor",
            "app-a/v0.1.0",
        )
        assert preview.notes == (
            f"## [0.2.0] - {TODAY}\n\n### Added\n\n"
            f"- **app-a:** greet the user ([#5]({URL}/pull/5))\n\n"
            f"[0.2.0]: {URL}/compare/app-a/v0.1.0...app-a/v0.2.0\n"
        )
        assert preview.touched
        assert preview.base_sha == _git(repository, "rev-parse", "master")

    def test_merged_but_unreleased_pull_requests_are_listed_with_it(self, tmp_path: Path) -> None:
        repository = _monorepo(tmp_path)
        _branch(repository, "feat/greeting", {"apps/app-a/greet.py": "wip"})
        fixed = _touch(repository, "apps/app-a/fix.py", "fix: stop crashing")
        merge = _test_merge(repository, "feat/greeting")
        merged = {fixed: (PullRequest(4, "fix(app-a): stop crashing", "fix/crash", True),)}

        preview = _preview(
            repository, merge, OpenPullRequest(5, "feat(app-a): greet", "feat/greeting"), merged
        )

        assert preview.version == "0.2.0"
        assert f"### Added\n\n- **app-a:** greet ([#5]({URL}/pull/5))" in preview.notes
        assert f"### Fixed\n\n- **app-a:** stop crashing ([#4]({URL}/pull/4))" in preview.notes

    def test_another_package_with_nothing_to_ship_releases_nothing(self, tmp_path: Path) -> None:
        repository = _monorepo(tmp_path)
        _branch(repository, "feat/greeting", {"apps/app-a/greet.py": "wip"})
        merge = _test_merge(repository, "feat/greeting")
        pull_request = OpenPullRequest(5, "feat(app-a): greet the user", "feat/greeting")

        preview = _preview(repository, merge, pull_request, scope=APP_B)

        assert preview.version == ""
        assert preview.error == ""
        assert not preview.touched
        assert "no commits touching apps/app-b, uv.lock since app-b/v0.1.0" in preview.reason

    def test_a_bang_in_the_title_releases_a_major_listed_as_breaking(self, tmp_path: Path) -> None:
        repository = _monorepo(tmp_path)
        _branch(repository, "fix/farewell", {"apps/app-b/bye.py": "wip"})
        merge = _test_merge(repository, "fix/farewell")
        pull_request = OpenPullRequest(6, "fix(app-b)!: say farewell once", "fix/farewell")

        preview = _preview(repository, merge, pull_request, scope=APP_B)

        assert (preview.version, preview.part) == ("1.0.0", "major")
        assert "### Changed\n\n- **BREAKING:** **app-b:** say farewell once ([#6]" in (
            preview.notes
        )

    def test_a_title_that_is_no_header_shows_the_releases_own_error(self, tmp_path: Path) -> None:
        repository = _monorepo(tmp_path)
        _branch(repository, "feat/greeting", {"apps/app-a/greet.py": "wip"})
        merge = _test_merge(repository, "feat/greeting")

        preview = _preview(repository, merge, OpenPullRequest(5, "Greet the user", "feat/greeting"))

        assert preview.version == ""
        assert preview.error == (
            "changelog failed: 'Greet the user' is not a Conventional Commits header, such as "
            "'feat(api): add the endpoint': edit the pull request title and re-run"
        )

    def test_a_branch_of_no_class_shows_the_planners_error(self, tmp_path: Path) -> None:
        repository = _monorepo(tmp_path)
        _branch(repository, "spike/greeting", {"apps/app-a/greet.py": "wip"})
        merge = _test_merge(repository, "spike/greeting")

        preview = _preview(repository, merge, OpenPullRequest(5, "feat: greet", "spike/greeting"))

        assert preview.error.startswith(
            "release plan failed: branch 'spike/greeting' matches no class"
        )

    def test_a_pull_request_outside_the_paths_still_previews_what_is_pending(
        self, tmp_path: Path
    ) -> None:
        repository = _monorepo(tmp_path)
        _branch(repository, "fix/b", {"apps/app-b/bye.py": "wip"})
        fixed = _touch(repository, "apps/app-a/fix.py", "fix: stop crashing")
        merge = _test_merge(repository, "fix/b")
        merged = {fixed: (PullRequest(4, "fix(app-a): stop crashing", "fix/crash", True),)}

        preview = _preview(repository, merge, OpenPullRequest(6, "fix(app-b): b", "fix/b"), merged)

        assert (preview.version, preview.touched) == ("0.1.1", False)
        assert "[#6]" not in preview.notes

    def test_a_bang_on_one_of_its_commits_releases_a_major(self, tmp_path: Path) -> None:
        repository = _monorepo(tmp_path)
        _branch(
            repository,
            "feat/rename",
            {"apps/app-a/one.py": "feat: one", "apps/app-a/two.py": "feat!: rename the field"},
        )
        merge = _test_merge(repository, "feat/rename")

        preview = _preview(
            repository, merge, OpenPullRequest(5, "feat(app-a): rename", "feat/rename")
        )

        assert (preview.version, preview.part) == ("1.0.0", "major")
        assert "- **BREAKING:** **app-a:** rename ([#5]" in preview.notes

    def test_a_first_release_has_no_tag_to_follow(self, tmp_path: Path) -> None:
        repository = _monorepo(tmp_path)
        _git(repository, "tag", "-d", "app-a/v0.1.0")
        start = _git(repository, "log", "-1", "--format=%H", "--", "apps/app-a")
        _branch(repository, "feat/greeting", {"apps/app-a/greet.py": "wip"})
        merge = _test_merge(repository, "feat/greeting")
        merged = {start: (PullRequest(1, "chore: start app-a", "chore/start", True),)}

        preview = _preview(
            repository, merge, OpenPullRequest(5, "feat: greet", "feat/greeting"), merged
        )

        assert (preview.version, preview.last_tag) == ("0.1.0", None)
        assert preview.notes.endswith(f"[0.1.0]: {URL}/releases/tag/app-a/v0.1.0\n")
        assert "as its first release" in preview.render("apps/app-a/CHANGELOG.md")

    def test_a_version_the_changelog_already_lists_shows_its_section(self, tmp_path: Path) -> None:
        repository = _monorepo(tmp_path)
        _branch(repository, "feat/greeting", {"apps/app-a/greet.py": "wip"})
        merge = _test_merge(repository, "feat/greeting")
        listed = "## [0.2.0] - 2026-10-01\n\n### Added\n\n- greet by hand\n"
        changelog = repository / "apps" / "app-a" / "CHANGELOG.md"
        changelog.write_text(
            f"# Changelog\n\n## [Unreleased]\n\n{listed}\n[0.2.0]: {URL}/x\n", "utf-8"
        )

        preview = _preview(repository, merge, OpenPullRequest(5, "feat: greet", "feat/greeting"))

        assert preview.notes == f"{listed}\n[0.2.0]: {URL}/x\n"

    def test_the_title_now_wins_over_what_github_reads_for_its_commits(
        self, tmp_path: Path
    ) -> None:
        repository = _monorepo(tmp_path)
        heads = _branch(repository, "feat/greeting", {"apps/app-a/greet.py": "wip"})
        merge = _test_merge(repository, "feat/greeting")
        stale = (PullRequest(5, "feat: stale title", "feat/greeting", False),)

        preview = _preview(
            repository,
            merge,
            OpenPullRequest(5, "feat: greet", "feat/greeting"),
            dict.fromkeys(heads, stale),
        )

        assert preview.notes.count("[#5]") == 1
        assert "- greet ([#5]" in preview.notes
        assert "stale" not in preview.notes

    def test_an_unscoped_release_ships_every_commit(self, tmp_path: Path) -> None:
        repository = _monorepo(tmp_path)
        _branch(repository, "fix/b", {"apps/app-b/bye.py": "wip"})
        merge = _test_merge(repository, "fix/b")

        preview = _preview(
            repository, merge, OpenPullRequest(6, "fix: b", "fix/b"), scope=ReleaseScope("app-a/v")
        )

        assert (preview.version, preview.touched) == ("0.1.1", True)
        assert "- b ([#6]" in preview.notes

    def test_git_or_github_failing_for_the_changelog_fails_the_preview(
        self, tmp_path: Path
    ) -> None:
        """An unreadable history is no release error: the step must fail, not say so."""
        repository = _monorepo(tmp_path)
        _branch(repository, "feat/greeting", {"apps/app-a/greet.py": "wip"})
        fixed = _touch(repository, "apps/app-a/fix.py", "fix: stop crashing")
        merge = _test_merge(repository, "feat/greeting")
        calls: list[str] = []

        def flaky(sha: str) -> tuple[PullRequest, ...]:
            calls.append(sha)
            if calls.count(sha) > 1:
                raise HistoryError("gh api failed: rate limited")
            return (
                (PullRequest(4, "fix: stop crashing", "fix/crash", True),) if sha == fixed else ()
            )

        with pytest.raises(ChangelogError) as raised:
            _preview(repository, merge, OpenPullRequest(5, "feat: g", "feat/greeting"), read=flaky)

        assert isinstance(raised.value.__cause__, HistoryError)

    def test_a_pull_request_touching_both_apps_releases_each(self, tmp_path: Path) -> None:
        repository = _monorepo(tmp_path)
        _branch(repository, "feat/log", {"apps/app-a/log.py": "wip", "apps/app-b/log.py": "wip"})
        merge = _test_merge(repository, "feat/log")
        pull_request = OpenPullRequest(7, "feat: log in both apps", "feat/log")

        previews = [_preview(repository, merge, pull_request, scope=s) for s in (APP_A, APP_B)]

        assert [p.version for p in previews] == ["0.2.0", "0.2.0"]
        assert all(f"- log in both apps ([#7]({URL}/pull/7))" in p.notes for p in previews)


class TestThePreviewIsTheRelease:
    """Merging the pull request writes the section the preview showed, date aside."""

    def test_the_merged_release_writes_the_previewed_section(self, tmp_path: Path) -> None:
        repository = _monorepo(tmp_path)
        heads = _branch(repository, "feat/greeting", {"apps/app-a/greet.py": "wip"})
        fixed = _touch(repository, "apps/app-a/fix.py", "fix: stop crashing")
        merge = _test_merge(repository, "feat/greeting")
        title = "feat(app-a)!: greet the user"
        merged = {fixed: (PullRequest(4, "fix(app-a): stop crashing", "fix/crash", True),)}
        preview = _preview(repository, merge, OpenPullRequest(5, title, "feat/greeting"), merged)

        _git(repository, "merge", "-q", "--no-ff", "-m", "Merge pull request #5", "feat/greeting")
        released = _git(repository, "rev-parse", "HEAD")
        shipped = {
            **merged,
            **dict.fromkeys(heads, (PullRequest(5, title, "feat/greeting", True),)),
        }
        plan = plan_release(repository, released, _reader(shipped), scope=APP_A)
        notes = build_changelog(
            repository,
            released,
            plan.version,
            _reader(shipped),
            changelog_file="apps/app-a/CHANGELOG.md",
            scope=APP_A,
            repository_url=URL,
        ).notes

        assert plan.version == preview.version == "1.0.0"
        assert notes == preview.notes

    def test_a_rebase_merge_writes_the_previewed_section(self, tmp_path: Path) -> None:
        repository = _monorepo(tmp_path)
        _branch(
            repository, "feat/two", {"apps/app-a/one.py": "feat: one", "apps/app-a/two": "fix: two"}
        )
        _touch(repository, "apps/app-b/later.py", "chore: move the base")
        merge = _test_merge(repository, "feat/two")
        title = "feat(app-a): two changes"
        preview = _preview(repository, merge, OpenPullRequest(5, title, "feat/two"))

        _git(repository, "rebase", "-q", "master", "feat/two")
        _git(repository, "checkout", "-q", "master")
        rebased = _git(repository, "rev-list", "master..feat/two").split()
        _git(repository, "merge", "-q", "--ff-only", "feat/two")
        shipped = dict.fromkeys(rebased, (PullRequest(5, title, "feat/two", True),))
        released = _git(repository, "rev-parse", "HEAD")
        plan = plan_release(repository, released, _reader(shipped), scope=APP_A)
        notes = build_changelog(
            repository,
            released,
            plan.version,
            _reader(shipped),
            changelog_file="apps/app-a/CHANGELOG.md",
            scope=APP_A,
            repository_url=URL,
        ).notes

        assert (plan.version, notes) == (preview.version, preview.notes)

    def test_a_squash_merge_drops_a_bang_that_only_a_later_commit_carried(
        self, tmp_path: Path
    ) -> None:
        """The documented divergence: GitHub's squash commit keeps the title as its header
        and lists the other commits as ``* feat!: ...`` body lines, which declare nothing."""
        repository = _monorepo(tmp_path)
        _branch(
            repository,
            "feat/two",
            {"apps/app-a/one.py": "feat: one", "apps/app-a/two.py": "feat!: rename the field"},
        )
        merge = _test_merge(repository, "feat/two")
        title = "feat(app-a): two changes"
        preview = _preview(repository, merge, OpenPullRequest(5, title, "feat/two"))

        _git(repository, "merge", "-q", "--squash", "feat/two")
        message = f"{title} (#5)\n\n* feat: one\n\n* feat!: rename the field"
        _git(repository, "commit", "-qm", message)
        squashed = _git(repository, "rev-parse", "HEAD")
        shipped = {squashed: (PullRequest(5, title, "feat/two", True),)}
        plan = plan_release(repository, squashed, _reader(shipped), scope=APP_A)

        assert (preview.version, plan.version) == ("1.0.0", "0.2.0")


class TestRender:
    def test_the_header_names_the_test_merge_and_its_base(self) -> None:
        header = preview_header("3ee9a91" + "0" * 33, "4771e69" + "1" * 33)

        assert header.startswith("## Release preview\n\n> [!NOTE]\n")
        assert "> Computed from the test merge `3ee9a91` (base `4771e69`)" in header
        assert header.endswith("\n\n")

    def test_a_release_shows_its_version_and_the_section_verbatim(self) -> None:
        notes = f"## [0.2.0] - {TODAY}\n\n### Added\n\n- greet\n"
        preview = ReleasePreview.released(
            "app-a/v", BASE, version="0.2.0", part="minor", last_tag="app-a/v0.1.0", notes=notes
        )

        text = preview.render("apps/app-a/CHANGELOG.md")

        assert text.startswith("### `app-a/v`: `app-a/v0.2.0` (minor)\n")
        assert "after `app-a/v0.1.0`" in text
        assert f"```markdown\n{notes}```\n" in text
        assert "The date is today's in UTC and provisional: the release dates" in text

    def test_no_release_says_why(self) -> None:
        preview = ReleasePreview.nothing("app-b/v", BASE, "nothing to release: no commits")

        text = preview.render("apps/app-b/CHANGELOG.md")

        assert text.startswith("### `app-b/v`: no release\n")
        assert "would not release `app-b/v` (nothing to release: no commits).\n" in text

    def test_an_error_is_fenced_longer_than_any_backticks_it_holds(self) -> None:
        preview = ReleasePreview.failed("app-a/v", BASE, "changelog failed: '```x' is not")

        text = preview.render("apps/app-a/CHANGELOG.md")

        assert text.startswith("### `app-a/v`: the release would fail\n")
        assert "````text\nchangelog failed: '```x' is not\n````\n" in text

    @pytest.mark.parametrize(
        ("scoped", "said"), [(True, "changes none of its paths"), (False, "brings no commit")]
    )
    def test_a_pending_release_this_pull_request_does_not_touch_is_explained(
        self, scoped: bool, said: str
    ) -> None:
        preview = ReleasePreview.released(
            "app-a/v",
            BASE,
            version="0.1.1",
            part="patch",
            last_tag="app-a/v0.1.0",
            notes="## x\n",
            touched=False,
            scoped=scoped,
        )

        assert f"This pull request {said}:" in preview.render("apps/app-a/CHANGELOG.md")


class TestMain:
    def _main(self, repository: Path, merge: str, output: Path, *extra: str) -> int:
        return main(
            [
                f"--repository={repository}",
                f"--merge-sha={merge}",
                "--slug=acme/mono",
                "--pull-request-number=5",
                "--pull-request-title=feat(app-a): greet the user",
                "--head-ref=feat/greeting",
                "--tag-prefix=app-a/v",
                "--paths=apps/app-a,uv.lock",
                "--changelog-file=apps/app-a/CHANGELOG.md",
                f"--output={output}",
                f"--date={TODAY}",
                "--server-url=https://github.com",
                *extra,
            ]
        )

    def _repository(self, tmp_path: Path) -> tuple[Path, str]:
        repository = _monorepo(tmp_path)
        _branch(repository, "feat/greeting", {"apps/app-a/greet.py": "wip"})
        return repository, _test_merge(repository, "feat/greeting")

    def test_it_appends_the_package_under_one_header(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repository, merge = self._repository(tmp_path)
        monkeypatch.setattr("preview_changelog.gh_commit_pull_requests", lambda slug: _reader({}))
        output = tmp_path / "preview.md"

        assert self._main(repository, merge, output) == 0
        app_b = ("--tag-prefix=app-b/v", "--paths=apps/app-b,uv.lock")
        assert self._main(repository, merge, output, *app_b) == 0

        text = output.read_text("utf-8")
        header = preview_header(merge, _git(repository, "rev-parse", f"{merge}^1"))
        assert text.startswith(header)
        assert text.count("## Release preview") == 1
        assert "### `app-a/v`: `app-a/v0.2.0` (minor)" in text
        assert "### `app-b/v`: no release" in text
        assert capsys.readouterr().out.splitlines() == [
            "version=0.2.0",
            "failed=false",
            "version=",
            "failed=false",
        ]

    def test_a_title_starting_with_a_dash_is_read_as_the_title(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repository, merge = self._repository(tmp_path)
        monkeypatch.setattr("preview_changelog.gh_commit_pull_requests", lambda slug: _reader({}))
        output = tmp_path / "preview.md"

        assert self._main(repository, merge, output, "--pull-request-title=-x") == 0

        assert capsys.readouterr().out.splitlines() == ["version=", "failed=true"]
        assert "'-x' is not a Conventional Commits header" in output.read_text("utf-8")

    def test_an_invalid_scope_fails_the_step(self, tmp_path: Path) -> None:
        repository, merge = self._repository(tmp_path)

        with pytest.raises(SystemExit):
            self._main(repository, merge, tmp_path / "p.md", "--paths=../escape")

    def test_a_merge_sha_that_is_not_a_full_sha_fails_the_step(self, tmp_path: Path) -> None:
        repository, _ = self._repository(tmp_path)

        with pytest.raises(SystemExit) as exited:
            self._main(repository, "HEAD", tmp_path / "p.md")

        assert exited.value.code == 1
        assert not (tmp_path / "p.md").exists()

    def test_a_commit_that_is_not_a_merge_fails_the_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repository, _ = self._repository(tmp_path)
        monkeypatch.setattr("preview_changelog.gh_commit_pull_requests", lambda slug: _reader({}))

        with pytest.raises(SystemExit) as exited:
            self._main(repository, _git(repository, "rev-parse", "master"), tmp_path / "p.md")

        assert exited.value.code == 1

    def test_the_plan_and_the_changelog_share_each_github_read(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repository = _monorepo(tmp_path)
        _branch(repository, "feat/greeting", {"apps/app-a/greet.py": "wip"})
        fixed = _touch(repository, "apps/app-a/fix.py", "fix: stop crashing")
        merge = _test_merge(repository, "feat/greeting")
        calls: list[str] = []

        def counting(sha: str) -> tuple[PullRequest, ...]:
            calls.append(sha)
            return (
                (PullRequest(4, "fix: stop crashing", "fix/crash", True),) if sha == fixed else ()
            )

        monkeypatch.setattr("preview_changelog.gh_commit_pull_requests", lambda slug: counting)

        assert self._main(repository, merge, tmp_path / "p.md") == 0
        assert calls == [fixed]

    def test_an_unreadable_history_fails_the_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repository, merge = self._repository(tmp_path)
        monkeypatch.setattr("preview_changelog.gh_commit_pull_requests", lambda slug: _reader({}))

        def unreadable(*args: object, **kwargs: object) -> None:
            raise ChangelogError("git log failed") from HistoryError("git log failed")

        monkeypatch.setattr("preview_changelog.build_changelog", unreadable)

        with pytest.raises(SystemExit) as exited:
            self._main(repository, merge, tmp_path / "p.md")

        assert exited.value.code == 1
