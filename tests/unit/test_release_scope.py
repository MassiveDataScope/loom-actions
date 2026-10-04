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
    add_release_arguments,
    add_scope_arguments,
    add_server_url_argument,
    repository_url,
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

    def test_a_scope_built_directly_is_normalised_too(self) -> None:
        scope = ReleaseScope("app-a/v", ("./apps/app-a/", "apps/app-a", "uv.lock", "apps//app-a"))

        assert scope.paths == ("apps/app-a", "uv.lock")
        assert scope == ReleaseScope.parse("app-a/v", "apps/app-a,uv.lock")

    def test_an_empty_path_is_refused_not_read_as_the_root(self) -> None:
        with pytest.raises(ReleaseScopeError, match="path '' is not allowed"):
            ReleaseScope("v", ("",))

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


class TestReleaseArguments:
    """The arguments every release script takes, declared once for all of them."""

    def _parse(self, *arguments: str) -> argparse.Namespace:
        parser = argparse.ArgumentParser()
        add_release_arguments(parser)
        add_server_url_argument(parser)
        return parser.parse_args(arguments)

    def test_the_commit_and_the_repository_are_read(self, tmp_path: Path) -> None:
        options = self._parse(f"--repository={tmp_path}", "--merge-sha=abc", "--slug=acme/repo")

        assert (options.repository, options.merge_sha, options.slug) == (
            tmp_path,
            "abc",
            "acme/repo",
        )

    def test_the_repository_defaults_to_the_working_directory(self) -> None:
        assert self._parse("--merge-sha=abc", "--slug=a/b").repository == Path.cwd()

    @pytest.mark.parametrize("missing", ["--merge-sha=abc", "--slug=acme/repo"])
    def test_the_commit_and_the_slug_are_required(self, missing: str) -> None:
        given = [a for a in ("--merge-sha=abc", "--slug=acme/repo") if a != missing]
        with pytest.raises(SystemExit):
            self._parse(*given)

    def test_the_repository_url_joins_the_server_and_the_slug(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GITHUB_SERVER_URL", raising=False)
        assert repository_url(self._parse("--merge-sha=a", "--slug=acme/repo")) == (
            "https://github.com/acme/repo"
        )
        given = self._parse("--merge-sha=a", "--slug=acme/repo", "--server-url=https://ghe.io/")
        assert repository_url(given) == "https://ghe.io/acme/repo"
