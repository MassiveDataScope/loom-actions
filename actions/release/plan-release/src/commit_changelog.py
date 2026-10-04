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
from pathlib import Path
from typing import Final, NoReturn, Protocol
from urllib.parse import quote

from changelog_file import ChangelogError, carry_release, check_changelog_file
from release_history import HistoryError, run

RETRIES: Final[int] = 3
_STATUS: Final[re.Pattern[str]] = re.compile(r"\(HTTP (\d{3})\)")


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


def _parse_args(arguments: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Commit a release's changelog to its branch.")
    parser.add_argument("--slug", required=True, help="owner/repo the branch lives in")
    parser.add_argument("--branch", required=True, help="base branch to commit to")
    parser.add_argument("--changelog-file", required=True, help="CHANGELOG.md, from the root")
    parser.add_argument("--built", type=Path, required=True, help="the file the plan built")
    parser.add_argument("--version", required=True)
    parser.add_argument("--tag-prefix", required=True)
    return parser.parse_args(arguments)


def _fail(message: str) -> NoReturn:
    print(f"changelog commit failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def main(arguments: Sequence[str] | None = None) -> int:
    """Commit the built changelog, and say whether this run committed it."""
    options = _parse_args(arguments)
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
