"""Unit tests for the scope of a release line: the tags it reads."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "actions" / "release" / "plan-release" / "src"))

from release_scope import (  # noqa: E402
    DEFAULT_SCOPE,
    ReleaseScope,
    ReleaseScopeError,
    add_scope_arguments,
    scope_of,
)


def _parse(*arguments: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_scope_arguments(parser)
    return parser.parse_args(arguments)


class TestTagPrefix:
    def test_the_default_scope_reads_the_v_tags(self) -> None:
        assert DEFAULT_SCOPE == ReleaseScope("v")

    def test_a_safe_prefix_is_kept(self) -> None:
        assert ReleaseScope("control-plane/v").tag_prefix == "control-plane/v"

    @pytest.mark.parametrize("prefix", ["", "-v", "v;id", "a..b"])
    def test_an_unsafe_prefix_is_refused_with_the_planners_message(self, prefix: str) -> None:
        with pytest.raises(ReleaseScopeError, match=f"tag prefix '{prefix}' is not allowed"):
            ReleaseScope(prefix)


class TestCommandLine:
    def test_the_prefix_is_v_unless_given(self) -> None:
        assert scope_of(_parse()) == DEFAULT_SCOPE
        assert scope_of(_parse("--tag-prefix=api-v")) == ReleaseScope("api-v")

    def test_an_unsafe_prefix_is_refused_once_read(self) -> None:
        options = _parse("--tag-prefix=-v")

        with pytest.raises(ReleaseScopeError, match="is not allowed"):
            scope_of(options)


class TestPaths:
    def test_no_paths_is_the_whole_repository(self) -> None:
        assert ReleaseScope.parse("v", "") == DEFAULT_SCOPE
        assert ReleaseScope.parse("v", " \n , \n") == DEFAULT_SCOPE

    def test_paths_are_split_on_newlines_and_commas_and_normalised(self) -> None:
        scope = ReleaseScope.parse("app-a/v", "apps/app-a/\n ./uv.lock , apps/app-a\n")

        assert scope == ReleaseScope("app-a/v", ("apps/app-a", "uv.lock"))

    @pytest.mark.parametrize(
        "path", ["/etc", "../x", "apps/../../x", "-p", "--all", ":(exclude)apps", "apps\\a"]
    )
    def test_a_path_outside_the_checkout_or_read_as_an_option_is_refused(self, path: str) -> None:
        with pytest.raises(ReleaseScopeError, match="is not allowed"):
            ReleaseScope.parse("v", f"apps/app-a\n{path}")
        with pytest.raises(ReleaseScopeError, match="is not allowed"):
            ReleaseScope("v", (path,))

    def test_git_log_is_limited_only_when_paths_are_given(self) -> None:
        assert DEFAULT_SCOPE.log_limits == ()
        assert ReleaseScope("v", ("apps/a", "uv.lock")).log_limits == (
            "--full-history",
            "--",
            "apps/a",
            "uv.lock",
        )

    def test_nothing_to_release_names_the_paths_only_when_scoped(self) -> None:
        assert DEFAULT_SCOPE.no_commits("v1.0.0") == "nothing to release: no commits since v1.0.0"
        assert ReleaseScope("a/v", ("apps/a", "uv.lock")).no_commits(None) == (
            "nothing to release: no commits touching apps/a, uv.lock since the start of history"
        )


class TestPathsOnTheCommandLine:
    def test_paths_come_from_the_option(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RELEASE_PATHS", raising=False)

        assert scope_of(_parse("--paths", "apps/a,uv.lock")).paths == ("apps/a", "uv.lock")
        assert scope_of(_parse()).paths == ()

    def test_paths_default_to_the_environment_a_composite_sets(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("RELEASE_PATHS", "apps/a\nuv.lock\n")

        assert scope_of(_parse()).paths == ("apps/a", "uv.lock")
        assert scope_of(_parse("--paths", "")).paths == ()

    def test_an_unsafe_path_is_refused_once_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RELEASE_PATHS", "../secrets")

        with pytest.raises(ReleaseScopeError, match="path '../secrets' is not allowed"):
            scope_of(_parse())
