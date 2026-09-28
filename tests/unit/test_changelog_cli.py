from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_changelog_cli_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = (
        repo_root / "actions/release/changelog-conventional-commit/src/cli.py"
    )
    spec = importlib.util.spec_from_file_location("changelog_cli", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_get_commit_squash_falls_back_to_subject_when_body_has_no_commit_list(
    monkeypatch,
) -> None:
    cli = _load_changelog_cli_module()

    def _fake_check_output(cmd, text=True):  # noqa: ARG001
        return "abc1234|abc1234abcdef|feat(core): add parser (#42)|"

    monkeypatch.setattr(cli.subprocess, "check_output", _fake_check_output)

    squash = cli.get_commit_squash()
    assert squash["sha"] == "abc1234"
    assert len(squash["commits"]) == 1
    assert squash["commits"][0]["subject"] == "feat(core): add parser"


def test_render_pr_mode_includes_stable_pr_files_link() -> None:
    cli = _load_changelog_cli_module()
    grouped = {
        "feat": {
            "(no scope)": [
                {
                    "title": "add parser",
                    "scope": "(no scope)",
                    "body": "",
                    "sha": "abc1234",
                    "sha_full": "abc1234abcdef",
                }
            ]
        }
    }

    markdown = cli.render(
        template_path=cli.DEFAULT_TEMPLATE,
        version="1.2.3",
        commits=grouped,
        repo_url="https://github.com/MassiveDataScope/loom-actions",
        squash=None,
        is_unreleased=True,
        pr_number=42,
    )

    assert "/pull/42" in markdown
    assert "/pull/42/files" in markdown


def test_render_hides_no_scope_heading_and_shows_unscoped_first() -> None:
    cli = _load_changelog_cli_module()
    grouped = {
        "feat": {
            "api": [
                {
                    "title": "scoped change",
                    "scope": "api",
                    "body": "",
                    "sha": "bbb2222",
                    "sha_full": "bbb2222abcdef",
                }
            ],
            "(no scope)": [
                {
                    "title": "unscoped change",
                    "scope": "(no scope)",
                    "body": "",
                    "sha": "aaa1111",
                    "sha_full": "aaa1111abcdef",
                }
            ],
        }
    }

    markdown = cli.render(
        template_path=cli.DEFAULT_TEMPLATE,
        version="1.2.3",
        commits=grouped,
        repo_url="https://github.com/MassiveDataScope/loom-actions",
        squash=None,
        is_unreleased=True,
        pr_number=42,
    )

    assert "### (no scope)" not in markdown
    assert markdown.index("unscoped change") < markdown.index("### api")


def test_get_commit_squash_keeps_the_subject_when_the_body_names_an_unrendered_type(
    monkeypatch,
) -> None:
    """A prose line shaped like a conventional commit must not swallow the release.

    Reproduces the commit that produced an empty 1.7.0 changelog: the squash
    subject is a real ``feat``, but the body carries ``Verification: 4866
    passed...``, which parses as a conventional commit of type ``Verification``
    -- a group the template does not render.  The old fallback only fired on an
    empty list, so the subject was dropped and the changelog came out blank.
    """
    cli = _load_changelog_cli_module()
    subject = "feat(ai): pluggable MCP server authentication (#139)"
    body = (
        "Loom could only reach MCP servers with no authentication at all.\n"
        "\n"
        "Verification: 4866 passed, 18 skipped; mypy clean on 541 files.\n"
    )
    monkeypatch.setattr(
        cli.subprocess,
        "check_output",
        lambda *_args, **_kwargs: f"abc1234|{'a' * 40}|{subject}|{body}",
    )

    squash = cli.get_commit_squash()
    grouped = cli.group_commits(squash["commits"])

    assert "feat" in grouped, (
        "the squash subject is the only reliable source for a squash merge; "
        f"grouped types were {sorted(grouped)}"
    )
    titles = [item["title"] for scope in grouped["feat"].values() for item in scope]
    assert "pluggable MCP server authentication" in titles
