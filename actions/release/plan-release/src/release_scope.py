"""Bound a release line: the tags it reads and, in a monorepo, the paths it ships.

The planner, the changelog, the notes and the break detection all read the
same scope, so they can never disagree on what a release ships. It is checked
once, when built from the command line, before any of them reads git.

Without paths a release ships every commit since its last tag, as it always
did. With paths it ships only the commits that change one of them: a package
of a monorepo names its directory and the files it shares with the others,
such as ``uv.lock``, so another package's pull requests never raise its
version nor reach its changelog. A path is a directory or file relative to the
root of the checkout, read literally: :mod:`release_history` runs git with
``GIT_LITERAL_PATHSPECS=1``, so ``apps/*`` names a directory called ``*``, not
a glob. It may not leave the checkout nor start with ``-``, which git would read
as an option, or ``:``, which reads as pathspec magic outside literal mode.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

from release_tags import DEFAULT_TAG_PREFIX, TagPrefixError, check_tag_prefix

# The variable plan-release's composite steps set from its ``paths`` input.
PATHS_VARIABLE: Final[str] = "RELEASE_PATHS"
_SEPARATORS: Final[re.Pattern[str]] = re.compile(r"[,\n]")


class ReleaseScopeError(ValueError):
    """Raised when a scope could not bound a release safely."""


def _check_path(path: str) -> None:
    unsafe = (
        not path
        or path.startswith(("-", ":"))
        or "\\" in path
        or PurePosixPath(path).is_absolute()
        or ".." in PurePosixPath(path).parts
    )
    if unsafe:
        raise ReleaseScopeError(
            f"path '{path}' is not allowed: name a directory or file inside the checkout, "
            "relative to its root, such as apps/api or uv.lock"
        )


@dataclass(frozen=True, slots=True)
class ReleaseScope:
    """The release line a release belongs to.

    Attributes:
        tag_prefix: Prefix of its release tags, before the version; see
            :func:`release_tags.check_tag_prefix`.
        paths:      Paths whose commits it ships; none ships every commit. Each
            is normalised (``./apps/a/`` is ``apps/a``) and kept once, in the
            order given.

    Raises:
        ReleaseScopeError: When the prefix or a path is not allowed.
    """

    tag_prefix: str = DEFAULT_TAG_PREFIX
    paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            check_tag_prefix(self.tag_prefix)
        except TagPrefixError as error:
            raise ReleaseScopeError(str(error)) from error
        for path in self.paths:
            _check_path(path)
        normalised = (str(PurePosixPath(path)) for path in self.paths)
        # Frozen: the normalised paths replace the given ones once, here.
        object.__setattr__(self, "paths", tuple(dict.fromkeys(normalised)))

    @classmethod
    def parse(cls, tag_prefix: str, paths: str) -> ReleaseScope:
        """Return the scope of *tag_prefix* and *paths*, separated by newlines or commas.

        Blank entries are skipped.

        Raises:
            ReleaseScopeError: When the prefix or a path is not allowed.
        """
        entries = (entry.strip() for entry in _SEPARATORS.split(paths))
        return cls(tag_prefix, tuple(entry for entry in entries if entry))

    @property
    def log_limits(self) -> tuple[str, ...]:
        """Return the ``git log`` arguments that keep the commits changing the paths.

        ``--full-history`` keeps every commit that changes a path, the way the
        unscoped range keeps every commit: without it git would drop a merged
        branch whose changes to the paths cancel out. Unscoped, there are none,
        so the command is the one it always was. The paths are literal only
        when git runs with ``GIT_LITERAL_PATHSPECS=1``, as
        :func:`release_history.run` does.
        """
        return ("--full-history", "--", *self.paths) if self.paths else ()

    def no_commits(self, last_tag: str | None) -> str:
        """Return why a range holding no commit of this scope ships nothing."""
        touching = f" touching {', '.join(self.paths)}" if self.paths else ""
        return (
            f"nothing to release: no commits{touching} since {last_tag or 'the start of history'}"
        )


DEFAULT_SCOPE: Final[ReleaseScope] = ReleaseScope()


def add_scope_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the options :func:`scope_of` reads to *parser*."""
    parser.add_argument(
        "--tag-prefix",
        default=DEFAULT_TAG_PREFIX,
        help="prefix of the release tags to read, before the version; v by default",
    )
    parser.add_argument(
        "--paths",
        default=os.environ.get(PATHS_VARIABLE, ""),
        help="paths whose commits the release ships, separated by newlines or commas; "
        f"${PATHS_VARIABLE} by default, and empty ships every commit",
    )


def scope_of(options: argparse.Namespace) -> ReleaseScope:
    """Return the scope the options added by :func:`add_scope_arguments` describe.

    Raises:
        ReleaseScopeError: When the prefix or a path is not allowed.
    """
    return ReleaseScope.parse(options.tag_prefix, options.paths)
