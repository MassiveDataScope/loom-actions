"""Unit tests for the Keep a Changelog file a release updates.

The standards each test pins:

- Keep a Changelog 1.1.0 (https://keepachangelog.com/en/1.1.0/): the header and
  intro, an ``## [Unreleased]`` section on top, newest version first,
  ``## [X.Y.Z] - YYYY-MM-DD``, the sections Added, Changed, Deprecated, Removed,
  Fixed and Security in that order with empty ones left out, and link
  references at the bottom.
- ISO 8601: the release date is a calendar date, ``YYYY-MM-DD``, in UTC.
- SemVer 2.0.0: the heading carries the bare version, never the tag prefix.
- Conventional Commits 1.0.0: the type of a pull request title picks the
  section; ``!`` or a ``BREAKING CHANGE:`` footer marks a break.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "actions" / "release" / "plan-release" / "src"))

from changelog_file import (  # noqa: E402
    INTRO,
    Change,
    ChangelogDocument,
    ChangelogError,
    Entry,
    build_changelog,
    carry_release,
    check_changelog_file,
    classify,
    main,
    release_links,
    render_section,
    shipped_changes,
    update_changelog,
)
from release_history import PullRequest  # noqa: E402
from release_scope import ReleaseScope  # noqa: E402

URL = "https://github.com/acme/repo"
DATE = "2026-10-04"
# 2026-10-04T10:25:43Z, the merge of periplo-cloud#1; 12:25:43 in Madrid.
MERGED_AT = "2026-10-04T12:25:43+02:00"


def _git(repository: Path, *arguments: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
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


def _commit(repository: Path, message: str, when: str = MERGED_AT) -> str:
    (repository / f"f{len(_git(repository, 'ls-files').split())}").write_text(message, "utf-8")
    _git(repository, "add", ".")
    _git(
        repository,
        "commit",
        "-m",
        message,
        env={"GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when},
    )
    return _git(repository, "rev-parse", "HEAD")


def _change(title: str, breaking: bool = False) -> Change:
    return Change(title=title, breaking=breaking, reference=f"[#7]({URL}/pull/7)")


class TestClassify:
    """Conventional Commits 1.0.0 types onto Keep a Changelog 1.1.0 sections."""

    @pytest.mark.parametrize(
        ("title", "section"),
        [
            ("feat: add it", "Added"),
            ("feat(api): add it", "Added"),
            ("fix: repair it", "Fixed"),
            ("perf: speed it up", "Changed"),
            ("refactor: reshape it", "Changed"),
            # A revert undoes a feature, a fix or a refactor alike; only for a
            # feature would Removed be right, and the title does not say which.
            ("revert: undo the cache", "Changed"),
            ("deprecate(api): the v1 endpoint", "Deprecated"),
            ("remove(api): the v0 endpoint", "Removed"),
            ("fix(security): escape the header", "Security"),
            ("fix(sec): escape the header", "Security"),
            ("fix(api, sec): escape the header", "Security"),
            ("fix(Security,api): escape the header", "Security"),
            ("fix(secrets): stop logging the token", "Fixed"),
            ("fix(second-pass): parse it twice", "Fixed"),
            ("FEAT: upper case types are the same type", "Added"),
        ],
    )
    def test_the_type_picks_the_section(self, title: str, section: str) -> None:
        entry = classify(_change(title))
        assert entry is not None
        assert entry.section == section

    @pytest.mark.parametrize(
        "title",
        [
            "build: pin it",
            "chore: tidy it",
            "ci: run it",
            "docs: explain it",
            "style: format it",
            "test: cover it",
            "chore(release): prepare v1.10.1 [automated]",
        ],
    )
    def test_types_that_change_nothing_a_user_sees_are_left_out(self, title: str) -> None:
        assert classify(_change(title)) is None

    @pytest.mark.parametrize(
        "title", ["feat!: drop it", "feat(api)!: drop it", "chore!: drop Python 3.10"]
    )
    def test_a_bang_lists_the_change_as_breaking_under_changed(self, title: str) -> None:
        entry = classify(_change(title))
        assert entry is not None
        assert entry.section == "Changed"
        assert entry.text.startswith("**BREAKING:** ")

    def test_a_footer_declaring_the_break_lists_it_as_breaking_too(self) -> None:
        entry = classify(_change("fix: drop the parameter", breaking=True))
        assert entry == Entry(
            "Changed", f"**BREAKING:** drop the parameter ([#7]({URL}/pull/7))", breaking=True
        )

    def test_a_security_fix_that_breaks_is_listed_as_breaking(self) -> None:
        entry = classify(_change("fix(security)!: refuse plain HTTP"))
        assert entry is not None
        assert entry.section == "Changed"

    def test_the_text_names_the_scope_the_description_and_the_reference(self) -> None:
        entry = classify(_change("feat(control-plane): S0 skeleton"))
        assert entry == Entry("Added", f"**control-plane:** S0 skeleton ([#7]({URL}/pull/7))")

    def test_a_change_without_a_reference_has_no_trailing_link(self) -> None:
        entry = classify(Change(title="fix: repair it", breaking=False, reference=""))
        assert entry == Entry("Fixed", "repair it")

    @pytest.mark.parametrize(
        "title",
        [
            "Add the thing",
            "feat:missing space",
            "feat(): empty scope",
            "feat()!: empty scope",
            "feat add it",
            "",
        ],
    )
    def test_a_title_that_is_not_a_conventional_commit_is_refused(self, title: str) -> None:
        with pytest.raises(ChangelogError, match="not a Conventional Commits header"):
            classify(_change(title))

    def test_a_revert_button_title_is_a_change_reverting_the_quoted_header(self) -> None:
        """GitHub's revert button and ``git revert`` both title the revert ``Revert "<title>"``."""
        entry = classify(_change('Revert "feat(api): add the endpoint"'))

        assert entry == Entry(
            "Changed", f"**Reverted:** feat(api): add the endpoint ([#7]({URL}/pull/7))"
        )

    def test_a_revert_of_a_title_that_is_no_header_is_refused(self) -> None:
        with pytest.raises(ChangelogError, match="'Add the thing' is not a Conventional"):
            classify(_change('Revert "Add the thing"'))

    @pytest.mark.parametrize("title", ["spike: try it", "spike!: try it", "spike(api)!: try it"])
    def test_a_type_without_a_section_is_refused_instead_of_dropped(self, title: str) -> None:
        with pytest.raises(ChangelogError, match="type 'spike' has no changelog section"):
            classify(_change(title))

    def test_a_footer_does_not_excuse_a_type_without_a_section(self) -> None:
        with pytest.raises(ChangelogError, match="type 'spike' has no changelog section"):
            classify(_change("spike: try it", breaking=True))


class TestRenderSection:
    def test_sections_follow_the_standard_order_and_empty_ones_are_left_out(self) -> None:
        entries = (
            Entry("Security", "escape it"),
            Entry("Fixed", "repair it"),
            Entry("Added", "add it"),
            Entry("Added", "add another"),
        )

        rendered = render_section("1.2.0", DATE, entries)

        assert rendered == (
            "## [1.2.0] - 2026-10-04\n"
            "\n"
            "### Added\n"
            "\n"
            "- add it\n"
            "- add another\n"
            "\n"
            "### Fixed\n"
            "\n"
            "- repair it\n"
            "\n"
            "### Security\n"
            "\n"
            "- escape it\n"
        )

    def test_a_release_with_no_user_facing_change_says_so(self) -> None:
        assert render_section("1.2.1", DATE, ()) == (
            "## [1.2.1] - 2026-10-04\n\nNo user-facing changes.\n"
        )


class TestReleaseLinks:
    def test_the_version_compares_the_previous_tag_with_its_own_honouring_the_prefix(
        self,
    ) -> None:
        assert release_links(URL, "control-plane/v", "0.1.0", "control-plane/v0.0.0") == (
            f"[unreleased]: {URL}/compare/control-plane/v0.1.0...HEAD",
            f"[0.1.0]: {URL}/compare/control-plane/v0.0.0...control-plane/v0.1.0",
        )

    def test_a_first_release_links_its_tag(self) -> None:
        assert release_links(URL, "v", "0.1.0", None) == (
            f"[unreleased]: {URL}/compare/v0.1.0...HEAD",
            f"[0.1.0]: {URL}/releases/tag/v0.1.0",
        )


SECTION_020 = "## [0.2.0] - 2026-10-05\n\n### Added\n\n- add it\n"
LINKS_020 = (
    f"[unreleased]: {URL}/compare/v0.2.0...HEAD",
    f"[0.2.0]: {URL}/compare/v0.1.0...v0.2.0",
)
EXISTING = (
    INTRO + "\n"
    "## [Unreleased]\n"
    "\n"
    "## [0.1.0] - 2026-10-04\n"
    "\n"
    "### Fixed\n"
    "\n"
    "- repair it\n"
    "\n"
    f"[unreleased]: {URL}/compare/v0.1.0...HEAD\n"
    f"[0.1.0]: {URL}/releases/tag/v0.1.0\n"
)


class TestUpdateChangelog:
    def test_a_new_file_starts_with_the_standard_header_and_an_unreleased_section(
        self,
    ) -> None:
        update = update_changelog("", "0.2.0", SECTION_020, LINKS_020)
        text, notes = update.text, update.notes

        assert text == (
            "# Changelog\n"
            "\n"
            "All notable changes to this project will be documented in this file.\n"
            "\n"
            "The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),\n"
            "and this project adheres to "
            "[Semantic Versioning](https://semver.org/spec/v2.0.0.html).\n"
            "\n"
            "## [Unreleased]\n"
            "\n"
            "## [0.2.0] - 2026-10-05\n"
            "\n"
            "### Added\n"
            "\n"
            "- add it\n"
            "\n"
            f"[unreleased]: {URL}/compare/v0.2.0...HEAD\n"
            f"[0.2.0]: {URL}/compare/v0.1.0...v0.2.0\n"
        )
        assert notes == SECTION_020 + "\n" + LINKS_020[1] + "\n"

    def test_the_newest_version_goes_first_and_the_links_follow(self) -> None:
        text = update_changelog(EXISTING, "0.2.0", SECTION_020, LINKS_020).text

        assert text == (
            INTRO + "\n"
            "## [Unreleased]\n"
            "\n"
            "## [0.2.0] - 2026-10-05\n"
            "\n"
            "### Added\n"
            "\n"
            "- add it\n"
            "\n"
            "## [0.1.0] - 2026-10-04\n"
            "\n"
            "### Fixed\n"
            "\n"
            "- repair it\n"
            "\n"
            f"[unreleased]: {URL}/compare/v0.2.0...HEAD\n"
            f"[0.2.0]: {URL}/compare/v0.1.0...v0.2.0\n"
            f"[0.1.0]: {URL}/releases/tag/v0.1.0\n"
        )

    def test_hand_written_unreleased_notes_are_kept_where_they_are(self) -> None:
        existing = EXISTING.replace(
            "## [Unreleased]\n", "## [Unreleased]\n\n### Added\n\n- planned by hand\n"
        )

        text = update_changelog(existing, "0.2.0", SECTION_020, LINKS_020).text

        assert "## [Unreleased]\n\n### Added\n\n- planned by hand\n\n## [0.2.0]" in text

    def test_a_version_already_listed_is_not_listed_twice(self) -> None:
        once = update_changelog(EXISTING, "0.2.0", SECTION_020, LINKS_020)

        twice = update_changelog(once.text, "0.2.0", "## [0.2.0] - 2027-01-01\n", LINKS_020)

        assert (once.changed, twice.changed) == (True, False)
        assert (twice.text, twice.notes) == (once.text, once.notes)

    def test_a_rerun_of_the_first_release_keeps_its_notes(self) -> None:
        update = update_changelog(EXISTING, "0.1.0", "## [0.1.0] - 2027-01-01\n", ())
        text, notes = update.text, update.notes

        assert text == EXISTING
        assert notes == (
            "## [0.1.0] - 2026-10-04\n\n### Fixed\n\n- repair it\n"
            f"\n[0.1.0]: {URL}/releases/tag/v0.1.0\n"
        )

    def test_a_file_that_does_not_keep_a_changelog_is_refused(self) -> None:
        with pytest.raises(ChangelogError, match=r"no '## \[Unreleased\]' heading"):
            update_changelog("# 🚀 Release 1.0.0\n\n- feat: one\n", "1.1.0", SECTION_020, ())


class TestCarryRelease:
    """The release a plan built, carried onto the file the base branch holds by commit time."""

    BUILT = update_changelog(EXISTING, "0.2.0", SECTION_020, LINKS_020).text

    def test_onto_the_file_it_was_built_on_it_is_the_built_file(self) -> None:
        update = carry_release(self.BUILT, EXISTING, "0.2.0")

        assert (update.text, update.changed) == (self.BUILT, True)

    def test_a_change_made_on_the_branch_since_is_kept(self) -> None:
        moved = EXISTING.replace("## [Unreleased]\n", "## [Unreleased]\n\n- planned by hand\n")

        update = carry_release(self.BUILT, moved, "0.2.0")

        assert update.changed is True
        assert "## [Unreleased]\n\n- planned by hand\n\n## [0.2.0] - 2026-10-05\n" in update.text
        assert update.text.endswith(
            f"[unreleased]: {URL}/compare/v0.2.0...HEAD\n"
            f"[0.2.0]: {URL}/compare/v0.1.0...v0.2.0\n"
            f"[0.1.0]: {URL}/releases/tag/v0.1.0\n"
        )

    def test_a_branch_that_already_lists_the_version_is_left_alone(self) -> None:
        update = carry_release(self.BUILT, self.BUILT, "0.2.0")

        assert (update.text, update.changed) == (self.BUILT, False)

    def test_a_missing_file_gets_the_header_and_the_release(self) -> None:
        update = carry_release(self.BUILT, "", "0.2.0")

        assert update.text.startswith(INTRO + "\n## [Unreleased]\n\n## [0.2.0] - 2026-10-05\n")

    def test_a_built_file_without_the_release_is_refused(self) -> None:
        with pytest.raises(ChangelogError, match="does not list 0.3.0"):
            carry_release(self.BUILT, EXISTING, "0.3.0")


class TestChangelogDocument:
    def test_the_references_at_the_bottom_are_parsed_apart_from_the_body(self) -> None:
        document = ChangelogDocument.parse(EXISTING)

        assert document.body[-1] == "- repair it"
        assert document.references == (
            f"[unreleased]: {URL}/compare/v0.1.0...HEAD",
            f"[0.1.0]: {URL}/releases/tag/v0.1.0",
        )

    def test_an_empty_file_is_the_standard_header_and_an_unreleased_section(self) -> None:
        document = ChangelogDocument.parse("")

        assert document.render() == INTRO + "\n## [Unreleased]\n\n\n"
        assert document.notes("0.1.0") is None

    def test_the_notes_of_a_listed_version_carry_its_link(self) -> None:
        assert ChangelogDocument.parse(EXISTING).notes("0.1.0") == (
            f"## [0.1.0] - 2026-10-04\n\n### Fixed\n\n- repair it\n\n"
            f"[0.1.0]: {URL}/releases/tag/v0.1.0\n"
        )


class TestCheckChangelogFile:
    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("CHANGELOG.md", "CHANGELOG.md"),
            ("./CHANGELOG.md", "CHANGELOG.md"),
            ("apps/control-plane/CHANGELOG.md", "apps/control-plane/CHANGELOG.md"),
        ],
    )
    def test_a_changelog_inside_the_checkout_is_accepted(self, path: str, expected: str) -> None:
        assert check_changelog_file(path).as_posix() == expected

    @pytest.mark.parametrize(
        "path",
        [
            "",
            "/etc/CHANGELOG.md",
            "../CHANGELOG.md",
            "apps/../../CHANGELOG.md",
            ".git/CHANGELOG.md",
            "apps\\CHANGELOG.md",
            "HISTORY.md",
            "changelog.md",
        ],
    )
    def test_anything_else_is_refused(self, path: str) -> None:
        with pytest.raises(ChangelogError, match="changelog file"):
            check_changelog_file(path)


def _pr(number: int, title: str, merged: bool = True) -> PullRequest:
    return PullRequest(number, title, f"branch/{number}", merged)


def _reader(pull_requests: dict[str, tuple[PullRequest, ...]]):  # type: ignore[no-untyped-def]
    return lambda sha: pull_requests.get(sha, ())


class TestShippedChanges:
    """One entry per merged pull request, from its title; a commit only without one."""

    def test_a_pull_request_is_one_change_however_many_commits_it_holds(
        self, tmp_path: Path
    ) -> None:
        repository = _repository(tmp_path)
        _git(repository, "tag", "v0.1.0")
        title = "feat(api): add the endpoint"
        shas = [
            _commit(repository, "feat(api): first try"),
            _commit(repository, 'revert: "feat(api): first try"'),
            _commit(repository, "feat(api): the endpoint"),
        ]
        pull_request = _pr(number=12, title=title)

        changes = shipped_changes(
            repository, "v0.1.0", shas[-1], _reader(dict.fromkeys(shas, (pull_request,))), URL
        )

        assert changes == (Change(title, False, f"[#12]({URL}/pull/12)"),)

    def test_a_commit_of_the_pull_request_declaring_a_break_marks_it(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _git(repository, "tag", "v0.1.0")
        first = _commit(repository, "feat: one")
        marked = _commit(repository, "fix: two\n\nBREAKING CHANGE: the field is gone")
        pull_request = _pr(number=3, title="feat: one and two")

        (change,) = shipped_changes(
            repository,
            "v0.1.0",
            marked,
            _reader({first: (pull_request,), marked: (pull_request,)}),
            URL,
        )

        assert change.breaking is True

    def test_pull_requests_are_listed_by_number(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _git(repository, "tag", "v0.1.0")
        later = _commit(repository, "fix: later")
        earlier = _commit(repository, "feat: earlier")

        changes = shipped_changes(
            repository,
            "v0.1.0",
            earlier,
            _reader(
                {
                    later: (_pr(9, "fix: later"),),
                    earlier: (_pr(4, "feat: earlier"),),
                }
            ),
            URL,
        )

        assert [change.title for change in changes] == ["feat: earlier", "fix: later"]

    def test_a_pull_request_closed_without_merging_is_not_listed(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _git(repository, "tag", "v0.1.0")
        merge = _commit(repository, "feat: one")
        closed = _pr(8, "feat: tried and abandoned", merged=False)

        changes = shipped_changes(
            repository, "v0.1.0", merge, _reader({merge: (closed, _pr(9, "feat: one"))}), URL
        )

        assert [change.title for change in changes] == ["feat: one"]

    def test_a_commit_without_a_pull_request_is_listed_by_its_subject(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _git(repository, "tag", "v0.1.0")
        pushed = _commit(repository, "fix: direct\n\nbody")

        changes = shipped_changes(repository, "v0.1.0", pushed, _reader({}), URL)

        assert changes == (
            Change("fix: direct", False, f"[`{pushed[:7]}`]({URL}/commit/{pushed})"),
        )

    def test_a_changelog_commit_is_not_a_change(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _git(repository, "tag", "v0.1.0")
        (repository / "CHANGELOG.md").write_text("# Changelog\n", "utf-8")
        _git(repository, "add", "CHANGELOG.md")
        _git(repository, "commit", "-m", "update the changelog")
        merge = _commit(repository, "feat: one")

        changes = shipped_changes(
            repository, "v0.1.0", merge, _reader({merge: (_pr(5, "feat: one"),)}), URL
        )

        assert [change.title for change in changes] == ["feat: one"]


# periplo-cloud's first control-plane release: PR #1 merged on 2026-10-04 with a
# merge commit, so every commit of its branch reaches the range, chores, tests
# and intermediate fixes included.
PERIPLO_COMMITS = (
    "feat(control-plane): S0 skeleton with hexagonal layout and RLS schema",
    "chore(control-plane): pin loom to the pushed PR commit",
    "fix(control-plane): address pre-merge reviews of S0",
    "refactor(audit): bound the chain-head loop to two attempts",
    "test(audit): one call inside pytest.raises in the new refusal tests",
)
PERIPLO_CHANGELOG = (
    "# Changelog\n"
    "\n"
    "All notable changes to this project will be documented in this file.\n"
    "\n"
    "The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),\n"
    "and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).\n"
    "\n"
    "## [Unreleased]\n"
    "\n"
    "## [0.1.0] - 2026-10-04\n"
    "\n"
    "### Added\n"
    "\n"
    "- **control-plane:** S0 skeleton of the control plane (016c-1) "
    "([#1](https://github.com/MassiveDataScope/periplo-cloud/pull/1))\n"
    "\n"
    "[unreleased]: https://github.com/MassiveDataScope/periplo-cloud/compare/"
    "control-plane/v0.1.0...HEAD\n"
    "[0.1.0]: https://github.com/MassiveDataScope/periplo-cloud/compare/"
    "control-plane/v0.0.0...control-plane/v0.1.0\n"
)


def _periplo(tmp_path: Path) -> tuple[Path, str, dict[str, tuple[PullRequest, ...]]]:
    repository = _repository(tmp_path)
    _git(repository, "tag", "-a", "control-plane/v0.0.0", "-m", "baseline")
    (repository / "apps" / "control-plane").mkdir(parents=True)
    shas = [_commit(repository, message) for message in PERIPLO_COMMITS]
    title = "feat(control-plane): S0 skeleton of the control plane (016c-1)"
    return repository, shas[-1], dict.fromkeys(shas, (_pr(1, title),))


class TestBuildChangelog:
    def test_periplo_clouds_first_control_plane_release(self, tmp_path: Path) -> None:
        repository, merge, pull_requests = _periplo(tmp_path)

        update = build_changelog(
            repository,
            merge,
            "0.1.0",
            _reader(pull_requests),
            changelog_file="apps/control-plane/CHANGELOG.md",
            scope=ReleaseScope("control-plane/v"),
            repository_url="https://github.com/MassiveDataScope/periplo-cloud",
        )
        text, notes = update.text, update.notes

        assert text == PERIPLO_CHANGELOG
        assert notes == (
            "## [0.1.0] - 2026-10-04\n"
            "\n"
            "### Added\n"
            "\n"
            "- **control-plane:** S0 skeleton of the control plane (016c-1) "
            "([#1](https://github.com/MassiveDataScope/periplo-cloud/pull/1))\n"
            "\n"
            "[0.1.0]: https://github.com/MassiveDataScope/periplo-cloud/compare/"
            "control-plane/v0.0.0...control-plane/v0.1.0\n"
        )

    def test_the_date_is_the_merge_commits_utc_calendar_date(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _git(repository, "tag", "v0.1.0")
        # 01:30 in Madrid on the 5th is still the 4th in UTC.
        merge = _commit(repository, "feat: one", when="2026-10-05T01:30:00+02:00")

        text = build_changelog(
            repository,
            merge,
            "0.2.0",
            _reader({merge: (_pr(2, "feat: one"),)}),
            changelog_file="CHANGELOG.md",
            scope=ReleaseScope("v"),
            repository_url=URL,
        ).text

        assert "## [0.2.0] - 2026-10-04\n" in text

    def test_a_listed_version_is_kept_even_if_a_title_was_since_made_invalid(
        self, tmp_path: Path
    ) -> None:
        repository, merge, pull_requests = _periplo(tmp_path)
        target = repository / "apps" / "control-plane" / "CHANGELOG.md"
        target.write_text(PERIPLO_CHANGELOG, "utf-8")
        renamed = {sha: (_pr(1, "S0 skeleton"),) for sha in pull_requests}

        text = build_changelog(
            repository,
            merge,
            "0.1.0",
            _reader(renamed),
            changelog_file="apps/control-plane/CHANGELOG.md",
            scope=ReleaseScope("control-plane/v"),
            repository_url="https://github.com/MassiveDataScope/periplo-cloud",
        ).text

        assert text == PERIPLO_CHANGELOG

    def test_an_unsafe_prefix_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repository = _repository(tmp_path)
        with pytest.raises(SystemExit) as exited:
            main(
                [
                    *("--repository", str(repository), "--merge-sha", "HEAD", "--slug", "o/r"),
                    *("--version", "0.1.0", "--changelog-file", "CHANGELOG.md"),
                    *("--notes-output", str(tmp_path / "notes.md"), "--tag-prefix=-v"),
                ]
            )

        assert exited.value.code == 1
        assert "changelog failed: tag prefix '-v' is not allowed" in capsys.readouterr().err
        assert not (repository / "CHANGELOG.md").exists()


class TestABreakNeedsAMajor:
    """A **BREAKING:** entry under a version that is not a major would contradict SemVer.

    The planner raises a major for the same marks, so this only fails a version
    passed by hand. On 0.x the planner's major is 1.0.0, so a break is too.
    """

    def _build(self, tmp_path: Path, last_tag: str, version: str) -> str:
        repository = _repository(tmp_path)
        _git(repository, "tag", last_tag)
        merge = _commit(repository, "feat: rename the field")
        notes = build_changelog(
            repository,
            merge,
            version,
            _reader({merge: (_pr(1, "feat(api)!: rename the field"),)}),
            changelog_file="CHANGELOG.md",
            scope=ReleaseScope("v"),
            repository_url=URL,
        ).notes
        return notes

    @pytest.mark.parametrize(
        ("last_tag", "version", "major"),
        [("v1.2.0", "1.3.0", "2.0.0"), ("v1.2.0", "1.2.1", "2.0.0"), ("v0.3.0", "0.4.0", "1.0.0")],
    )
    def test_a_break_under_another_version_is_refused(
        self, tmp_path: Path, last_tag: str, version: str, major: str
    ) -> None:
        with pytest.raises(ChangelogError, match=f"{version} is not {major}"):
            self._build(tmp_path, last_tag, version)

    @pytest.mark.parametrize(("last_tag", "version"), [("v1.2.0", "2.0.0"), ("v0.3.0", "1.0.0")])
    def test_a_break_under_the_major_is_listed(
        self, tmp_path: Path, last_tag: str, version: str
    ) -> None:
        notes = self._build(tmp_path, last_tag, version)

        assert "- **BREAKING:** **api:** rename the field" in notes


def _touch(repository: Path, path: str, message: str) -> str:
    target = repository / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(message, "utf-8")
    _git(repository, "add", path)
    _git(
        repository,
        "commit",
        "-m",
        message,
        env={"GIT_COMMITTER_DATE": MERGED_AT, "GIT_AUTHOR_DATE": MERGED_AT},
    )
    return _git(repository, "rev-parse", "HEAD")


class TestPathScope:
    """A package of a monorepo lists only the pull requests that touch its paths."""

    def _build(self, repository: Path, merge: str, readers: dict[str, tuple[PullRequest, ...]]):  # type: ignore[no-untyped-def]
        return build_changelog(
            repository,
            merge,
            "0.2.0",
            _reader(readers),
            changelog_file="apps/app-a/CHANGELOG.md",
            scope=ReleaseScope("app-a/v", ("apps/app-a",)),
            repository_url=URL,
        )

    def test_only_the_pull_requests_touching_the_package_are_listed(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _git(repository, "tag", "app-a/v0.1.0")
        mine = _touch(repository, "apps/app-a/src.py", "feat(app-a): one")
        other = _touch(repository, "apps/app-b/src.py", "fix(app-b): two")
        both = _touch(repository, "apps/app-a/x.py", "fix: three") and _touch(
            repository, "apps/app-b/x.py", "fix: three"
        )
        both_first = _git(repository, "rev-parse", "HEAD~1")
        readers = {
            mine: (_pr(1, "feat(app-a): one"),),
            other: (_pr(2, "fix(app-b): two"),),
            both_first: (_pr(3, "fix: three"),),
            both: (_pr(3, "fix: three"),),
        }

        notes = self._build(repository, both, readers).notes

        assert notes == (
            "## [0.2.0] - 2026-10-04\n\n### Added\n\n"
            f"- **app-a:** one ([#1]({URL}/pull/1))\n\n### Fixed\n\n"
            f"- three ([#3]({URL}/pull/3))\n\n"
            f"[0.2.0]: {URL}/compare/app-a/v0.1.0...app-a/v0.2.0\n"
        )

    def test_another_packages_changelog_commit_is_not_read(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path)
        _git(repository, "tag", "app-a/v0.1.0")
        _touch(repository, "apps/app-b/CHANGELOG.md", "# Changelog\n")
        mine = _touch(repository, "apps/app-a/src.py", "fix(app-a): one")

        notes = self._build(repository, mine, {mine: (_pr(4, "fix(app-a): one"),)}).notes

        assert f"- **app-a:** one ([#4]({URL}/pull/4))" in notes


class TestMain:
    def _run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> tuple[int, str, Path, Path]:
        repository, merge, pull_requests = _periplo(tmp_path)
        monkeypatch.setattr(
            "changelog_file.gh_commit_pull_requests", lambda _slug: _reader(pull_requests)
        )
        monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
        notes = tmp_path / "CHANGELOG_RELEASE.md"
        code = main(
            [
                "--repository",
                str(repository),
                "--merge-sha",
                merge,
                "--slug",
                "MassiveDataScope/periplo-cloud",
                "--version",
                "0.1.0",
                "--tag-prefix=control-plane/v",
                "--changelog-file",
                "apps/control-plane/CHANGELOG.md",
                "--notes-output",
                str(notes),
            ]
        )
        return code, capsys.readouterr().out, repository, notes

    def test_writes_the_file_and_the_notes_and_reports_the_change(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        code, out, repository, notes = self._run(tmp_path, monkeypatch, capsys)

        assert code == 0
        assert out == "changed=true\n"
        target = repository / "apps" / "control-plane" / "CHANGELOG.md"
        assert target.read_text("utf-8") == PERIPLO_CHANGELOG
        assert notes.read_text("utf-8").startswith("## [0.1.0] - 2026-10-04\n")

    def test_a_rerun_reports_no_change(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        first = tmp_path / "first"
        first.mkdir()
        _, _, repository, notes = self._run(first, monkeypatch, capsys)
        target = repository / "apps" / "control-plane" / "CHANGELOG.md"
        written = target.read_text("utf-8")
        written_notes = notes.read_text("utf-8")

        code = main(
            [
                "--repository",
                str(repository),
                "--merge-sha",
                _git(repository, "rev-parse", "HEAD"),
                "--slug",
                "MassiveDataScope/periplo-cloud",
                "--version",
                "0.1.0",
                "--tag-prefix=control-plane/v",
                "--changelog-file",
                "apps/control-plane/CHANGELOG.md",
                "--notes-output",
                str(notes),
            ]
        )

        assert code == 0
        assert capsys.readouterr().out == "changed=false\n"
        assert target.read_text("utf-8") == written
        assert notes.read_text("utf-8") == written_notes

    def test_a_refusal_fails_before_writing_anything(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repository = _repository(tmp_path)
        notes = tmp_path / "notes.md"

        with pytest.raises(SystemExit) as exited:
            main(
                [
                    "--repository",
                    str(repository),
                    "--merge-sha",
                    "HEAD",
                    "--slug",
                    "o/r",
                    "--version",
                    "0.1.0",
                    "--changelog-file",
                    "../CHANGELOG.md",
                    "--notes-output",
                    str(notes),
                ]
            )

        assert exited.value.code == 1
        assert "changelog failed: changelog file" in capsys.readouterr().err
        assert not notes.exists()
