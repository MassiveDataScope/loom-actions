"""Decide which version a labelled merge releases, from the branches it ships."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn

from conventional_commits import declares_break
from release_history import (
    CommitPullRequests,
    HistoryError,
    changes_only_changelogs,
    commit_message,
    gh_commit_pull_requests,
    latest_release_tag,
    merged_pull_requests,
    range_log,
)
from release_scope import (
    DEFAULT_SCOPE,
    ReleaseScope,
    ReleaseScopeError,
    add_release_arguments,
    add_scope_arguments,
    scope_of,
)
from release_tags import DEFAULT_TAG_PREFIX, release_tag_pattern

_PARTS: Final[tuple[str, ...]] = ("major", "minor", "patch")
_DEFAULT_CONFIG: Final[Path] = Path("pyproject.toml")


class ReleasePlanError(RuntimeError):
    """Raised when the release a merge would ship cannot be determined."""


class NothingToRelease(ReleasePlanError):
    """Raised when no commit since the last tag touches the paths of a scoped release.

    A monorepo package's release runs for every labelled merge, so a merge that
    changed only other packages is no anomaly: the release is a no-op, not a
    failure. An unscoped release with nothing to ship still fails.
    """


class IgnoredBranchesOnly(ReleasePlanError):
    """Raised when every branch since the last tag belongs to a class that ships no version.

    The release still fails on it: a labelled merge of such branches is a
    mistake. Its own class only lets a preview tell it from other refusals.
    """


@dataclass(frozen=True, slots=True)
class ShippedPullRequest:
    """One pull request the release ships, and the part its branch asks for."""

    sha: str
    head_ref: str
    part: str | None


@dataclass(frozen=True, slots=True)
class ReleasePlan:
    """The release a labelled merge commit produces."""

    last_tag: str | None
    part: str
    version: str
    shipped: tuple[ShippedPullRequest, ...]

    def render(self) -> str:
        """Return the plan as the lines an operator reads before publishing."""
        lines = [
            f"last tag : {self.last_tag or '(none)'}",
            f"part     : {self.part}",
            f"version  : {self.version}",
            "ships    :",
        ]
        for entry in self.shipped:
            part = entry.part or "-"
            lines.append(f"  {entry.sha[:8]}  {part:<5}  {entry.head_ref}")
        return "\n".join(lines) + "\n"


def branch_rules(repository: Path, config: Path = _DEFAULT_CONFIG) -> Mapping[str, tuple[str, ...]]:
    """Return the branch patterns of every class declared in *config*.

    Args:
        repository: Checkout the release is planned in.
        config:     TOML file holding ``[tool.semantic_branch]``, relative to
            *repository* unless absolute.

    Raises:
        ReleasePlanError: When *config* does not exist.
    """
    path = repository / config
    if not path.is_file():
        raise ReleasePlanError(
            f"semantic branch config '{config}' not found in {repository}: "
            "pass the file that declares [tool.semantic_branch]"
        )
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    section = data.get("tool", {}).get("semantic_branch", {})
    return {
        key: tuple(section.get(key, ())) for key in ("major", "minor", "patch", "release_ignore")
    }


def classify_branch(head_ref: str, rules: Mapping[str, tuple[str, ...]]) -> str | None:
    """Return the part *head_ref* asks for, or None when its class ships nothing.

    Raises:
        ReleasePlanError: When no class in the rules matches *head_ref*.
    """
    for part in _PARTS:
        if any(re.fullmatch(pattern, head_ref) for pattern in rules.get(part, ())):
            return part
    if any(re.fullmatch(pattern, head_ref) for pattern in rules.get("release_ignore", ())):
        return None
    raise ReleasePlanError(
        f"branch '{head_ref}' matches no class in [tool.semantic_branch]: "
        "add its prefix there or rename the branch"
    )


def highest_part(parts: Iterable[str | None]) -> str | None:
    """Return the largest part among *parts*, or None when every one ships nothing."""
    present = {part for part in parts if part is not None}
    for part in _PARTS:
        if part in present:
            return part
    return None


def next_version(last_tag: str | None, part: str, prefix: str = DEFAULT_TAG_PREFIX) -> str:
    """Return the version that raising *part* from *last_tag*, a *prefix* tag, produces.

    Raises:
        TagPrefixError:   When *prefix* is not allowed.
        ReleasePlanError: When *last_tag* is not a *prefix* release tag.
    """
    if last_tag is None:
        return {"major": "1.0.0", "minor": "0.1.0", "patch": "0.0.1"}[part]
    matched = release_tag_pattern(prefix).match(last_tag)
    if matched is None:
        raise ReleasePlanError(f"tag '{last_tag}' is not a release tag")
    major, minor, patch = (int(group) for group in matched.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def breaking_commits(
    repository: Path, merge_sha: str, scope: ReleaseScope = DEFAULT_SCOPE
) -> tuple[str, ...]:
    """Return the commits since the last release of *scope* that declare a break.

    The range is the one :func:`plan_release` ships: from the highest release
    tag before *merge_sha* to *merge_sha*, or the whole history without a tag.

    Raises:
        HistoryError: When git cannot read the range.
    """
    last_tag = latest_release_tag(repository, merge_sha, scope.tag_prefix)
    return tuple(
        sha
        for sha in range_log(repository, last_tag, merge_sha, scope)
        if declares_break(commit_message(repository, sha))
    )


def _shipped_by(
    repository: Path,
    sha: str,
    commit_pull_requests: CommitPullRequests,
    rules: Mapping[str, tuple[str, ...]],
) -> list[ShippedPullRequest]:
    pull_requests = merged_pull_requests(commit_pull_requests, sha)
    if not pull_requests and changes_only_changelogs(repository, sha):
        return []
    if not pull_requests:
        raise ReleasePlanError(
            f"commit {sha} belongs to no merged pull request: a direct push cannot be classified"
        )
    marked = declares_break(commit_message(repository, sha))
    shipped = []
    for pull_request in pull_requests:
        branch_part = classify_branch(pull_request.head_ref, rules)
        breaking = marked or declares_break(pull_request.title)
        shipped.append(
            ShippedPullRequest(sha, pull_request.head_ref, "major" if breaking else branch_part)
        )
    return shipped


def _nothing_to_release(scope: ReleaseScope, last_tag: str | None) -> ReleasePlanError:
    message = scope.no_commits(last_tag)
    return NothingToRelease(message) if scope.paths else ReleasePlanError(message)


def plan_release(
    repository: Path,
    merge_sha: str,
    commit_pull_requests: CommitPullRequests,
    *,
    config: Path = _DEFAULT_CONFIG,
    scope: ReleaseScope = DEFAULT_SCOPE,
) -> ReleasePlan:
    """Return the release *merge_sha* ships, from the branches merged since the last tag.

    Only the commits of *scope* count: with paths, the commits that change one
    of them, for the part, the declared breaks and the shipped list alike.

    The part is the highest one any shipped branch asks for, so a batch holding a
    feature never ships as a patch. A commit declaring a break — a ``!`` in its
    subject or a ``BREAKING CHANGE:`` footer — or a pull request whose title
    carries the ``!`` asks for a major whatever its branch asks for, so the
    version agrees with the ``**BREAKING:**`` entries of the changelog.

    A commit with no merged pull request, or one whose branch matches no
    declared class, refuses the release instead of lowering it; the one
    exception is a commit that changes only ``CHANGELOG.md`` files, which a
    release keeping a changelog pushes and which ships no version.

    Args:
        repository:           Checkout to read tags and commits from.
        merge_sha:            Commit the release is cut from.
        commit_pull_requests: Reader of the pull requests a commit belongs to;
            only the merged ones count.
        config:               TOML file holding the branch rules, relative to
            *repository* unless absolute.
        scope:                Release line the release belongs to: the tags it
            reads, ``v`` unless a monorepo package releases under its own.

    Returns:
        The planned release.

    Raises:
        NothingToRelease: When *scope* has paths and no commit since the last
            tag, but its own changelog commits, touches them.
        IgnoredBranchesOnly: When every branch in the range belongs to a class
            that ships no version.
        ReleasePlanError: When the range holds no commit of *scope*, *config*
            does not exist, a commit has no merged pull request, or a branch is
            unclassified.
        HistoryError:     When git or GitHub cannot read the range.
    """
    last_tag = latest_release_tag(repository, merge_sha, scope.tag_prefix)
    commits = range_log(repository, last_tag, merge_sha, scope)
    if not commits:
        raise _nothing_to_release(scope, last_tag)

    rules = branch_rules(repository, config)
    shipped: list[ShippedPullRequest] = []
    for sha in commits:
        shipped.extend(_shipped_by(repository, sha, commit_pull_requests, rules))
    if not shipped and scope.paths:
        # Only the package's own changelog commits, which ship no version.
        raise NothingToRelease(scope.no_commits(last_tag))

    part = highest_part(entry.part for entry in shipped)
    if part is None:
        raise IgnoredBranchesOnly(
            "nothing to release: every branch since "
            f"{last_tag or 'the start of history'} belongs to a class that ships no version"
        )
    return ReleasePlan(
        last_tag, part, next_version(last_tag, part, scope.tag_prefix), tuple(shipped)
    )


def _parse_args(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan the release a labelled merge ships.")
    add_release_arguments(parser)
    parser.add_argument(
        "--semantic-branch-config",
        default=str(_DEFAULT_CONFIG),
        help="TOML file declaring [tool.semantic_branch], relative to --repository; "
        "empty means pyproject.toml",
    )
    add_scope_arguments(parser)
    parser.add_argument(
        "--format",
        choices=("text", "github"),
        default="text",
        help="text prints the plan for an operator; github writes version and part outputs, "
        "both empty when a scoped release has nothing to ship",
    )
    return parser.parse_args(arguments)


def _fail(message: str) -> NoReturn:
    print(f"release plan failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parse_args(arguments)
    try:
        plan = plan_release(
            options.repository,
            options.merge_sha,
            gh_commit_pull_requests(options.slug),
            config=Path(options.semantic_branch_config or _DEFAULT_CONFIG),
            scope=scope_of(options),
        )
    except NothingToRelease as nothing:
        print(json.dumps({"version": "", "part": ""}) if options.format == "github" else nothing)
        return 0
    except (ReleaseScopeError, ReleasePlanError, HistoryError) as error:
        _fail(str(error))
    if options.format == "github":
        print(json.dumps({"version": plan.version, "part": plan.part}))
    else:
        print(plan.render(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
