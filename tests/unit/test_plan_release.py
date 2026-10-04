"""Unit tests for the release a labelled merge ships."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "actions" / "release" / "plan-release" / "src"))

import plan_release as plan_release_module  # noqa: E402
from plan_release import (  # noqa: E402
    ReleasePlanError,
    classify_branch,
    declares_break,
    highest_part,
    main,
    next_version,
    plan_release,
)
from release_scope import ReleaseScope, ReleaseScopeError  # noqa: E402
from release_tags import (  # noqa: E402
    TagPrefixError,
    check_tag_prefix,
    release_tag_glob,
    release_tag_pattern,
)

_RULES = {
    "major": ("breaking/.*",),
    "minor": ("feature/.*", "feat/.*", "multifeature/.*"),
    "patch": ("hotfix/.*", "fix/.*", "refactor/.*", "perf/.*"),
    "release_ignore": ("wip/.*", "docs/.*", "chore/.*", "ci/.*", "test/.*", "build/.*"),
}


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path, rules: str) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=master")
    _git(repository, "config", "user.email", "release@example.com")
    _git(repository, "config", "user.name", "Release")
    (repository / "pyproject.toml").write_text(rules, encoding="utf-8")
    _git(repository, "add", "pyproject.toml")
    _git(repository, "commit", "-m", "chore: initial")
    return repository


def _rules_toml(rules: Mapping[str, tuple[str, ...]] = _RULES) -> str:
    lines = ["[tool.semantic_branch]"]
    for key, patterns in rules.items():
        rendered = ", ".join(f'"{pattern}"' for pattern in patterns)
        lines.append(f"{key} = [{rendered}]")
    return "\n".join(lines) + "\n"


def _commit(repository: Path, message: str) -> str:
    (repository / message.replace(":", "_").replace(" ", "_")).write_text("x", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", message)
    return _git(repository, "rev-parse", "HEAD")


class TestClassifyBranch:
    def test_reads_the_part_a_prefix_asks_for(self) -> None:
        assert classify_branch("feat/x", _RULES) == "minor"
        assert classify_branch("fix/x", _RULES) == "patch"
        assert classify_branch("breaking/x", _RULES) == "major"

    def test_an_ignore_class_ships_nothing_and_is_not_an_anomaly(self) -> None:
        assert classify_branch("ci/x", _RULES) is None
        assert classify_branch("docs/x", _RULES) is None

    def test_an_unknown_prefix_refuses_instead_of_guessing(self) -> None:
        with pytest.raises(ReleasePlanError, match="matches no class"):
            classify_branch("spike/x", _RULES)


class TestDeclaresBreak:
    @pytest.mark.parametrize(
        "message",
        [
            "feat!: drop it",
            "feat(api)!: drop it",
            "fix: keep it\n\nBREAKING CHANGE: the field is gone",
            "fix: keep it\n\nBREAKING-CHANGE: the field is gone",
        ],
    )
    def test_reads_every_spelling_of_the_marker(self, message: str) -> None:
        assert declares_break(message) is True

    @pytest.mark.parametrize(
        "message",
        [
            "feat: add it",
            "fix(api): repair it",
            "docs: say that this is a BREAKING CHANGE for consumers",
            "feat: the ! belongs to the prose, not to the type",
            "fix: repair the parser!: only the type may carry the marker",
        ],
    )
    def test_does_not_read_a_break_where_there_is_none(self, message: str) -> None:
        assert declares_break(message) is False


class TestHighestPart:
    def test_a_feature_in_the_batch_wins_over_every_fix(self) -> None:
        assert highest_part(["patch", "minor", "patch", None]) == "minor"

    def test_a_breaking_change_wins_over_a_feature(self) -> None:
        assert highest_part(["minor", "major", "patch"]) == "major"

    def test_returns_none_when_nothing_ships_a_version(self) -> None:
        assert highest_part([None, None]) is None


class TestNextVersion:
    @pytest.mark.parametrize(
        ("last_tag", "part", "expected"),
        [
            ("v1.10.0", "patch", "1.10.1"),
            ("v1.10.0", "minor", "1.11.0"),
            ("v1.10.0", "major", "2.0.0"),
            ("v1.9.9", "patch", "1.9.10"),
        ],
    )
    def test_raises_only_the_requested_part(self, last_tag: str, part: str, expected: str) -> None:
        assert next_version(last_tag, part) == expected

    def test_starts_from_zero_without_a_tag(self) -> None:
        assert next_version(None, "minor") == "0.1.0"


class TestPlanRelease:
    def test_a_batch_holding_a_feature_ships_a_minor(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "v1.10.0")
        first = _commit(repository, "fix: one")
        second = _commit(repository, "feat: two")
        third = _commit(repository, "ci: three")
        refs = {first: ("fix/one",), second: ("feat/two",), third: ("ci/three",)}

        plan = plan_release(repository, third, lambda sha: refs[sha])

        assert (plan.last_tag, plan.part, plan.version) == ("v1.10.0", "minor", "1.11.0")
        assert len(plan.shipped) == 3

    def test_an_unmarked_batch_takes_the_part_its_branches_ask_for(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "v1.10.0")
        feature = _commit(repository, "feat: one")
        fix = _commit(repository, "fix: two")
        refs = {feature: ("feat/one",), fix: ("fix/two",)}

        plan = plan_release(repository, fix, lambda sha: refs[sha])

        assert plan.version == "1.11.0"

    def test_a_marked_commit_ships_a_major_whatever_its_branch_asks_for(
        self, tmp_path: Path
    ) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "v1.10.0")
        marked = _commit(repository, "feat(api)!: rename the field")
        refs = {marked: ("feat/rename",)}

        plan = plan_release(repository, marked, lambda sha: refs[sha])

        assert (plan.part, plan.version) == ("major", "2.0.0")

    def test_a_footer_declaring_the_break_ships_a_major_too(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "v1.10.0")
        marked = _commit(repository, "fix: drop the parameter\n\nBREAKING CHANGE: it is gone")
        refs = {marked: ("fix/drop",)}

        plan = plan_release(repository, marked, lambda sha: refs[sha])

        assert (plan.part, plan.version) == ("major", "2.0.0")

    def test_a_marked_commit_on_a_branch_that_ships_nothing_still_ships_a_major(
        self, tmp_path: Path
    ) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "v1.10.0")
        marked = _commit(repository, "ci!: drop the published output")
        refs = {marked: ("ci/drop",)}

        plan = plan_release(repository, marked, lambda sha: refs[sha])

        assert (plan.part, plan.version) == ("major", "2.0.0")

    def test_a_range_ending_before_a_feature_leaves_it_for_the_next_release(
        self, tmp_path: Path
    ) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "v1.10.0")
        marked = _commit(repository, "fix: one")
        later = _commit(repository, "feat: two")
        refs = {marked: ("fix/one",), later: ("feat/two",)}

        plan = plan_release(repository, marked, lambda sha: refs[sha])

        assert plan.version == "1.10.1"

    def test_a_rerun_of_a_tagged_release_plans_the_same_version(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "v1.10.0")
        feature = _commit(repository, "feat: one")
        marked = _commit(repository, "fix: two")
        _git(repository, "tag", "v1.11.0", marked)
        refs = {feature: ("feat/one",), marked: ("fix/two",)}

        plan = plan_release(repository, marked, lambda sha: refs[sha])

        assert (plan.last_tag, plan.version) == ("v1.10.0", "1.11.0")

    def test_refuses_a_range_that_ships_no_version(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "v1.10.0")
        only = _commit(repository, "ci: one")

        with pytest.raises(ReleasePlanError, match="ships no version"):
            plan_release(repository, only, lambda _sha: ("ci/one",))

    def test_a_commit_carrying_the_only_tag_is_planned_from_the_start(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "v1.10.0")
        head = _git(repository, "rev-parse", "HEAD")

        plan = plan_release(repository, head, lambda _sha: ("fix/x",))

        assert (plan.last_tag, plan.version) == (None, "0.0.1")

    def test_refuses_a_commit_that_belongs_to_no_pull_request(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "v1.10.0")
        pushed = _commit(repository, "fix: direct")

        with pytest.raises(ReleasePlanError, match="belongs to no pull request"):
            plan_release(repository, pushed, lambda _sha: ())

    def test_refuses_an_unclassified_branch_instead_of_lowering_the_part(
        self, tmp_path: Path
    ) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "v1.10.0")
        _commit(repository, "feat: one")
        marked = _commit(repository, "spike: two")
        with pytest.raises(ReleasePlanError, match="matches no class"):
            plan_release(repository, marked, lambda _sha: ("spike/two",))

    def test_renders_every_shipped_branch_for_an_operator(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "v1.10.0")
        marked = _commit(repository, "feat: one")

        rendered = plan_release(repository, marked, lambda _sha: ("feat/one",)).render()

        assert "version  : 1.11.0" in rendered
        assert "feat/one" in rendered


class TestSemanticBranchConfig:
    def test_default_config_output_is_identical(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "v1.10.0")
        merge = _commit(repository, "feat: one")
        monkeypatch.setattr(
            plan_release_module, "gh_commit_pull_requests", lambda _slug: lambda _sha: ("feat/one",)
        )
        base = ["--repository", str(repository), "--merge-sha", merge, "--slug", "o/r"]

        outputs = []
        variants = (
            [],
            ["--semantic-branch-config", "pyproject.toml"],
            ["--semantic-branch-config", ""],
        )
        for extra in variants:
            for output_format in ("text", "github"):
                assert main([*base, *extra, "--format", output_format]) == 0
                outputs.append(capsys.readouterr().out)

        assert outputs[:2] == outputs[2:4] == outputs[4:]
        assert outputs[1] == '{"version": "1.11.0", "part": "minor"}\n'

    def test_custom_config_path_is_read(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path, "[project]\nname = 'monorepo'\n")
        (repository / "apps" / "api").mkdir(parents=True)
        (repository / "apps" / "api" / "pyproject.toml").write_text(_rules_toml(), encoding="utf-8")
        _git(repository, "tag", "v1.10.0")
        merge = _commit(repository, "fix: one")

        plan = plan_release(
            repository, merge, lambda _sha: ("fix/one",), config=Path("apps/api/pyproject.toml")
        )

        assert (plan.part, plan.version) == ("patch", "1.10.1")
        with pytest.raises(ReleasePlanError, match="matches no class"):
            plan_release(repository, merge, lambda _sha: ("fix/one",))

    def test_missing_config_raises_release_plan_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "v1.10.0")
        merge = _commit(repository, "fix: one")

        with pytest.raises(ReleasePlanError, match="apps/api/pyproject.toml.*not found"):
            plan_release(
                repository, merge, lambda _sha: ("fix/one",), config=Path("apps/api/pyproject.toml")
            )
        with pytest.raises(SystemExit) as exited:
            main(
                [
                    "--repository",
                    str(repository),
                    "--merge-sha",
                    merge,
                    "--slug",
                    "o/r",
                    "--semantic-branch-config",
                    "missing.toml",
                ]
            )
        assert exited.value.code == 1
        assert "missing.toml" in capsys.readouterr().err

    def test_dependabot_branch_is_release_ignore_when_configured(self, tmp_path: Path) -> None:
        rules = {**_RULES, "release_ignore": (*_RULES["release_ignore"], "dependabot/.*")}
        repository = _repository(tmp_path, _rules_toml())
        (repository / "release.toml").write_text(_rules_toml(rules), encoding="utf-8")
        _git(repository, "tag", "v1.10.0")
        bump = _commit(repository, "build(deps): bump a dependency")

        def head_refs(_sha: str) -> tuple[str, ...]:
            return ("dependabot/pip/uv-0.9.0",)

        assert classify_branch("dependabot/pip/uv-0.9.0", rules) is None
        with pytest.raises(ReleasePlanError, match="matches no class"):
            plan_release(repository, bump, head_refs)
        with pytest.raises(ReleasePlanError, match="ships no version"):
            plan_release(repository, bump, head_refs, config=Path("release.toml"))


class TestTagPrefix:
    """A package of a monorepo plans from its own tags and never another package's."""

    def test_the_default_reads_the_tags_it_always_read(self) -> None:
        assert release_tag_glob() == "v[0-9]*.[0-9]*.[0-9]*"
        assert release_tag_pattern().pattern == r"^v(\d+)\.(\d+)\.(\d+)$"

    @pytest.mark.parametrize("prefix", ["control-plane-v", "api_v", "apps/api/v", "r.", "2"])
    def test_a_safe_prefix_is_accepted(self, prefix: str) -> None:
        assert check_tag_prefix(prefix) == prefix

    @pytest.mark.parametrize(
        "prefix",
        ["", "-v", "/v", ".v", "a..b", "a//b", "a/.b", "a.lock/v", "a v", "v$(id)", 'v"', "v*"],
    )
    def test_an_unsafe_prefix_is_refused(self, prefix: str) -> None:
        with pytest.raises(TagPrefixError, match="is not allowed"):
            check_tag_prefix(prefix)

    def test_next_version_reads_the_prefixed_tag(self) -> None:
        assert next_version("api-v1.9.9", "patch", "api-v") == "1.9.10"
        with pytest.raises(ReleasePlanError, match="is not a release tag"):
            next_version("v1.9.9", "patch", "api-v")

    def test_another_packages_later_tag_is_not_read(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "api-v1.10.0")
        _commit(repository, "fix: worker")
        _git(repository, "tag", "worker-v3.0.0")
        _git(repository, "tag", "v7.0.0")
        merge = _commit(repository, "feat: api")
        refs = {merge: ("feat/api",)}

        plan = plan_release(
            repository, merge, lambda sha: refs.get(sha, ("fix/x",)), scope=ReleaseScope("api-v")
        )

        assert (plan.last_tag, plan.part, plan.version) == ("api-v1.10.0", "minor", "1.11.0")
        assert len(plan.shipped) == 2

    def test_a_prefix_that_starts_another_prefix_does_not_read_its_tags(
        self, tmp_path: Path
    ) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "api-v1.0.0")
        _commit(repository, "fix: one")
        _git(repository, "tag", "api-v2-v5.0.0")
        merge = _commit(repository, "fix: two")

        plan = plan_release(repository, merge, lambda _sha: ("fix/x",), scope=ReleaseScope("api-v"))

        assert (plan.last_tag, plan.version) == ("api-v1.0.0", "1.0.1")

    def test_a_package_without_tags_starts_from_zero_beside_other_packages(
        self, tmp_path: Path
    ) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "v1.10.0")
        merge = _commit(repository, "feat: first")

        plan = plan_release(
            repository, merge, lambda _sha: ("feat/x",), scope=ReleaseScope("api-v")
        )

        assert (plan.last_tag, plan.version) == (None, "0.1.0")

    def test_a_slash_in_the_prefix_reads_the_nested_tags(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "apps/api/v0.3.0")
        merge = _commit(repository, "fix: one")

        plan = plan_release(
            repository, merge, lambda _sha: ("fix/x",), scope=ReleaseScope("apps/api/v")
        )

        assert (plan.last_tag, plan.version) == ("apps/api/v0.3.0", "0.3.1")

    def test_a_rerun_of_a_prefixed_release_plans_the_same_version(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "api-v1.10.0")
        merge = _commit(repository, "feat: one")
        _git(repository, "tag", "api-v1.11.0", merge)

        plan = plan_release(
            repository, merge, lambda _sha: ("feat/x",), scope=ReleaseScope("api-v")
        )

        assert (plan.last_tag, plan.version) == ("api-v1.10.0", "1.11.0")

    def test_an_unsafe_prefix_fails_the_plan(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repository = _repository(tmp_path, _rules_toml())
        merge = _commit(repository, "fix: one")

        with pytest.raises(ReleaseScopeError, match="is not allowed"):
            ReleaseScope("v;id")
        with pytest.raises(SystemExit) as exited:
            main(
                [
                    "--repository",
                    str(repository),
                    "--merge-sha",
                    merge,
                    "--slug",
                    "o/r",
                    "--tag-prefix=-v",
                ]
            )
        assert exited.value.code == 1
        assert "tag prefix '-v' is not allowed" in capsys.readouterr().err

    def test_default_prefix_output_is_identical(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "v1.10.0")
        _git(repository, "tag", "api-v4.0.0")
        merge = _commit(repository, "feat: one")
        monkeypatch.setattr(
            plan_release_module, "gh_commit_pull_requests", lambda _slug: lambda _sha: ("feat/one",)
        )
        base = ["--repository", str(repository), "--merge-sha", merge, "--slug", "o/r"]

        outputs = []
        for extra in ([], ["--tag-prefix", "v"], ["--tag-prefix=v"]):
            for output_format in ("text", "github"):
                assert main([*base, *extra, "--format", output_format]) == 0
                outputs.append(capsys.readouterr().out)

        assert outputs[:2] == outputs[2:4] == outputs[4:]
        assert outputs[0].startswith("last tag : v1.10.0\n")
        assert outputs[1] == '{"version": "1.11.0", "part": "minor"}\n'


class TestChangelogCommits:
    """A release that keeps a changelog commits it with no pull request.

    That commit lands after the release's tag, so the next release reads it. It
    changes nothing but ``CHANGELOG.md`` files, which ship no version, so the
    planner passes over it instead of refusing it as a direct push.
    """

    def _changelog_commit(self, repository: Path, *paths: str) -> str:
        for path in paths:
            (repository / path).parent.mkdir(parents=True, exist_ok=True)
            (repository / path).write_text("# Changelog\n", encoding="utf-8")
            _git(repository, "add", path)
        _git(repository, "commit", "-m", "docs(release): changelog for v1.10.0")
        return _git(repository, "rev-parse", "HEAD")

    @pytest.mark.parametrize(
        "paths", [("CHANGELOG.md",), ("apps/api/CHANGELOG.md",), ("CHANGELOG.md", "a/CHANGELOG.md")]
    )
    def test_a_commit_changing_only_changelogs_is_passed_over(
        self, tmp_path: Path, paths: tuple[str, ...]
    ) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "v1.10.0")
        changelog = self._changelog_commit(repository, *paths)
        merge = _commit(repository, "feat: one")
        refs = {changelog: (), merge: ("feat/one",)}

        plan = plan_release(repository, merge, lambda sha: refs[sha])

        assert plan.version == "1.11.0"
        assert [entry.sha for entry in plan.shipped] == [merge]

    @pytest.mark.parametrize("paths", [("README.md",), ("CHANGELOG.md", "src.py")])
    def test_a_direct_push_touching_anything_else_is_still_refused(
        self, tmp_path: Path, paths: tuple[str, ...]
    ) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "v1.10.0")
        pushed = self._changelog_commit(repository, *paths)

        with pytest.raises(ReleasePlanError, match="belongs to no pull request"):
            plan_release(repository, pushed, lambda _sha: ())

    def test_a_range_holding_only_a_changelog_commit_ships_nothing(self, tmp_path: Path) -> None:
        repository = _repository(tmp_path, _rules_toml())
        _git(repository, "tag", "v1.10.0")
        changelog = self._changelog_commit(repository, "CHANGELOG.md")

        with pytest.raises(ReleasePlanError, match="nothing to release"):
            plan_release(repository, changelog, lambda _sha: ())
