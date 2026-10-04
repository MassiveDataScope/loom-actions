"""Build the release notes for every commit accumulated since the last tag."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from release_history import HistoryError, latest_release_tag, range_log
from release_scope import (
    DEFAULT_SCOPE,
    ReleaseScope,
    ReleaseScopeError,
    add_scope_arguments,
    scope_of,
)


class ReleaseNotesError(RuntimeError):
    """Raised when the notes for a release cannot be built."""


def release_entries(
    repository: Path, last_tag: str | None, scope: ReleaseScope = DEFAULT_SCOPE
) -> tuple[str, ...]:
    """Return the subject of every non-merge commit of *scope* the release ships."""
    return range_log(repository, last_tag, "HEAD", scope, pretty="%s")


def render_release_notes(
    version: str,
    last_tag: str | None,
    entries: Sequence[str],
    scope: ReleaseScope = DEFAULT_SCOPE,
) -> str:
    """Render the notes body, refusing a release that ships no commit of *scope*."""
    if not entries:
        raise ReleaseNotesError(scope.no_commits(last_tag))
    since = f"Changes since {last_tag}:" if last_tag else "Changes:"
    listed = "\n".join(f"- {entry}" for entry in entries)
    return f"# 🚀 Release {version}\n\n{since}\n\n{listed}\n"


def build_release_notes(repository: Path, version: str, scope: ReleaseScope = DEFAULT_SCOPE) -> str:
    """Return the notes for every commit between the last reachable tag of *scope* and HEAD."""
    last_tag = latest_release_tag(repository, "HEAD", scope.tag_prefix)
    entries = release_entries(repository, last_tag, scope)
    return render_release_notes(version, last_tag, entries, scope)


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
    except (ReleaseScopeError, ReleaseNotesError, HistoryError) as error:
        _fail(str(error))
    options.output.write_text(notes, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
