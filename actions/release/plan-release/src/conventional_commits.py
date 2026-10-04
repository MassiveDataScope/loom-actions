"""Parse a commit message or a pull request title as Conventional Commits 1.0.0.

The planner raises a declared break to a major and the changelog lists it as
``**BREAKING:**`` through this one parser, so the version and the changelog can
never disagree on a break. It follows https://www.conventionalcommits.org/en/v1.0.0/:

- the header is a type, an optional scope in parentheses, an optional ``!``
  and a colon followed by a space and the description (items 1 to 5);
- a scope is a noun (item 4), so an empty ``()`` makes no header at all and
  ``feat()!: x`` declares nothing, the way ``feat:x`` does not;
- a break is the ``!`` (item 13), or a ``BREAKING CHANGE: <description>`` footer,
  ``BREAKING-CHANGE`` being its synonym (items 11, 12 and 16), and only in a
  message whose header is valid;
- the type is not case sensitive; the footer token is upper case (item 15).

Which types exist is not the parser's call: any word is a type here, and the
changelog decides which ones it has a section for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

_HEADER: Final[re.Pattern[str]] = re.compile(
    r"^(?P<type>[A-Za-z]+)(?:\((?P<scope>[^()\s][^()]*)\))?(?P<bang>!)?: (?P<description>\S.*)$"
)
_BREAKING_FOOTER: Final[re.Pattern[str]] = re.compile(r"^BREAKING[ -]CHANGE: \S", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class ConventionalCommit:
    """The parts of a Conventional Commits message a release reads.

    Attributes:
        type:        The type, lower case.
        scope:       The scope without its parentheses, or None.
        breaking:    Whether the header's ``!`` or a footer declares a break.
        description: The description that follows ``: ``.
    """

    type: str
    scope: str | None
    breaking: bool
    description: str

    @classmethod
    def parse(cls, message: str) -> ConventionalCommit | None:
        """Return the commit *message* describes, or None when its header is not one.

        *message* is a full commit message, or a pull request title alone.
        """
        subject, _, body = message.partition("\n")
        matched = _HEADER.match(subject.strip())
        if matched is None:
            return None
        breaking = matched["bang"] is not None or _BREAKING_FOOTER.search(body) is not None
        return cls(matched["type"].lower(), matched["scope"], breaking, matched["description"])


def declares_break(message: str) -> bool:
    """Return whether *message* is a Conventional Commit that declares a break."""
    parsed = ConventionalCommit.parse(message)
    return parsed is not None and parsed.breaking
