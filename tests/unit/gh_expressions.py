"""Evaluate the ``if:`` expressions of a workflow the way GitHub Actions does.

Enough of the expression syntax for the job and step conditions of
``release-on-label``: string literals, ``true``/``false``, context paths such
as ``needs.tag-app.result``, ``==``, ``!=``, ``!``, ``&&``, ``||``,
parentheses and the status functions. Every context path must be given, so a
condition that reads something the test did not think of fails loudly.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_TOKEN = re.compile(
    r"\s*(?:(?P<string>'(?:[^']|'')*')"
    r"|(?P<op>&&|\|\||==|!=|!|\(|\))"
    r"|(?P<call>[A-Za-z_][\w-]*\(\))"
    r"|(?P<name>[A-Za-z_][\w.-]*))"
)
_OPERATORS = {"&&": " and ", "||": " or ", "!": " not ", "==": "==", "!=": "!=", "(": "(", ")": ")"}


def _body(expression: str) -> str:
    text = expression.strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2]
    return " ".join(text.split())


def evaluate(expression: str, context: Mapping[str, object]) -> bool:
    """Return the truth of *expression*, every path and call read from *context*."""
    text, position = _body(expression), 0
    source: list[str] = []
    values: list[object] = []
    while position < len(text):
        found = _TOKEN.match(text, position)
        if found is None or found.end() == position:
            raise ValueError(f"cannot read {text[position:]!r}")
        position = found.end()
        if found["string"] is not None:
            values.append(found["string"][1:-1].replace("''", "'"))
            source.append(f"_v[{len(values) - 1}]")
        elif found["op"] is not None:
            source.append(_OPERATORS[found["op"]])
        else:
            key = found["call"] or found["name"]
            if key in ("true", "false"):
                values.append(key == "true")
            else:
                values.append(context[key])
            source.append(f"_v[{len(values) - 1}]")
    return bool(eval("".join(source), {"__builtins__": {}}, {"_v": values}))  # noqa: S307


def conjuncts(expression: str) -> list[str]:
    """Split *expression* on its top-level ``&&``; one item when it holds none."""
    text, depth, start = _body(expression), 0, 0
    parts: list[str] = []
    for index, char in enumerate(text):
        depth += {"(": 1, ")": -1}.get(char, 0)
        if depth == 0 and text.startswith("&&", index):
            parts.append(text[start:index].strip())
            start = index + 2
    parts.append(text[start:].strip())
    return [part for part in parts if part]
