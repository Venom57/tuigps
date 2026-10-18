"""Source-level compatibility checks.

requires-python is >=3.10, but the dev venv runs a newer Python whose parser
accepts PEP 701 f-strings. ``ast.parse(..., feature_version=(3, 10))`` does not
reject them, so the pre-3.12 f-string restrictions are checked explicitly.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "tuigps"


def _sources():
    return sorted(SRC.rglob("*.py"))


def _fstring_offenders() -> list[str]:
    """f-string expressions that are a SyntaxError on Python 3.10/3.11."""
    offenders = []
    for path in _sources():
        source = path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            outer = ast.get_source_segment(source, node) or ""
            quote = outer.lstrip("rRbBfFuU")[:1]
            for value in node.values:
                if not isinstance(value, ast.FormattedValue):
                    continue
                seg = ast.get_source_segment(source, value.value) or ""
                if "\\" in seg:
                    offenders.append(f"{path.relative_to(SRC)}:{value.lineno}: backslash")
                if quote and quote in seg:
                    offenders.append(f"{path.relative_to(SRC)}:{value.lineno}: reused quote")
    return offenders


def test_sources_parse():
    for path in _sources():
        ast.parse(path.read_text(), filename=str(path), feature_version=(3, 10))


def test_no_pep701_only_fstring_expressions():
    assert _fstring_offenders() == []
