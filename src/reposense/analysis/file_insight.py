"""What one file does: summary, responsibilities, key symbols, collaborators.

The model writes the explanations; symbol line numbers and collaborator paths are checked
against the real file and repository, and wrong ones are fixed or dropped.
"""

from __future__ import annotations

import json

from reposense.core.cache import JsonCache, content_hash
from reposense.core.llm import LLM
from reposense.core.source import find_symbol, numbered
from reposense.core.workspace import Workspace

SYSTEM = """You explain one source file to a developer who is new to the repository {repo}.
Return JSON:
{{
  "summary": "what this file is for, 1-2 sentences",
  "role": "how it fits into the project, 1 sentence",
  "responsibilities": ["3-6 short bullet points"],
  "symbols": [{{"name": "function/class name", "kind": "class|function|method|constant|other",
               "line": <line number where it is defined>, "description": "1 sentence"}}],
  "collaborators": [{{"path": "repo-relative path of a file this one imports or is tightly coupled with",
                     "relation": "1 short phrase"}}],
  "newcomer_tips": ["things to know before editing this file, 0-3 items"]
}}
List the 5-15 most important symbols. Use the line numbers shown in the input. Write text in {lang}."""


def file_insight(ws: Workspace, path: str, llm: LLM, cache: JsonCache, *, lang: str = "Chinese",
                 refresh: bool = False) -> dict:
    text = ws.read_text(path)
    if text is None:
        raise FileNotFoundError(path)
    lines = text.splitlines()

    def compute() -> dict:
        raw = llm.json(SYSTEM.format(repo=ws.repo, lang=lang),
                       json.dumps({"path": path, "source": numbered(text)}, ensure_ascii=False))
        return verify(ws, raw, lines)

    result = cache.get_or_compute("insight", f"{path}:{content_hash(text)}:{lang}", compute, refresh=refresh)
    return {**result, "path": path}


def verify(ws: Workspace, raw: dict, lines: list[str]) -> dict:
    symbols, dropped = [], []
    for s in raw.get("symbols") or []:
        name, line = str(s.get("name", "")), int(s.get("line") or 0)
        found = find_symbol(lines, line, name)
        if found is None:
            dropped.append(f"symbol {name}: not found near line {line}")
            continue
        symbols.append({"name": name, "kind": s.get("kind", "other"), "line": found,
                        "description": str(s.get("description", ""))})
    collaborators = []
    for c in raw.get("collaborators") or []:
        p = str(c.get("path", "")).strip("/")
        try:
            exists = ws.resolve(p).is_file()
        except ValueError:
            exists = False
        if exists:
            collaborators.append({"path": p, "relation": str(c.get("relation", ""))})
        else:
            dropped.append(f"collaborator {p}: no such file")
    return {
        "summary": str(raw.get("summary", "")), "role": str(raw.get("role", "")),
        "responsibilities": [str(x) for x in raw.get("responsibilities") or []],
        "symbols": sorted(symbols, key=lambda s: s["line"]),
        "collaborators": collaborators,
        "newcomer_tips": [str(x) for x in raw.get("newcomer_tips") or []],
        "dropped": dropped,
    }
