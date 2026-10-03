from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, TypedDict

import tomli_w

VERSION_UNRELEASED = "UNRELEASED"


class SemanticBranchConfig(TypedDict, total=False):
    """Structure of the [tool.semantic_branch] section in pyproject.toml."""

    major: list[str]
    minor: list[str]
    patch: list[str]
    prerelease: list[str]
    prerelease_ignore: list[str]
    release_ignore: list[str]


class ConfigError(Exception):
    """Custom error for configuration-related problems."""


def load_config(path: str) -> dict[str, Any]:
    """Load a TOML file and return its contents as a dictionary."""
    file_path = Path(path)
    if not file_path.exists():
        raise ConfigError(f"❌ Config file not found: {path}")
    return tomllib.loads(file_path.read_text(encoding="utf-8"))


def matches(branch: str, patterns: list[str] | None) -> bool:
    """Check if the branch name matches any of the given regex patterns."""
    if not patterns:
        return False
    return any(re.fullmatch(pattern, branch) for pattern in patterns)


def bump(branch: str, cfg: SemanticBranchConfig, current: str, breaking: bool = False) -> str:
    """Increment version number according to the rules defined in config.

    A declared break raises a major whatever the branch asks for.
    """
    major, minor, patch = map(int, current.split("."))

    if breaking:
        major += 1
        minor = 0
        patch = 0
    elif matches(branch, cfg.get("minor")):
        minor += 1
        patch = 0
    elif matches(branch, cfg.get("major")):
        major += 1
        minor = 0
        patch = 0
    elif matches(branch, cfg.get("patch")):
        patch += 1

    return f"{major}.{minor}.{patch}"


def calc_next_version(
    cfg: SemanticBranchConfig,
    branch: str,
    prerelease: bool,
    current: str,
    breaking: bool = False,
) -> tuple[str, bool]:
    """
    Calculate the next version number based on branch name and configuration.

    When *breaking* is true a commit declared a break: the release is a major and
    is not skipped by ``release_ignore``, as plan-release decides.

    Returns:
        - the calculated version string
        - a boolean indicating whether deployment should proceed
    """
    if (prerelease and matches(branch, cfg.get("prerelease_ignore"))) or (
        not prerelease and not breaking and matches(branch, cfg.get("release_ignore"))
    ):
        return VERSION_UNRELEASED, False

    next_version = bump(branch, cfg, current, breaking)
    if prerelease and matches(branch, cfg.get("prerelease")):
        count = subprocess.check_output(
            ["git", "rev-list", "--count", "HEAD"],
            text=True,
        ).strip()
        return f"{next_version}.dev{count}", True

    if not prerelease:
        return next_version, True

    raise ValueError(f"Branch {branch} does not match any rules")


def update_pyproject(data: dict[str, Any], path: Path, new_version: str) -> None:
    """Update the version field inside pyproject.toml."""
    project_data = data.setdefault("project", {})
    project_data["version"] = new_version
    path.write_text(tomli_w.dumps(data), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    """Configure and parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Semantic version bumping tool")
    parser.add_argument("--branch", required=True, help="Current branch name")
    parser.add_argument(
        "--prerelease",
        required=False,
        default="false",
        help="Flag to indicate prerelease",
    )
    parser.add_argument(
        "--config", required=False, default="pyproject.toml", help="Path to config file"
    )
    parser.add_argument(
        "--semantic-branch-config",
        required=False,
        default="",
        help="TOML file declaring [tool.semantic_branch]; empty reads it from --config",
    )
    parser.add_argument(
        "--breaking",
        required=False,
        default="false",
        help="true when a commit declared a break: raises the version to a major",
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point of the script."""
    try:
        args = parse_args()
        data = load_config(args.config)
        # The version is always read from and written to --config; only the
        # branch rules may live in another file, which is never rewritten.
        rules = load_config(args.semantic_branch_config) if args.semantic_branch_config else data
        cfg: SemanticBranchConfig = rules.get("tool", {}).get("semantic_branch", {})
        current_version: str = data.get("project", {}).get("version", "0.1.0")
        prerelease: bool = args.prerelease.lower() == "true"
        breaking: bool = args.breaking.lower() == "true"

        version, deploy = calc_next_version(
            cfg, args.branch, prerelease, current_version, breaking
        )

        if deploy and Path(args.config).suffix == ".toml":
            update_pyproject(data, Path(args.config), version)

        print(f"version={version}")
        print(f"deploy={'true' if deploy else 'false'}")
    except ConfigError as e:
        sys.exit(str(e))
    except Exception as e:
        sys.exit(f"❌ Unexpected error: {e}")


if __name__ == "__main__":
    main()
