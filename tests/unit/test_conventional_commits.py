"""Unit tests for the one Conventional Commits 1.0.0 parser the planner and the changelog share.

https://www.conventionalcommits.org/en/v1.0.0/#specification, the items pinned:

1. a type, "followed by the OPTIONAL scope, OPTIONAL !, and REQUIRED terminal
   colon and space";
4. a scope "MUST consist of a noun ... surrounded by parenthesis": ``()`` holds
   no noun, so ``feat()!: x`` is not a header and declares nothing;
11./12./13. a break is a ``!`` right before the ``:``, or a footer
   ``BREAKING CHANGE: <description>`` (``BREAKING-CHANGE`` is a synonym);
15. nothing is case sensitive except ``BREAKING CHANGE``, which is upper case.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "actions" / "release" / "plan-release" / "src"))

from conventional_commits import ConventionalCommit, declares_break  # noqa: E402


class TestParse:
    def test_reads_the_type_the_scope_and_the_description(self) -> None:
        assert ConventionalCommit.parse("feat(api): add the endpoint") == ConventionalCommit(
            "feat", "api", False, "add the endpoint"
        )

    def test_the_scope_is_optional_and_the_type_is_not_case_sensitive(self) -> None:
        assert ConventionalCommit.parse("FIX: repair it") == ConventionalCommit(
            "fix", None, False, "repair it"
        )

    @pytest.mark.parametrize(
        "message",
        [
            "feat!: drop it",
            "feat(api)!: drop it",
            "fix: keep it\n\nBREAKING CHANGE: the field is gone",
            "fix: keep it\n\nBREAKING-CHANGE: the field is gone",
        ],
    )
    def test_reads_every_spelling_of_the_break(self, message: str) -> None:
        parsed = ConventionalCommit.parse(message)

        assert parsed is not None
        assert parsed.breaking is True
        assert declares_break(message) is True

    @pytest.mark.parametrize(
        "message",
        [
            "Add the thing",
            "feat:missing space",
            "feat(): empty scope",
            "feat()!: empty scope with a break",
            "feat!:missing space",
            "feat add it",
            "feat: ",
            "",
        ],
    )
    def test_anything_else_is_not_a_header(self, message: str) -> None:
        assert ConventionalCommit.parse(message) is None
        assert declares_break(message) is False


class TestDeclaresBreak:
    @pytest.mark.parametrize(
        "message",
        [
            "feat: add it",
            "fix(api): repair it",
            "docs: say that this is a BREAKING CHANGE for consumers",
            "feat: the ! belongs to the prose, not to the type",
            "fix: repair the parser!: only the type may carry the marker",
            "fix: keep it\n\nbreaking change: the token is upper case",
            "fix: keep it\n\nBREAKING CHANGE:no space, no footer",
            "Update it\n\nBREAKING CHANGE: a footer of a message that is no commit header",
        ],
    )
    def test_does_not_read_a_break_where_there_is_none(self, message: str) -> None:
        assert declares_break(message) is False
