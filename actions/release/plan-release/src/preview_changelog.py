"""Preview, on an open pull request, the release a labelled merge of it would ship.

The preview runs the release's own code rather than imitating it: the planner
of :mod:`plan_release` and the changelog of :mod:`changelog_file`, on the test
merge GitHub checks out for a ``pull_request`` event (``refs/pull/N/merge``),
whose first parent is the head of the base branch and whose second is the head
of the pull request. The one thing a release knows that an open pull request
does not is that it merged, so the commits the second parent brings in are read
as belonging to the pull request, merged, under the title it has now; every
other commit is read from GitHub as the release reads it. So the preview shows:

- the version, from the last ``<prefix>`` tag, the class of every shipped
  branch and the declared breaks, the pull request's title included;
- the section the release adds to the changelog, which is also the body of its
  GitHub Release: the pull requests merged since the last tag and not released
  yet, and this one, scoped to the paths of the package;
- "no release" when no commit since the last tag touches those paths, or the
  error the release would stop on, word for word, such as a title that is not
  a Conventional Commits header.

It matches the release when both run the same plan-release source. The release
runs the planner release-on-label pins, while the preview runs the one its
caller pins; release-on-label pins v1.11.0, whose planner and changelog give the
version and section this source gives (what changed since only adds an
optional date and :func:`release_history.commit_parents`). The pin moves to the
release that ships this preview in a follow-up.

It is exact for merge-commit and rebase merges, whose commits reach the base
branch as they are. A squash merge ships one commit GitHub writes, and the
release reads breaks and paths from it: the title is its header, so a ``!`` in
the title counts, but a ``feat!:`` commit other than the first becomes a
``* feat!:`` line of its body, which declares nothing, and a ``BREAKING
CHANGE:`` footer counts only when the repository squashes with the "pull
request title and description" setting and the description carries it.

It previews a release that keeps a changelog (``changelog: true`` on
release-on-label). The section is dated today, in UTC, which is provisional:
the release dates it by its merge commit. One run previews one package and
appends it to a Markdown file, so a monorepo runs it once per package and posts
the file as one comment.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Final, NoReturn

from changelog_file import ChangelogError, build_changelog
from plan_release import NothingToRelease, ReleasePlanError, plan_release
from release_history import (
    CommitPullRequests,
    HistoryError,
    PullRequest,
    commit_parents,
    gh_commit_pull_requests,
    range_log,
)
from release_scope import (
    ReleaseScope,
    ReleaseScopeError,
    add_scope_arguments,
    scope_of,
)

_HEADER: Final[str] = (
    "## Release preview\n"
    "\n"
    "> [!NOTE]\n"
    "> A preview, not a release: nothing is tagged or written. Each package below shows\n"
    "> what merging this pull request with the release label would release: the version\n"
    "> and the section added to its changelog, which is also the GitHub Release body.\n"
    "> The pull request is read as merged under the title it has now, so editing the\n"
    "> title updates the preview.\n"
    ">\n"
    "> Computed from the test merge `{merge}` (base `{base}`): a base branch that moved\n"
    "> since then is not in it until the next run on this pull request.\n"
    "\n"
)
_BACKTICKS: Final[re.Pattern[str]] = re.compile(r"`+")
# The rule release-on-label checks its release commit against.
_FULL_SHA: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{40}")


class PreviewError(RuntimeError):
    """Raised when the checkout cannot be previewed as a merge of the pull request."""


@dataclass(frozen=True, slots=True)
class OpenPullRequest:
    """The pull request a preview reads as merged: its number, title and branch."""

    number: int
    title: str
    head_ref: str

    def as_merged(self) -> PullRequest:
        """Return the pull request as the release reads it once merged."""
        return PullRequest(self.number, self.title, self.head_ref, merged=True)


@dataclass(frozen=True, slots=True)
class PullRequestMerge:
    """The test merge of a pull request: its commit, its base and the commits it brings in."""

    sha: str
    base: str
    commits: frozenset[str]

    @classmethod
    def read(cls, repository: Path, merge_sha: str) -> PullRequestMerge:
        """Return the test merge *merge_sha*, read once from git.

        Its commits are the non-merge commits its second parent holds and its
        first, the head of the base branch, does not: the pull request's own,
        without the base commits a merge of the base into the branch brought.

        Raises:
            PreviewError: When *merge_sha* is not a merge of two parents.
            HistoryError: When git cannot read it.
        """
        parents = commit_parents(repository, merge_sha)
        if len(parents) != 2:
            raise PreviewError(
                f"commit {merge_sha} is not a merge of the pull request into its base: "
                "preview the test merge a pull_request event checks out, github.sha"
            )
        commits = range_log(repository, parents[0], merge_sha)
        return cls(merge_sha, parents[0], frozenset(commits))


def with_pull_request_merged(
    read: CommitPullRequests, pull_request: OpenPullRequest, commits: frozenset[str]
) -> CommitPullRequests:
    """Return *read*, except that each of *commits* belongs to *pull_request*, merged.

    What GitHub says of those commits, the open pull request under an older
    title included, is never read: the pull request as it is now wins.
    """
    merged = (pull_request.as_merged(),)

    def reader(sha: str) -> tuple[PullRequest, ...]:
        return merged if sha in commits else read(sha)

    return reader


def _fenced(text: str, info: str) -> str:
    longest = max((len(run) for run in _BACKTICKS.findall(text)), default=0)
    fence = "`" * max(3, longest + 1)
    body = text.rstrip("\n")
    return f"{fence}{info}\n{body}\n{fence}\n"


@dataclass(frozen=True, slots=True)
class ReleasePreview:
    """What a labelled merge would release for one package.

    Build it with :meth:`released`, :meth:`nothing` or :meth:`failed`.

    Attributes:
        tag_prefix: Prefix of the package's release tags.
        base_sha:   Head of the base branch the test merge was computed on.
        version:    Version it would release; empty when it releases nothing
            or fails.
        part:       Version part the shipped branches ask for.
        last_tag:   Release tag the version follows, or None for a first one.
        notes:      The changelog section, with its link, the release would add.
        error:      The error the release would stop on, as it reports it.
        reason:     Why nothing would be released.
        touched:    Whether a commit of the pull request is in the release.
        scoped:     Whether the release is bounded by paths.
    """

    tag_prefix: str
    base_sha: str
    version: str = ""
    part: str = ""
    last_tag: str | None = None
    notes: str = ""
    error: str = ""
    reason: str = ""
    touched: bool = True
    scoped: bool = True

    @classmethod
    def released(
        cls,
        tag_prefix: str,
        base_sha: str,
        *,
        version: str,
        part: str,
        last_tag: str | None,
        notes: str,
        touched: bool = True,
        scoped: bool = True,
    ) -> ReleasePreview:
        """Return the preview of a release of *version*, adding *notes*."""
        return cls(
            tag_prefix, base_sha, version, part, last_tag, notes, touched=touched, scoped=scoped
        )

    @classmethod
    def nothing(cls, tag_prefix: str, base_sha: str, reason: str) -> ReleasePreview:
        """Return the preview of no release, for *reason*."""
        return cls(tag_prefix, base_sha, reason=reason, touched=False)

    @classmethod
    def failed(cls, tag_prefix: str, base_sha: str, error: str) -> ReleasePreview:
        """Return the preview of a release stopping on *error*."""
        return cls(tag_prefix, base_sha, error=error)

    def render(self, changelog_file: str) -> str:
        """Return the package's part of the preview comment, in Markdown."""
        if self.error:
            return (
                f"### `{self.tag_prefix}`: the release would fail\n\n"
                "A labelled merge would stop on this error before writing any tag:\n\n"
                f"{_fenced(self.error, 'text')}"
            )
        if not self.version:
            return (
                f"### `{self.tag_prefix}`: no release\n\n"
                f"A labelled merge would not release `{self.tag_prefix}` ({self.reason}).\n"
            )
        return self._release(changelog_file)

    def _release(self, changelog_file: str) -> str:
        tag = f"{self.tag_prefix}{self.version}"
        after = f"after `{self.last_tag}`" if self.last_tag else "as its first release"
        lines = [
            f"### `{self.tag_prefix}`: `{tag}` ({self.part})\n",
            f"A labelled merge would tag `{tag}` {after} and add this section to "
            f"`{changelog_file}`. The date is today's in UTC and provisional: the release "
            "dates the section by its merge commit.\n",
        ]
        if not self.touched:
            outside = "changes none of its paths" if self.scoped else "brings no commit"
            lines.append(
                f"This pull request {outside}: the section lists what was merged before it "
                "and is not released yet, which its labelled merge releases.\n"
            )
        lines.append(_fenced(self.notes, "markdown"))
        return "\n".join(lines)


def _preview(
    repository: Path,
    merge: PullRequestMerge,
    pull_request: OpenPullRequest,
    read: CommitPullRequests,
    *,
    config: Path,
    scope: ReleaseScope,
    changelog_file: str,
    repository_url: str,
    date: str,
) -> ReleasePreview:
    shipping = with_pull_request_merged(read, pull_request, merge.commits)
    try:
        plan = plan_release(repository, merge.sha, shipping, config=config, scope=scope)
    except NothingToRelease as nothing:
        return ReleasePreview.nothing(scope.tag_prefix, merge.base, str(nothing))
    update = build_changelog(
        repository,
        merge.sha,
        plan.version,
        shipping,
        changelog_file=changelog_file,
        scope=scope,
        repository_url=repository_url,
        date=date,
    )
    return ReleasePreview.released(
        scope.tag_prefix,
        merge.base,
        version=plan.version,
        part=plan.part,
        last_tag=plan.last_tag,
        notes=update.notes,
        touched=any(entry.sha in merge.commits for entry in plan.shipped),
        scoped=bool(scope.paths),
    )


def preview_release(
    repository: Path,
    merge_sha: str,
    pull_request: OpenPullRequest,
    read: CommitPullRequests,
    *,
    config: Path,
    scope: ReleaseScope,
    changelog_file: str,
    repository_url: str,
    date: str,
) -> ReleasePreview:
    """Return what a labelled merge of *pull_request* would release for *scope*.

    Args:
        repository:     Checkout holding the test merge and every tag.
        merge_sha:      The test merge of the pull request into its base.
        pull_request:   The pull request, read as merged under its title now.
        read:           Reader of the pull requests of the other commits.
        config:         TOML file holding the branch rules, as the release reads it.
        scope:          Tag prefix and paths of the package.
        changelog_file: The package's ``CHANGELOG.md``.
        repository_url: URL the changelog links pull requests and tags to.
        date:           Date of the section, ``YYYY-MM-DD``.

    Returns:
        The release, no release, or the error the release would stop on, with
        the same message the release prints.

    Raises:
        PreviewError:   When *merge_sha* is not a merge of two parents.
        HistoryError:   When git or GitHub cannot be read while planning.
        ChangelogError: When they cannot be read while writing the section; its
            ``__cause__`` is the :class:`HistoryError`. Either is no release
            error but a failure to preview, so the step must fail.
    """
    merge = PullRequestMerge.read(repository, merge_sha)
    try:
        return _preview(
            repository,
            merge,
            pull_request,
            read,
            config=config,
            scope=scope,
            changelog_file=changelog_file,
            repository_url=repository_url,
            date=date,
        )
    except ReleasePlanError as error:
        return ReleasePreview.failed(scope.tag_prefix, merge.base, f"release plan failed: {error}")
    except ChangelogError as error:
        if isinstance(error.__cause__, HistoryError):
            raise
        return ReleasePreview.failed(scope.tag_prefix, merge.base, f"changelog failed: {error}")


def preview_header(merge_sha: str, base_sha: str) -> str:
    """Return the header of the comment, naming the test merge and its base by short sha.

    GitHub recomputes the test merge lazily, so a run can preview a base that
    has moved since; the shas make such a stale preview visible.
    """
    return _HEADER.format(merge=merge_sha[:7], base=base_sha[:7])


def append_preview(output: Path, text: str, header: str) -> None:
    """Append *text* to *output*, starting a new file with *header*."""
    existing = output.read_text(encoding="utf-8") if output.is_file() else ""
    separator = "\n" if existing else header
    output.write_text(f"{existing}{separator}{text}", encoding="utf-8")


def _parse_args(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview the release a labelled merge of a pull request would ship."
    )
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--merge-sha", required=True, help="the test merge of the pull request")
    parser.add_argument("--slug", required=True, help="owner/repo the pull requests live in")
    parser.add_argument("--pull-request-number", type=int, required=True)
    parser.add_argument("--pull-request-title", required=True)
    parser.add_argument("--head-ref", required=True, help="branch of the pull request")
    parser.add_argument("--semantic-branch-config", default="pyproject.toml")
    add_scope_arguments(parser)
    parser.add_argument("--changelog-file", required=True)
    parser.add_argument("--output", type=Path, required=True, help="Markdown file to append to")
    parser.add_argument("--date", default=datetime.now(UTC).date().isoformat())
    parser.add_argument(
        "--server-url", default=os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    )
    return parser.parse_args(arguments)


def _fail(message: str) -> NoReturn:
    print(f"release preview failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def main(arguments: Sequence[str] | None = None) -> int:
    """Append the preview to the output and print ``version=`` and ``failed=`` outputs."""
    options = _parse_args(arguments)
    pull_request = OpenPullRequest(
        options.pull_request_number, options.pull_request_title, options.head_ref
    )
    if not _FULL_SHA.fullmatch(options.merge_sha):
        _fail(f"merge sha '{options.merge_sha}' is not a full commit sha")
    try:
        preview = preview_release(
            options.repository,
            options.merge_sha,
            pull_request,
            # The plan and the changelog read the same commits: one request each.
            cache(gh_commit_pull_requests(options.slug)),
            config=Path(options.semantic_branch_config or "pyproject.toml"),
            scope=scope_of(options),
            changelog_file=options.changelog_file,
            repository_url=f"{options.server_url.rstrip('/')}/{options.slug}",
            date=options.date,
        )
    except (ReleaseScopeError, PreviewError, HistoryError, ChangelogError) as error:
        _fail(str(error))
    header = preview_header(options.merge_sha, preview.base_sha)
    append_preview(options.output, preview.render(options.changelog_file), header)
    print(f"version={preview.version}")
    print(f"failed={'true' if preview.error else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
