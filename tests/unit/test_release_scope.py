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
