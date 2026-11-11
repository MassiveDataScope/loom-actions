"""Commit the changelog a release built to its base branch, through the contents API.

The plan builds the file on the head of the base branch, before the tag; the
commit lands once the GitHub Release exists, and the branch may have moved in
between. Each attempt reads the file the branch holds now and carries the
release onto it with :func:`changelog_file.carry_release`, the planner's own
update, so a change made meanwhile is kept and a version the branch already
lists, say by a re-run that committed first, is never added twice.

GitHub answers 409 when the file changed between that read and the write. The
commit is then retried up to three times, pausing 1, 2 and 4 seconds, each time
over the blob read again. Through the API the commit needs no checkout and no
persisted credential, and GitHub signs it.

Two more modes read a ref and never write. ``render`` writes the changelog the
ref should hold, the release carried onto it, to a file; ``verify`` fails
unless the ref already lists the planned section, so a release that never
writes its base branch can check the file a person committed on the pull
request. Both put the planned section in place of one the ref lists, and date
it as follows, so a merge on another UTC day than the render keeps passing:

- ``render`` reuses the ISO 8601 date of the section the ref lists, or takes
  today's, in UTC, when it lists none;
- ``verify`` requires that date to be a valid ``YYYY-MM-DD`` and does not
  compare it; everything else must be the file ``render`` expects, exactly.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final, NoReturn, Protocol
from urllib.parse import quote

from changelog_file import (
    ChangelogDocument,
    ChangelogError,
    carry_release,
    check_changelog_file,
)
from release_history import HistoryError, run

RETRIES: Final[int] = 3
MODES: Final[tuple[str, ...]] = ("commit", "render", "verify")
_STATUS: Final[re.Pattern[str]] = re.compile(r"\(HTTP (\d{3})\)")
_DATED_HEADING: Final[re.Pattern[str]] = re.compile(r"## \[[^\]]+\] - (\d{4}-\d{2}-\d{2})")


class CommitConflict(HistoryError):
    """Raised when the file changed on the branch between its read and the commit."""


@dataclass(frozen=True, slots=True)
class RemoteFile:
    """A file on the base branch: its text, and its blob, None when the branch has none."""

    text: str
    sha: str | None


class Branch(Protocol):
    """The file a release commits, on its base branch."""

    def read(self) -> RemoteFile: ...

    def write(self, text: str, sha: str | None, message: str) -> str: ...


def _status(error: HistoryError) -> str | None:
    found = _STATUS.search(str(error))
    return found[1] if found else None


@dataclass(frozen=True, slots=True)
class ContentsApi:
    """One file of a branch, read and written through ``gh`` and the contents API."""

    slug: str
    path: str
    branch: str

    @property
    def _endpoint(self) -> str:
        return f"repos/{self.slug}/contents/{quote(self.path)}"

    def read(self) -> RemoteFile:
        """Return the file on the branch; a file the branch does not hold is empty.

        Raises:
            HistoryError: When GitHub answers anything but the file or a 404.
        """
        command = ("gh", "api", "--method", "GET", self._endpoint, "-f", f"ref={self.branch}")
        try:
            answer = json.loads(run(command))
        except HistoryError as error:
            if _status(error) == "404":
                return RemoteFile("", None)
            raise
        if answer.get("encoding") != "base64":
            raise HistoryError(f"{self.path} on {self.branch} is not served as base64 content")
        return RemoteFile(base64.b64decode(answer["content"]).decode("utf-8"), answer["sha"])

    def write(self, text: str, sha: str | None, message: str) -> str:
        """Commit *text* over the blob *sha*, or as a new file, and return the commit.

        Raises:
            CommitConflict: When GitHub answers 409: the blob is no longer the file's.
            HistoryError:   When GitHub refuses the commit otherwise.
        """
        body = {
            "message": message,
            "branch": self.branch,
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            **({"sha": sha} if sha else {}),
        }
        command = ("gh", "api", "--method", "PUT", self._endpoint, "--input", "-")
        try:
            return run((*command, "--jq", ".commit.sha"), stdin=json.dumps(body)).strip()
        except HistoryError as error:
            if _status(error) == "409":
                raise CommitConflict(str(error)) from error
            raise


def _attempt(built: str, version: str, branch: Branch, message: str) -> bool:
    remote = branch.read()
    update = carry_release(built, remote.text, version)
    if update.changed:
        branch.write(update.text, remote.sha, message)
    return update.changed


def commit_release(
    built: str,
    version: str,
    branch: Branch,
    message: str,
    *,
    sleep: Callable[[float], object] = time.sleep,
) -> bool:
    """Commit the release of *version* that *built* lists onto *branch*.

    Returns:
        True when this call committed it, False when the branch already listed it.

    Raises:
        CommitConflict: When the branch still conflicts after :data:`RETRIES` retries.
        ChangelogError: When *built* does not list the release, or the branch's
            file does not keep a changelog.
        HistoryError:   When GitHub refuses the read or the commit otherwise.
    """
    for retry in range(RETRIES):
        try:
            return _attempt(built, version, branch, message)
        except CommitConflict:
            sleep(2**retry)
    return _attempt(built, version, branch, message)


def _iso_date(section: str) -> str | None:
    """Return the ``YYYY-MM-DD`` date of *section*'s heading, or None if it holds no valid one."""
    found = _DATED_HEADING.fullmatch(section.partition("\n")[0])
    if found is None:
        return None
    try:
        date.fromisoformat(found[1])
    except ValueError:
        return None
    return found[1]


def _without(document: ChangelogDocument, version: str) -> ChangelogDocument:
    """Return *document* without the section of *version* and its link reference."""
    heading, label = f"## [{version}]", f"[{version}]:".lower()
    body = document.body
    start = next((i for i, line in enumerate(body) if line.startswith(heading)), None)
    if start is None:
        return document
    end = next((i for i in range(start + 1, len(body)) if body[i].startswith("## ")), len(body))
    references = tuple(ref for ref in document.references if not ref.lower().startswith(label))
    return ChangelogDocument((*body[:start], *body[end:]), references)


def render_release(built: str, onto: str, version: str, *, today: str) -> str:
    """Return *onto* holding the release of *version* that the changelog *built* lists.

    A section *onto* already lists for *version* is replaced and keeps its date
    when it is a valid ISO 8601 one; otherwise the section is dated *today*.
    Onto a file that does not list the version, this is what :func:`commit_release`
    writes, but for the date.

    Raises:
        ChangelogError: When *built* does not list *version* with its links, or
            *onto* does not keep a changelog.
    """
    planned = ChangelogDocument.parse(built)
    section, links = planned.section(version), planned.release_links(version)
    if section is None or links is None:
        raise ChangelogError(f"the built changelog does not list {version} with its links")
    current = ChangelogDocument.parse(onto)
    listed = current.section(version)
    dated = (_iso_date(listed) if listed else None) or today
    _, _, entries = section.partition("\n")
    section = f"## [{version}] - {dated}\n{entries}"
    return _without(current, version).with_release(section, links).render()


def verify_release(built: str, onto: str, version: str) -> None:
    """Check that *onto* is exactly the changelog :func:`render_release` expects of it.

    The heading date must be a valid ISO 8601 ``YYYY-MM-DD``; its value is not
    compared, since the expected file reuses it. Everything else is: the
    section and its link, then the whole file, so the position of the section,
    the other link references, line endings and blank lines count too.

    Raises:
        ChangelogError: When *onto* does not list *version*, lists it more than
            once, defines its link outside the references at the bottom, has no
            ISO 8601 date in its heading, or differs from the expected file.
    """
    document = ChangelogDocument.parse(onto)
    listed = document.section(version)
    if listed is None:
        raise ChangelogError(f"the changelog does not list {version}")
    heading, label = f"## [{version}]", f"[{version}]:".lower()
    if (times := sum(line.startswith(heading) for line in document.body)) > 1:
        raise ChangelogError(f"the changelog lists {version} {times} times")
    if any(line.lstrip().lower().startswith(label) for line in document.body):
        raise ChangelogError(
            f"the changelog defines the link of {version} outside the link references "
            "at the bottom"
        )
    dated = _iso_date(listed)
    if dated is None:
        first, _, _ = listed.partition("\n")
        raise ChangelogError(f"{first!r} is not dated with an ISO 8601 date, YYYY-MM-DD")
    expected = render_release(built, onto, version, today=dated)
    if ChangelogDocument.parse(expected).notes(version) != document.notes(version):
        raise ChangelogError(f"the section of {version} differs from the planned one")
    if expected != onto:
        raise ChangelogError(
            "the changelog differs from the expected file outside the section of "
            f"{version}: its position, the other link references, line endings or blank lines"
        )


def _parse_args(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Commit a release's changelog to its branch.")
    parser.add_argument("--slug", required=True, help="owner/repo the branch lives in")
    parser.add_argument("--branch", required=True, help="base branch to commit to")
    parser.add_argument("--changelog-file", required=True, help="CHANGELOG.md, from the root")
    parser.add_argument("--built", type=Path, required=True, help="the file the plan built")
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag-prefix", required=True)
    parser.add_argument(
        "--mode", choices=MODES, default="commit", help="commit it, or render or verify a ref"
    )
    parser.add_argument("--ref", default="", help="ref render and verify read; the branch if empty")
    parser.add_argument("--out", default="", help="file render and verify write the expected to")
    return parser.parse_args(arguments)


def _fail(message: str, mode: str = "commit") -> NoReturn:
    print(f"changelog {mode} failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _render_or_verify(options: argparse.Namespace) -> int:
    """Write the expected file, print ``changed=`` for ``$GITHUB_OUTPUT``, then verify."""
    if options.mode == "render" and not options.out:
        _fail("render needs --out, the file to write the expected changelog to", "render")
    ref = options.ref or options.branch
    try:
        path = str(check_changelog_file(options.changelog_file))
        built = options.built.read_text(encoding="utf-8")
        current = ContentsApi(options.slug, path, ref).read().text
        expected = render_release(built, current, options.version, today=_today())
        if options.out:
            out = Path(options.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(expected, encoding="utf-8")
        print(f"changed={'true' if expected != current else 'false'}")
        if options.mode == "verify":
            verify_release(built, current, options.version)
            print(f"{path!r} on {ref!r} holds the expected changelog", file=sys.stderr)
    except (ChangelogError, HistoryError, OSError) as error:
        _fail(f"{options.changelog_file!r} on {ref!r}: {error}", options.mode)
    return 0


def main(arguments: Sequence[str] | None = None) -> int:
    """Commit the built changelog, and say whether this run committed it.

    With ``--mode render`` or ``--mode verify`` nothing is committed: see the module.
    """
    options = _parse_args(arguments)
    if options.mode != "commit":
        return _render_or_verify(options)
    try:
        path = str(check_changelog_file(options.changelog_file))
        built = options.built.read_text(encoding="utf-8")
        branch = ContentsApi(options.slug, path, options.branch)
        message = f"docs(release): changelog for {options.tag_prefix}{options.version}"
        committed = commit_release(built, options.version, branch, message)
    except (ChangelogError, HistoryError, OSError) as error:
        _fail(str(error))
    if committed:
        print(f"committed {path} to {options.branch}")
    else:
        print(f"{options.branch} already lists the release in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
