"""Build the release notes for every commit accumulated since the last tag."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from release_scope import (
    DEFAULT_SCOPE,
    ReleaseScope,
    ReleaseScopeError,
    add_scope_arguments,
    scope_of,
)
from release_tags import DEFAULT_TAG_PREFIX, release_tag_glob, release_tag_pattern


class ReleaseNotesError(RuntimeError):
    """Raised when the notes for a release cannot be built."""


def _run_git(repository: Path, *arguments: str) -> str:
    command = ("git", "-C", str(repository), *arguments)
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise ReleaseNotesError(f"{' '.join(command)} failed: {detail.strip()}") from error
    return completed.stdout


def _tagged_commit(repository: Path, tag: str) -> str:
    return _run_git(repository, "rev-list", "-n", "1", tag).strip()


def latest_release_tag(repository: Path, prefix: str = DEFAULT_TAG_PREFIX) -> str | None:
    """Return the highest *prefix* version tag before HEAD, ignoring a tag on HEAD itself.

    Notes are rebuilt when a release is re-run for a commit that is already
    tagged, so a tag pointing at HEAD is not a release that preceded it. Tags
    with another prefix belong to another release line and are not read.

    Raises:
        TagPrefixError: When *prefix* is not allowed.
    """
    pattern = release_tag_pattern(prefix)
    head = _run_git(repository, "rev-parse", "HEAD").strip()
    output = _run_git(
        repository,
        "tag",
        "--list",
        release_tag_glob(prefix),
        "--merged",
        "HEAD",
        "--sort=-v:refname",
    )
    for line in output.splitlines():
        candidate = line.strip()
        if pattern.match(candidate) and _tagged_commit(repository, candidate) == head:
            continue
        if pattern.match(candidate):
            return candidate
    return None


def release_entries(repository: Path, last_tag: str | None) -> tuple[str, ...]:
    """Return one entry per non-merge commit that the release ships."""
    revision_range = f"{last_tag}..HEAD" if last_tag else "HEAD"
    output = _run_git(repository, "log", "--no-merges", "--pretty=%s", revision_range)
    return tuple(line.strip() for line in output.splitlines() if line.strip())


def render_release_notes(version: str, last_tag: str | None, entries: Sequence[str]) -> str:
    """Render the notes body, refusing a release that ships no commits."""
    if not entries:
        raise ReleaseNotesError(
            f"nothing to release: no commits since {last_tag or 'the start of history'}"
        )
    since = f"Changes since {last_tag}:" if last_tag else "Changes:"
    listed = "\n".join(f"- {entry}" for entry in entries)
    return f"# 🚀 Release {version}\n\n{since}\n\n{listed}\n"


def build_release_notes(repository: Path, version: str, scope: ReleaseScope = DEFAULT_SCOPE) -> str:
    """Return the notes for every commit between the last reachable tag of *scope* and HEAD."""
    last_tag = latest_release_tag(repository, scope.tag_prefix)
    return render_release_notes(version, last_tag, release_entries(repository, last_tag))


def _parse_args(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build release notes from the accumulated commits."
    )
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    add_scope_arguments(parser)
    return parser.parse_args(arguments)


def _fail(message: str) -> NoReturn:
    print(f"release notes failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parse_args(arguments)
    try:
        notes = build_release_notes(options.repository, options.version, scope_of(options))
    except (ReleaseScopeError, ReleaseNotesError) as error:
        _fail(str(error))
    options.output.write_text(notes, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
