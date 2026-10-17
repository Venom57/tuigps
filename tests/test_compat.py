"""Source-level compatibility checks."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "tuigps"


def _fstring_backslash_offenders() -> list[str]:
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        source = path.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FormattedValue):
                seg = ast.get_source_segment(source, node.value) or ""
                if "\\" in seg:
                    offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    return offenders


def test_sources_parse():
    for path in SRC.rglob("*.py"):
        ast.parse(path.read_text(), filename=str(path))


@pytest.mark.xfail(
    strict=True,
    reason="New finding N1 (not in plan): backslash inside an f-string expression "
    "(widgets/satellite_table.py) is a SyntaxError on Python 3.10/3.11, but "
    "requires-python is >=3.10",
)
def test_no_backslash_in_fstring_expressions():
    assert _fstring_backslash_offenders() == []
