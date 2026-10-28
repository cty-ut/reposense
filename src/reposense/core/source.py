"""Helpers for showing source code to the model and checking what it cites back."""

from __future__ import annotations

import re

_OUTLINE = re.compile(r"^\s*(?:async\s+def|def|class|function|export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const)|"
                      r"func|fn|pub\s+fn|interface|type|struct|impl)\b")


def numbered(text: str, max_lines: int = 900) -> str:
    """Source with line numbers. Beyond `max_lines`, only definition lines (an outline) are kept."""
    lines = text.splitlines()
    head = [f"{i:>5}  {ln}" for i, ln in enumerate(lines[:max_lines], 1)]
    rest = [f"{i:>5}  {ln}" for i, ln in enumerate(lines[max_lines:], max_lines + 1) if _OUTLINE.match(ln)]
    if rest:
        head.append(f"  ... (lines {max_lines + 1}-{len(lines)}: only definitions shown)")
    return "\n".join(head + rest)


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def anchor_in_range(lines: list[str], start: int, end: int, anchor: str) -> bool:
    """True when `anchor` (whitespace-insensitive) occurs within lines start..end (1-based)."""
    if not (1 <= start <= end <= len(lines)) or not norm(anchor):
        return False
    return norm(anchor) in norm("\n".join(lines[start - 1:end]))


def find_symbol(lines: list[str], line: int, name: str, slack: int = 3) -> int | None:
    """Line where `name` appears at or near `line`; None when the model's citation is wrong."""
    short = name.split(".")[-1].split("(")[0].strip()
    if not short:
        return None
    pat = re.compile(rf"\b{re.escape(short)}\b")
    for delta in range(slack + 1):
        for ln in (line - delta, line + delta):
            if 1 <= ln <= len(lines) and pat.search(lines[ln - 1]):
                return ln
    return None
