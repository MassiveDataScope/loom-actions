"""Tell whether the commits since the last release declare a breaking change.

A release that takes its part from the merged branch name, as loom-actions' own
``release.yml`` does, reads this to raise the part to a major the way
``plan_release`` does. It prints ``breaking=true`` or ``breaking=false``, a line
for ``$GITHUB_OUTPUT``, and names every marked commit on stderr.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from plan_release import ReleasePlanError, breaking_commits
from release_history import HistoryError
from release_scope import ReleaseScopeError, add_scope_arguments, scope_of


def _parse_args(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tell whether the commits since the last release declare a break."
    )
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--merge-sha", required=True)
    add_scope_arguments(parser)
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parse_args(arguments)
    try:
        marked = breaking_commits(options.repository, options.merge_sha, scope_of(options))
    except (ReleaseScopeError, ReleasePlanError, HistoryError) as error:
        print(f"break detection failed: {error}", file=sys.stderr)
        return 1
    for sha in marked:
        print(f"declares a break: {sha}", file=sys.stderr)
    print(f"breaking={'true' if marked else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
