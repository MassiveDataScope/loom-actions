"""Bound a release line: the tags it reads to plan a release and write its notes.

The planner, the changelog, the notes and the break detection all read the
same scope, so they can never disagree on what a release ships. It is checked
once, when built from the command line, before any of them reads git.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Final

from release_tags import DEFAULT_TAG_PREFIX, TagPrefixError, check_tag_prefix


class ReleaseScopeError(ValueError):
    """Raised when a scope could not bound a release safely."""


@dataclass(frozen=True, slots=True)
class ReleaseScope:
    """The release line a release belongs to.

    Attributes:
        tag_prefix: Prefix of its release tags, before the version; see
            :func:`release_tags.check_tag_prefix`.

    Raises:
        ReleaseScopeError: When the prefix is not allowed.
    """

    tag_prefix: str = DEFAULT_TAG_PREFIX

    def __post_init__(self) -> None:
        try:
            check_tag_prefix(self.tag_prefix)
        except TagPrefixError as error:
            raise ReleaseScopeError(str(error)) from error


DEFAULT_SCOPE: Final[ReleaseScope] = ReleaseScope()


def add_scope_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the options :func:`scope_of` reads to *parser*."""
    parser.add_argument(
        "--tag-prefix",
        default=DEFAULT_TAG_PREFIX,
        help="prefix of the release tags to read, before the version; v by default",
    )


def scope_of(options: argparse.Namespace) -> ReleaseScope:
    """Return the scope the options added by :func:`add_scope_arguments` describe.

    Raises:
        ReleaseScopeError: When the prefix is not allowed.
    """
    return ReleaseScope(options.tag_prefix)
