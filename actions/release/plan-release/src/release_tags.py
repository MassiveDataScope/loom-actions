"""Name the release tags of one release line, ``v1.2.3`` unless a prefix says otherwise.

A monorepo whose packages release on their own gives each one a prefix, such as
``api-v``, so a package reads and writes ``api-v1.2.3`` and never another
package's tags.
"""

from __future__ import annotations

import re
from typing import Final

DEFAULT_TAG_PREFIX: Final[str] = "v"
_ALLOWED: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
# Sequences git refuses in a ref name even though each character is allowed.
_REFUSED: Final[tuple[str, ...]] = ("..", "//", "/.", ".lock/")


class TagPrefixError(ValueError):
    """Raised when a tag prefix could not name a git tag safely."""


def check_tag_prefix(prefix: str) -> str:
    """Return *prefix* when it is a safe start of a git tag name.

    It starts with a letter or a digit and holds only letters, digits, ``.``,
    ``_``, ``-`` and ``/``, without a sequence git refuses in a ref name.

    Raises:
        TagPrefixError: When *prefix* is empty or holds anything else.
    """
    if _ALLOWED.fullmatch(prefix) and not any(part in prefix for part in _REFUSED):
        return prefix
    raise TagPrefixError(
        f"tag prefix '{prefix}' is not allowed: start with a letter or a digit and use "
        "only letters, digits, '.', '_', '-' and '/'"
    )


def release_tag_glob(prefix: str = DEFAULT_TAG_PREFIX) -> str:
    """Return the ``git tag --list`` pattern of the release tags carrying *prefix*."""
    return f"{check_tag_prefix(prefix)}[0-9]*.[0-9]*.[0-9]*"


def release_tag_pattern(prefix: str = DEFAULT_TAG_PREFIX) -> re.Pattern[str]:
    """Return the expression a release tag carrying *prefix* matches in full.

    Its groups are the major, minor and patch numbers.
    """
    return re.compile(rf"^{re.escape(check_tag_prefix(prefix))}(\d+)\.(\d+)\.(\d+)$")
