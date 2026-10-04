"""Add the release to a Keep a Changelog file and write its section as the notes.

The file follows Keep a Changelog 1.1.0 (https://keepachangelog.com/en/1.1.0/):
the standard header and intro, an ``## [Unreleased]`` section on top, newest
version first under ``## [X.Y.Z] - YYYY-MM-DD``, the sections Added, Changed,
Deprecated, Removed, Fixed and Security in that order with empty ones left out,
and link references at the bottom that compare one release tag with the next.

- The heading carries the SemVer 2.0.0 version without the tag prefix; the links
  carry the full tags, ``control-plane/v0.1.0`` for prefix ``control-plane/v``.
- The date is the ISO 8601 calendar date, in UTC, of the commit the release is
  cut from, so a re-run writes the same date.
- One entry per merged pull request, from its title, which must be a
  Conventional Commits 1.0.0 header. A pull request merged with a merge commit
  brings every commit of its branch into the range, intermediate ones and
  reverts of them included; its title says what it changed for a user. A
  commit that came from no pull request is listed by its own subject.
- The range is the one the planner ships: every commit since the last tag of
  the prefix that changes a path of the release scope, or every commit when the
  scope names no path.
- A version already in the file is left as it is and its section is the notes,
  so re-running a release never lists it twice.

Types map onto sections as follows; any other type is refused rather than
dropped, so a mistyped title fails the release before its tag is written.

====================  ==========================================================
``feat``              Added
``fix``               Fixed; Security when a scope starts with ``sec``
``perf``/``refactor`` Changed
``revert``            Changed: a revert undoes a feature, a fix or a refactor
                      alike, and only for a feature would Removed be right
``deprecate``         Deprecated: no other convention marks a deprecation, so
                      the section is filled only when a title says so
``remove``            Removed
``build``, ``chore``, Left out: nothing a user of the release sees changes
``ci``, ``docs``,
``style``, ``test``
any type with ``!``   Changed, marked ``**BREAKING:**``; so is any title whose
or a footer           pull request holds a commit with a ``BREAKING CHANGE:``
                      footer, which also made the planner raise a major
====================  ==========================================================
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Final, NoReturn

from plan_release import declares_break
from release_history import (
    CHANGELOG_NAME,
    CommitPullRequests,
    HistoryError,
    PullRequest,
    changes_only_changelogs,
    commit_message,
    commit_timestamp,
    gh_commit_pull_requests,
    latest_release_tag,
    merged_pull_requests,
    range_log,
)
from release_scope import (
    DEFAULT_SCOPE,
    ReleaseScope,
    ReleaseScopeError,
    add_scope_arguments,
    scope_of,
)

SECTIONS: Final[tuple[str, ...]] = (
    "Added",
    "Changed",
    "Deprecated",
    "Removed",
    "Fixed",
    "Security",
)
EXCLUDED_TYPES: Final[frozenset[str]] = frozenset({"build", "chore", "ci", "docs", "style", "test"})
_SECTION_OF_TYPE: Final[dict[str, str]] = {
    "feat": "Added",
    "fix": "Fixed",
    "perf": "Changed",
    "refactor": "Changed",
    "revert": "Changed",
    "deprecate": "Deprecated",
    "remove": "Removed",
}
INTRO: Final[str] = (
    "# Changelog\n"
    "\n"
    "All notable changes to this project will be documented in this file.\n"
    "\n"
    "The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),\n"
    "and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).\n"
)
_NEW_FILE: Final[str] = f"{INTRO}\n## [Unreleased]\n"
_NO_CHANGES: Final[str] = "No user-facing changes."
_HEADER: Final[re.Pattern[str]] = re.compile(
    r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^()\s][^()]*)\))?(?P<bang>!)?: (?P<description>\S.*)$"
)
_UNRELEASED: Final[re.Pattern[str]] = re.compile(r"^## \[unreleased\]\s*$", re.IGNORECASE)
_REFERENCE: Final[re.Pattern[str]] = re.compile(r"^\[[^\]]+\]: \S+$")


class ChangelogError(RuntimeError):
    """Raised when the changelog of a release cannot be written."""


@dataclass(frozen=True, slots=True)
class Change:
    """One change the release ships: a pull request, or a commit without one."""

    title: str
    breaking: bool
    reference: str


@dataclass(frozen=True, slots=True)
class Entry:
    """One line of the changelog, and the section it belongs to."""

    section: str
    text: str


def check_changelog_file(path: str) -> PurePosixPath:
    """Return *path* when it names a ``CHANGELOG.md`` inside the checkout.

    Raises:
        ChangelogError: When *path* is empty, absolute, leaves the checkout,
            enters ``.git`` or names another file.
    """
    candidate = PurePosixPath(path)
    unsafe = (
        not path
        or "\\" in path
        or candidate.is_absolute()
        or ".." in candidate.parts
        or ".git" in candidate.parts
    )
    if unsafe or candidate.name != CHANGELOG_NAME:
        raise ChangelogError(
            f"changelog file '{path}' is not allowed: name a {CHANGELOG_NAME} inside the "
            "checkout, relative to its root, such as apps/api/CHANGELOG.md"
        )
    return candidate


def _is_security(scope: str | None) -> bool:
    return scope is not None and any(
        part.strip().lower().startswith("sec") for part in scope.split(",")
    )


def _section(kind: str, scope: str | None, breaking: bool) -> str | None:
    if breaking:
        return "Changed"
    if kind == "fix" and _is_security(scope):
        return "Security"
    if kind in _SECTION_OF_TYPE:
        return _SECTION_OF_TYPE[kind]
    if kind in EXCLUDED_TYPES:
        return None
    raise ChangelogError(
        f"type '{kind}' has no changelog section: use one of "
        f"{', '.join(sorted({*_SECTION_OF_TYPE, *EXCLUDED_TYPES}))}"
    )


def classify(change: Change) -> Entry | None:
    """Return the changelog entry of *change*, or None when no user sees it.

    Raises:
        ChangelogError: When the title is not a Conventional Commits header or
            its type has no section.
    """
    matched = _HEADER.match(change.title.strip())
    if matched is None:
        raise ChangelogError(
            f"'{change.title}' is not a Conventional Commits header, such as "
            "'feat(api): add the endpoint': edit the pull request title and re-run"
        )
    scope = matched["scope"]
    breaking = change.breaking or matched["bang"] is not None
    section = _section(matched["type"].lower(), scope, breaking)
    if section is None:
        return None
    text = matched["description"].strip()
    if scope:
        text = f"**{scope}:** {text}"
    if breaking:
        text = f"**BREAKING:** {text}"
    if change.reference:
        text = f"{text} ({change.reference})"
    return Entry(section, text)


def shipped_changes(
    repository: Path,
    last_tag: str | None,
    merge_sha: str,
    pull_requests: CommitPullRequests,
    repository_url: str,
    scope: ReleaseScope = DEFAULT_SCOPE,
) -> tuple[Change, ...]:
    """Return one change per merged pull request the release ships, then each lone commit.

    Only the commits of *scope* are read, so a pull request is listed when one
    of its commits changes a path of the scope.

    Pull requests come in number order, lone commits oldest first. A commit
    that changes only ``CHANGELOG.md`` files is a previous release's changelog
    and is not a change.
    """
    merged: dict[int, tuple[PullRequest, bool]] = {}
    lone: list[Change] = []
    for sha in reversed(range_log(repository, last_tag, merge_sha, scope)):
        found = merged_pull_requests(pull_requests, sha)
        if not found and changes_only_changelogs(repository, sha):
            continue
        message = commit_message(repository, sha)
        marked = declares_break(message)
        if not found:
            link = f"[`{sha[:7]}`]({repository_url}/commit/{sha})"
            lone.append(Change(message.partition("\n")[0].strip(), marked, link))
        for pull_request in found:
            _, seen = merged.get(pull_request.number, (pull_request, False))
            merged[pull_request.number] = (pull_request, seen or marked)
    listed = [
        Change(pull_request.title, marked, f"[#{number}]({repository_url}/pull/{number})")
        for number, (pull_request, marked) in sorted(merged.items())
    ]
    return (*listed, *lone)


def render_section(version: str, date: str, entries: Sequence[Entry]) -> str:
    """Return the ``## [version] - date`` section, its sections in the standard order."""
    lines = [f"## [{version}] - {date}"]
    for section in SECTIONS:
        items = [entry.text for entry in entries if entry.section == section]
        if items:
            lines += ["", f"### {section}", "", *(f"- {item}" for item in items)]
    if len(lines) == 1:
        lines += ["", _NO_CHANGES]
    return "\n".join(lines) + "\n"


def release_links(
    repository_url: str, tag_prefix: str, version: str, last_tag: str | None
) -> tuple[str, str]:
    """Return the ``[unreleased]`` and ``[version]`` link references of the release."""
    tag = f"{tag_prefix}{version}"
    target = (
        f"{repository_url}/compare/{last_tag}...{tag}"
        if last_tag
        else f"{repository_url}/releases/tag/{tag}"
    )
    return (f"[unreleased]: {repository_url}/compare/{tag}...HEAD", f"[{version}]: {target}")


@dataclass(frozen=True, slots=True)
class ChangelogDocument:
    """A Keep a Changelog file: its body, and the link references that close it.

    Only the parts a release reads or writes are modelled: the ``## `` headings
    that open each version, and the references at the bottom. Everything else,
    hand-written notes under ``## [Unreleased]`` included, is kept line for line.
    """

    body: tuple[str, ...]
    references: tuple[str, ...]

    @classmethod
    def parse(cls, text: str) -> ChangelogDocument:
        """Return the document *text* holds; empty *text* is a new file's header."""
        lines = (text or _NEW_FILE).rstrip("\n").splitlines()
        end = len(lines)
        while end and (not lines[end - 1].strip() or _REFERENCE.match(lines[end - 1])):
            end -= 1
        return cls(tuple(lines[:end]), tuple(line for line in lines[end:] if line.strip()))

    def notes(self, version: str) -> str | None:
        """Return the section of *version*, with its link reference, or None if unlisted."""
        heading = f"## [{version}]"
        start = next((i for i, line in enumerate(self.body) if line.startswith(heading)), None)
        if start is None:
            return None
        section = "\n".join(self.body[start : self._section_end(start)]).strip("\n") + "\n"
        link = next((ref for ref in self.references if ref.startswith(f"[{version}]:")), None)
        return f"{section}\n{link}\n" if link else section

    def with_release(self, section: str, links: Sequence[str]) -> ChangelogDocument:
        """Return the document with *section* right under ``## [Unreleased]``.

        *links* holds the ``[unreleased]`` reference first and the version's
        next; they replace the old ``[unreleased]`` reference on top of the rest.

        Raises:
            ChangelogError: When the document has no ``## [Unreleased]`` heading.
        """
        start = next((i for i, line in enumerate(self.body) if _UNRELEASED.match(line)), None)
        if start is None:
            raise ChangelogError(
                "the changelog has no '## [Unreleased]' heading, so it does not follow "
                "Keep a Changelog 1.1.0: add one under the intro, or start from an empty file"
            )
        end = self._section_end(start)
        head = _strip_blank(self.body[:end], leading=False)
        tail = _strip_blank(self.body[end:], leading=True)
        body = (*head, "", *section.rstrip("\n").split("\n"), *(("", *tail) if tail else ()))
        kept = tuple(ref for ref in self.references if not ref.lower().startswith("[unreleased]:"))
        return ChangelogDocument(body, (*links, *kept))

    def render(self) -> str:
        """Return the file: the body, a blank line, then the link references."""
        return "\n".join(self.body) + "\n\n" + "\n".join(self.references) + "\n"

    def _section_end(self, start: int) -> int:
        return next(
            (i for i in range(start + 1, len(self.body)) if self.body[i].startswith("## ")),
            len(self.body),
        )


def _strip_blank(lines: Sequence[str], *, leading: bool) -> tuple[str, ...]:
    """Return *lines* without the empty lines at their end, and at their start if *leading*."""
    first, last = 0, len(lines)
    while last > first and not lines[last - 1]:
        last -= 1
    while leading and first < last and not lines[first]:
        first += 1
    return tuple(lines[first:last])


def update_changelog(
    text: str, version: str, section: str, links: Sequence[str]
) -> tuple[str, str]:
    """Return *text* with *section* added on top, and the notes of the release.

    *links* holds the ``[unreleased]`` reference first and the version's next.
    A version *text* already lists is left as it is. Empty *text* starts the
    file with the standard header.

    Raises:
        ChangelogError: When *text* has no ``## [Unreleased]`` heading.
    """
    document = ChangelogDocument.parse(text)
    if (found := document.notes(version)) is not None:
        return text, found
    return document.with_release(section, links).render(), (
        section.rstrip("\n") + f"\n\n{links[1]}\n"
    )


def release_date(repository: Path, sha: str) -> str:
    """Return the ISO 8601 calendar date, in UTC, of the commit *sha*."""
    return datetime.fromtimestamp(commit_timestamp(repository, sha), tz=UTC).date().isoformat()


def build_changelog(
    repository: Path,
    merge_sha: str,
    version: str,
    pull_requests: CommitPullRequests,
    *,
    changelog_file: str,
    scope: ReleaseScope = DEFAULT_SCOPE,
    repository_url: str,
) -> tuple[str, str]:
    """Return the changelog with the release *merge_sha* ships, and its notes.

    Raises:
        ChangelogError: When the file is not allowed, git cannot read the range,
            the file does not keep a changelog, or a title is not a
            Conventional Commits header with a known type.
    """
    path = repository / check_changelog_file(changelog_file)
    try:
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if (found := ChangelogDocument.parse(text).notes(version)) is not None:
            return text, found
        last_tag = latest_release_tag(repository, merge_sha, scope.tag_prefix)
        changes = shipped_changes(
            repository, last_tag, merge_sha, pull_requests, repository_url, scope
        )
        date = release_date(repository, merge_sha)
    except HistoryError as error:
        raise ChangelogError(str(error)) from error
    entries = [entry for change in changes if (entry := classify(change)) is not None]
    links = release_links(repository_url, scope.tag_prefix, version, last_tag)
    return update_changelog(text, version, render_section(version, date, entries), links)


def _parse_args(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add the release to a Keep a Changelog file and write its notes."
    )
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--merge-sha", required=True)
    parser.add_argument("--slug", required=True, help="owner/repo the pull requests live in")
    parser.add_argument("--version", required=True)
    add_scope_arguments(parser)
    parser.add_argument(
        "--changelog-file", required=True, help="CHANGELOG.md to update, relative to the root"
    )
    parser.add_argument("--notes-output", type=Path, required=True)
    parser.add_argument(
        "--server-url", default=os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    )
    return parser.parse_args(arguments)


def _fail(message: str) -> NoReturn:
    print(f"changelog failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def main(arguments: Sequence[str] | None = None) -> int:
    """Write the changelog and the notes, and print ``changed=`` for ``$GITHUB_OUTPUT``."""
    options = _parse_args(arguments)
    try:
        text, notes = build_changelog(
            options.repository,
            options.merge_sha,
            options.version,
            gh_commit_pull_requests(options.slug),
            changelog_file=options.changelog_file,
            scope=scope_of(options),
            repository_url=f"{options.server_url.rstrip('/')}/{options.slug}",
        )
    except (ReleaseScopeError, ChangelogError) as error:
        _fail(str(error))
    path = options.repository / check_changelog_file(options.changelog_file)
    changed = not path.is_file() or path.read_text(encoding="utf-8") != text
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    options.notes_output.write_text(notes, encoding="utf-8")
    print(f"changed={'true' if changed else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
