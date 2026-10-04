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
  the prefix, in the whole repository, not only under the package directory.
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

from plan_release import (
    CHANGELOG_NAME,
    CommitMergedPullRequests,
    PullRequest,
    ReleasePlanError,
    changes_only_changelogs,
    commit_message,
    commit_timestamp,
    declares_break,
    gh_commit_merged_pull_requests,
    latest_release_tag,
    shipped_commits,
)
from release_tags import DEFAULT_TAG_PREFIX, TagPrefixError, check_tag_prefix

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
    pull_requests: CommitMergedPullRequests,
    repository_url: str,
) -> tuple[Change, ...]:
    """Return one change per merged pull request the release ships, then each lone commit.

    Pull requests come in number order, lone commits oldest first. A commit
    that changes only ``CHANGELOG.md`` files is a previous release's changelog
    and is not a change.
    """
    merged: dict[int, tuple[PullRequest, bool]] = {}
    lone: list[Change] = []
    for sha in reversed(shipped_commits(repository, last_tag, merge_sha)):
        found = pull_requests(sha)
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


def _split_references(text: str) -> tuple[list[str], list[str]]:
    lines = text.rstrip("\n").splitlines()
    end = len(lines)
    while end and (not lines[end - 1].strip() or _REFERENCE.match(lines[end - 1])):
        end -= 1
    return lines[:end], [line for line in lines[end:] if line.strip()]


def _section_end(lines: Sequence[str], start: int) -> int:
    return next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )


def existing_notes(text: str, version: str) -> str | None:
    """Return the section of *version* already in *text*, with its link, or None."""
    lines, references = _split_references(text)
    heading = f"## [{version}]"
    start = next((i for i, line in enumerate(lines) if line.startswith(heading)), None)
    if start is None:
        return None
    section = "\n".join(lines[start : _section_end(lines, start)]).strip("\n") + "\n"
    link = next((ref for ref in references if ref.startswith(f"[{version}]:")), None)
    return f"{section}\n{link}\n" if link else section


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
    found = existing_notes(text, version)
    if found is not None:
        return text, found
    lines, references = _split_references(text or f"{INTRO}\n## [Unreleased]\n")
    start = next((i for i, line in enumerate(lines) if _UNRELEASED.match(line)), None)
    if start is None:
        raise ChangelogError(
            "the changelog has no '## [Unreleased]' heading, so it does not follow "
            "Keep a Changelog 1.1.0: add one under the intro, or start from an empty file"
        )
    end = _section_end(lines, start)
    head = "\n".join(lines[:end]).rstrip("\n")
    tail = "\n".join(lines[end:]).strip("\n")
    body = "\n\n".join(part for part in (head, section.rstrip("\n"), tail) if part)
    kept = [ref for ref in references if not ref.lower().startswith("[unreleased]:")]
    updated = body + "\n\n" + "\n".join([*links, *kept]) + "\n"
    return updated, section.rstrip("\n") + f"\n\n{links[1]}\n"


def release_date(repository: Path, sha: str) -> str:
    """Return the ISO 8601 calendar date, in UTC, of the commit *sha*."""
    return datetime.fromtimestamp(commit_timestamp(repository, sha), tz=UTC).date().isoformat()


def build_changelog(
    repository: Path,
    merge_sha: str,
    version: str,
    pull_requests: CommitMergedPullRequests,
    *,
    changelog_file: str,
    tag_prefix: str = DEFAULT_TAG_PREFIX,
    repository_url: str,
) -> tuple[str, str]:
    """Return the changelog with the release *merge_sha* ships, and its notes.

    Raises:
        ChangelogError: When the prefix or the file is not allowed, git cannot
            read the range, the file does not keep a changelog, or a title is
            not a Conventional Commits header with a known type.
    """
    path = repository / check_changelog_file(changelog_file)
    try:
        check_tag_prefix(tag_prefix)
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if (found := existing_notes(text, version)) is not None:
            return text, found
        last_tag = latest_release_tag(repository, merge_sha, tag_prefix)
        changes = shipped_changes(repository, last_tag, merge_sha, pull_requests, repository_url)
        date = release_date(repository, merge_sha)
    except (TagPrefixError, ReleasePlanError) as error:
        raise ChangelogError(str(error)) from error
    entries = [entry for change in changes if (entry := classify(change)) is not None]
    links = release_links(repository_url, tag_prefix, version, last_tag)
    return update_changelog(text, version, render_section(version, date, entries), links)


def _parse_args(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add the release to a Keep a Changelog file and write its notes."
    )
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--merge-sha", required=True)
    parser.add_argument("--slug", required=True, help="owner/repo the pull requests live in")
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--tag-prefix",
        default=DEFAULT_TAG_PREFIX,
        help="prefix of the release tags to read, before the version; v by default",
    )
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
            gh_commit_merged_pull_requests(options.slug),
            changelog_file=options.changelog_file,
            tag_prefix=options.tag_prefix,
            repository_url=f"{options.server_url.rstrip('/')}/{options.slug}",
        )
    except ChangelogError as error:
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
