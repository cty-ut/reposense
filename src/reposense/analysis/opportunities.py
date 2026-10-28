"""Ways to contribute around one file: open GitHub issues that mention it, and improvement ideas
found by reading the code (each idea must cite an exact line range, which is verified).
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from reposense.core.cache import JsonCache, content_hash
from reposense.core.git import Git
from reposense.core.github import GitHubClient
from reposense.core.llm import LLM
from reposense.core.source import anchor_in_range, numbered
from reposense.core.workspace import Workspace

_TODO = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")

SYSTEM = """You help a first-time contributor find a good first contribution in one file of {repo}.
Suggest 3-6 concrete, small, reviewable improvements a newcomer could make here, e.g. a missing test
for an untested branch, an unclear error message, a TODO that is ready to be done, a missing type hint
or docstring, an edge case. Avoid large refactors and style-only nitpicks.
Return JSON:
{{"suggestions": [{{"title": "short title", "kind": "bug|test|docs|typing|feature|refactor",
  "difficulty": "easy|medium|hard", "start_line": n, "end_line": n,
  "anchor": "one exact code line copied from start_line..end_line",
  "detail": "what to change and why, 1-3 sentences"}}]}}
Use the line numbers shown in the input. Write text in {lang}."""


def related_issues(gh: GitHubClient, path: str, symbols: list[str] | None = None, limit: int = 10) -> list[dict]:
    """Open issues mentioning the file name or its main symbols."""
    name = PurePosixPath(path).name
    queries = [(f'"{name}"', f"mentions {name}")]
    queries += [(f'"{s}"', f"mentions {s}") for s in (symbols or [])[:3] if len(s) > 3]
    seen: dict[int, dict] = {}
    for q, why in queries:
        try:
            for it in gh.search(q, "issue", state="open", limit=limit):
                seen.setdefault(it["number"], {**it, "match": why})
        except Exception:
            continue
    return sorted(seen.values(), key=lambda i: -i["comments"])[:limit]


def code_suggestions(ws: Workspace, path: str, llm: LLM, cache: JsonCache, *, lang: str = "Chinese",
                     refresh: bool = False) -> dict:
    text = ws.read_text(path)
    if text is None:
        raise FileNotFoundError(path)
    lines = text.splitlines()

    def compute() -> dict:
        stem = PurePosixPath(path).stem
        test_refs = [f for f in Git(ws.root).ls_files() if "test" in f.lower() and stem in PurePosixPath(f).stem]
        todos = [f"{i}: {ln.strip()}" for i, ln in enumerate(lines, 1) if _TODO.search(ln)][:20]
        raw = llm.json(SYSTEM.format(repo=ws.repo, lang=lang), json.dumps({
            "path": path, "test_files_with_similar_name": test_refs[:5], "todo_lines": todos,
            "source": numbered(text),
        }, ensure_ascii=False))
        kept, dropped = [], []
        for s in raw.get("suggestions") or []:
            start, end = int(s.get("start_line") or 0), int(s.get("end_line") or 0)
            if anchor_in_range(lines, start, end, str(s.get("anchor", ""))):
                kept.append({k: s.get(k) for k in ("title", "kind", "difficulty", "start_line", "end_line",
                                                   "anchor", "detail")})
            else:
                dropped.append(f"{s.get('title')}: cited lines {start}-{end} do not contain the anchor")
        return {"suggestions": kept, "dropped": dropped}

    return cache.get_or_compute("suggestions", f"{path}:{content_hash(text)}:{lang}", compute, refresh=refresh)
