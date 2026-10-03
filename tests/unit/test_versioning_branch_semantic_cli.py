"""Unit tests for the branch rules file ``versioning-branch-semantic`` reads.

The version is read from, and written back to, ``--config``. The branch rules
come from ``--semantic-branch-config`` when it is given, and from ``--config``
otherwise, so a caller that passes nothing new computes exactly what it did.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

CLI = (
    Path(__file__).parents[2]
    / "actions"
    / "release"
    / "versioning-branch-semantic"
    / "src"
    / "cli.py"
)

RULES = """[tool.semantic_branch]
minor = ["feat/.*"]
patch = ["fix/.*"]
release_ignore = ["docs/.*"]
"""

DEPENDABOT_RULES = """[tool.semantic_branch]
minor = ["feat/.*"]
patch = ["fix/.*"]
release_ignore = ["docs/.*", "dependabot/.*"]
"""


def _cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("versioning_cli", CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *arguments: str,
) -> str:
    monkeypatch.setattr(sys, "argv", ["cli.py", *arguments])
    _cli().main()
    return capsys.readouterr().out


def _project(path: Path, version: str, rules: str = "") -> Path:
    path.write_text(f'[project]\nname = "api"\nversion = "{version}"\n\n{rules}', encoding="utf-8")
    return path


def test_default_config_output_is_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    without = _project(tmp_path / "without.toml", "1.2.3", RULES)
    explicit = _project(tmp_path / "explicit.toml", "1.2.3", RULES)

    plain = _run(monkeypatch, capsys, "--branch", "feat/x", "--config", str(without))
    empty = _run(
        monkeypatch,
        capsys,
        "--branch",
        "feat/x",
        "--config",
        str(explicit),
        "--semantic-branch-config",
        "",
    )

    assert plain == empty == "version=1.3.0\ndeploy=true\n"
    assert without.read_text(encoding="utf-8") == explicit.read_text(encoding="utf-8")


def test_custom_config_path_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path / "pyproject.toml", "1.2.3")
    rules = tmp_path / "release.toml"
    rules.write_text(RULES, encoding="utf-8")

    output = _run(
        monkeypatch,
        capsys,
        "--branch",
        "fix/x",
        "--config",
        str(project),
        "--semantic-branch-config",
        str(rules),
    )

    assert output == "version=1.2.4\ndeploy=true\n"
    assert 'version = "1.2.4"' in project.read_text(encoding="utf-8")
    assert rules.read_text(encoding="utf-8") == RULES


def test_missing_config_exits_with_a_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path / "pyproject.toml", "1.2.3", RULES)
    missing = tmp_path / "missing.toml"

    with pytest.raises(SystemExit) as exited:
        _run(
            monkeypatch,
            capsys,
            "--branch",
            "fix/x",
            "--config",
            str(project),
            "--semantic-branch-config",
            str(missing),
        )

    assert str(exited.value.code) == f"❌ Config file not found: {missing}"


def test_dependabot_branch_is_release_ignore_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path / "pyproject.toml", "1.2.3")
    before = project.read_text(encoding="utf-8")
    rules = tmp_path / "release.toml"
    rules.write_text(DEPENDABOT_RULES, encoding="utf-8")

    output = _run(
        monkeypatch,
        capsys,
        "--branch",
        "dependabot/pip/uv-0.9.0",
        "--config",
        str(project),
        "--semantic-branch-config",
        str(rules),
    )

    assert output == "version=UNRELEASED\ndeploy=false\n"
    assert project.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("branch", ["feat/x", "fix/x", "docs/x"])
def test_without_a_declared_break_the_output_is_identical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    branch: str,
) -> None:
    without = _project(tmp_path / "without.toml", "1.2.3", RULES)
    explicit = _project(tmp_path / "explicit.toml", "1.2.3", RULES)

    plain = _run(monkeypatch, capsys, "--branch", branch, "--config", str(without))
    unmarked = _run(
        monkeypatch, capsys, "--branch", branch, "--config", str(explicit), "--breaking", "false"
    )

    assert plain == unmarked
    assert without.read_text(encoding="utf-8") == explicit.read_text(encoding="utf-8")


@pytest.mark.parametrize("branch", ["feat/x", "fix/x"])
def test_a_declared_break_raises_any_branch_to_a_major(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    branch: str,
) -> None:
    project = _project(tmp_path / "pyproject.toml", "1.10.0", RULES)

    output = _run(
        monkeypatch, capsys, "--branch", branch, "--config", str(project), "--breaking", "true"
    )

    assert output == "version=2.0.0\ndeploy=true\n"
    assert 'version = "2.0.0"' in project.read_text(encoding="utf-8")


def test_a_declared_break_is_released_even_from_an_ignored_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = _project(tmp_path / "pyproject.toml", "1.10.0", RULES)

    output = _run(
        monkeypatch, capsys, "--branch", "docs/x", "--config", str(project), "--breaking", "true"
    )

    assert output == "version=2.0.0\ndeploy=true\n"
