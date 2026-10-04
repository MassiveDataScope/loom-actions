"""Read what a release ships: its range of commits, their tags and their pull requests.

The planner, the changelog, the notes and the break detection read the history
through this module only, so they can never disagree on the last release, the
commits since it or the pull requests those commits came from.

Every git command runs with ``GIT_LITERAL_PATHSPECS=1``: a path of a release
scope names a file or a directory, never a glob nor pathspec magic.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from release_scope import DEFAULT_SCOPE, ReleaseScope
from release_tags import DEFAULT_TAG_PREFIX, release_tag_glob, release_tag_pattern

# The file name Keep a Changelog 1.1.0 gives the changelog, and the only one a
# release commits with no pull request.
CHANGELOG_NAME: Final[str] = "CHANGELOG.md"


class HistoryError(RuntimeError):
    """Raised when git or GitHub cannot tell what a release ships."""


@dataclass(frozen=True, slots=True)
class PullRequest:
    """A pull request a commit belongs to, as GitHub lists it."""

    number: int
    title: str
    head_ref: str
    merged: bool


CommitPullRequests = Callable[[str], tuple[PullRequest, ...]]


def run(command: Sequence[str], stdin: str | None = None) -> str:
    """Return the standard output of *command*, given *stdin*, with literal git pathspecs.

    Raises:
        HistoryError: When the command cannot start or exits with an error; the
            message holds its standard error.
    """
    environment = {**os.environ, "GIT_LITERAL_PATHSPECS": "1"}
    try:
        completed = subprocess.run(
            command, input=stdin, check=True, capture_output=True, text=True, env=environment
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or str(error)
        raise HistoryError(f"{' '.join(command)} failed: {detail.strip()}") from error
    return completed.stdout


def _git(repository: Path, *arguments: str) -> str:
    return run(("git", "-C", str(repository), *arguments))


def _commit_of(repository: Path, revision: str) -> str:
    return _git(repository, "rev-parse", "--verify", f"{revision}^{{commit}}").strip()


def latest_release_tag(
    repository: Path, revision: str, prefix: str = DEFAULT_TAG_PREFIX
) -> str | None:
    """Return the highest *prefix* release tag before *revision*, ignoring its own tags.

    A release re-run for a commit that is already tagged must plan the same
    version and write the same notes again, so a tag pointing at *revision* is
    not a release that preceded it. Tags with another prefix belong to another
    release line, such as another package of a monorepo, and are not read.

    Raises:
        TagPrefixError: When *prefix* is not allowed.
        HistoryError:   When git cannot read the tags.
    """
    pattern = release_tag_pattern(prefix)
    target = _commit_of(repository, revision)
    output = _git(
        repository,
        "tag",
        "--list",
        release_tag_glob(prefix),
        "--merged",
        target,
        "--sort=-v:refname",
    )
    for line in output.splitlines():
        candidate = line.strip()
        if pattern.match(candidate) and _commit_of(repository, candidate) != target:
            return candidate
    return None


def range_log(
    repository: Path,
    last_tag: str | None,
    revision: str,
    scope: ReleaseScope = DEFAULT_SCOPE,
    pretty: str = "%H",
) -> tuple[str, ...]:
    """Return one *pretty* line per non-merge commit of *scope* since *last_tag*, newest first.

    Without *last_tag* the range is the whole history of *revision*.
    """
    revision_range = f"{last_tag}..{revision}" if last_tag else revision
    output = _git(
        repository, "log", "--no-merges", f"--pretty={pretty}", revision_range, *scope.log_limits
    )
    return tuple(line.strip() for line in output.splitlines() if line.strip())


def commit_message(repository: Path, sha: str) -> str:
    """Return the full message of *sha*."""
    return _git(repository, "log", "-1", "--pretty=%B", sha)


def commit_parents(repository: Path, revision: str) -> tuple[str, ...]:
    """Return the parents of *revision*, in order: two for a merge, none for a root.

    Raises:
        HistoryError: When *revision* names no commit.
    """
    line = _git(repository, "rev-list", "--parents", "-n", "1", f"{revision}^{{commit}}")
    return tuple(line.split()[1:])


def commit_timestamp(repository: Path, sha: str) -> int:
    """Return the committer time of *sha*, in seconds since the epoch."""
    return int(_git(repository, "log", "-1", "--pretty=%ct", sha).strip())


def changes_only_changelogs(repository: Path, sha: str) -> bool:
    """Return whether every path *sha* changes is a ``CHANGELOG.md`` file.

    A release that keeps a changelog commits it to the base branch with no pull
    request, after its tag, so the next release reads that commit. It ships no
    version and is no change, so the planner and the changelog pass over it.
    """
    output = _git(
        repository, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "--root", sha
    )
    paths = [path for path in output.split("\0") if path]
    return bool(paths) and all(PurePosixPath(path).name == CHANGELOG_NAME for path in paths)


def gh_commit_pull_requests(slug: str) -> CommitPullRequests:
    """Return a reader of every pull request of *slug* that holds a commit."""

    def read(sha: str) -> tuple[PullRequest, ...]:
        output = run(
            (
                "gh",
                "api",
                f"repos/{slug}/commits/{sha}/pulls",
                "--jq",
                "[.[] | {number, title, head_ref: .head.ref, merged: (.merged_at != null)}]",
            )
        )
        return tuple(
            PullRequest(
                int(item["number"]), str(item["title"]), str(item["head_ref"]), item["merged"]
            )
            for item in json.loads(output)
        )

    return read


def merged_pull_requests(read: CommitPullRequests, sha: str) -> tuple[PullRequest, ...]:
    """Return the merged pull requests *sha* came from.

    A pull request closed without merging, or still open, can hold the same
    commit; it shipped nothing, so neither the plan nor the changelog reads it.
    """
    return tuple(pull_request for pull_request in read(sha) if pull_request.merged)
