"""A ``gh`` and a ``sleep`` on PATH that log every call and answer from a script.

Each rule matches a call whose arguments, joined by spaces, contain its text;
the first matching rule answers, with its answers in turn and the last one
repeated. A call no rule matches fails, so a script never reaches GitHub in a
way the test did not expect. ``sleep`` only logs how long it was asked to wait.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_GH = """#!/usr/bin/env python3
import json, pathlib, sys
config = pathlib.Path({config!r})
line = " ".join(sys.argv[1:])
with open({log!r}, "a", encoding="utf-8") as log:
    log.write(line + "\\n")
for index, rule in enumerate(json.loads(config.read_text("utf-8"))):
    if rule["match"] in line:
        counter = config.with_name(f"{{config.name}}.{{index}}")
        seen = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(seen + 1))
        answer = rule["answers"][min(seen, len(rule["answers"]) - 1)]
        sys.stdout.write(answer.get("stdout", ""))
        sys.stderr.write(answer.get("stderr", ""))
        sys.exit(answer.get("exit", 0))
sys.stderr.write("unexpected gh call: " + line + "\\n")
sys.exit(97)
"""

_SLEEP = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> {log!r}
"""


def answer(body: Any = None, *, exit: int = 0, stderr: str = "") -> dict[str, Any]:
    """One answer: *body* as JSON (or as is when a string), and the exit status."""
    stdout = body if isinstance(body, str) else ("" if body is None else json.dumps(body))
    return {"stdout": stdout, "stderr": stderr, "exit": exit}


@dataclass(frozen=True)
class Stub:
    env: dict[str, str]
    gh_log: Path
    sleep_log: Path

    def calls(self) -> list[str]:
        return self.gh_log.read_text("utf-8").splitlines() if self.gh_log.exists() else []

    def sleeps(self) -> list[str]:
        return self.sleep_log.read_text("utf-8").splitlines() if self.sleep_log.exists() else []


def install(tmp_path: Path, rules: list[tuple[str, list[dict[str, Any]]]]) -> Stub:
    """Put the stubs on PATH, *rules* being ``(text the call holds, answers)`` pairs."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    config = tmp_path / "gh-rules.json"
    config.write_text(
        json.dumps([{"match": match, "answers": answers} for match, answers in rules]), "utf-8"
    )
    gh_log, sleep_log = tmp_path / "gh.log", tmp_path / "sleep.log"
    for name, text in (
        ("gh", _GH.format(config=str(config), log=str(gh_log))),
        ("sleep", _SLEEP.format(log=str(sleep_log))),
    ):
        (bin_dir / name).write_text(text, encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GH_TOKEN": "t",
        "GITHUB_REPOSITORY": "o/r",
    }
    return Stub(env, gh_log, sleep_log)
