"""Reading order for a newcomer: which files to read first, and why.

The model picks and orders 5-8 files from the non-test source files, given their one-line
descriptions and the import graph (both computed elsewhere and passed in). Every step is checked:
the file must be a candidate, must be connected by an import to an earlier step (so the route
follows the real structure), and the classes/functions it says to look for must be defined in
the file; their line numbers are taken from the file, not from the model.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import PurePosixPath

from reposense.core.cache import JsonCache
from reposense.core.files import is_test_path
from reposense.core.llm import LLM
from reposense.core.workspace import Workspace

CODE_SUFFIXES = {".py", ".js", ".jsx", ".mjs", ".ts", ".tsx", ".vue", ".svelte", ".go", ".rs", ".java", ".kt",
                 ".swift", ".rb", ".php", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".scala"}
MAX_CANDIDATES = 60
MAX_EDGES = 300
MAX_STEPS = 8

SYSTEM = """You plan a reading order for a developer who is new to the repository {repo} and wants to
understand how it works before contributing.
You get a README excerpt, candidate files (path, one-line description, lines, imported_by = how many
files import it, imports = how many files it imports) and import edges between them ("a -> b" means a imports b).
Pick 5-8 files and order them so each step builds on the previous ones: start with what a user of the
project meets first (public entry point, main concept), then the core objects, then the machinery they
rely on. Each file after the first should import, or be imported by, an earlier file. Skip trivial helpers.
Return JSON:
{{"steps": [{{"path": "<path exactly as given>", "why": "<why read it at this point, 1 sentence>",
             "look_for": ["<1-3 names of classes or functions defined in this file to start with>"]}}]}}
Write "why" in {lang}."""


def find_definition(text: str, name: str) -> int | None:
    """1-based line where `name` is defined as a class/function/constant, if anywhere."""
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", name):
        return None
    pattern = re.compile(
        rf"^\s*(export\s+)?(default\s+)?(async\s+)?(pub\s+)?"
        rf"((def|class|function|func|fn|interface|type|struct|enum|trait|const|let|var)\s+{re.escape(name)}\b"
        rf"|{re.escape(name)}\s*(=|:)(?!=))")
    for i, line in enumerate(text.splitlines(), 1):
        if pattern.match(line):
            return i
    return None


def candidates(edges: list[dict], summaries: dict[str, str], lines_of: dict[str, int]) -> list[dict]:
    imported_by = Counter(e["target"] for e in edges)
    imports = Counter(e["source"] for e in edges)
    files = [p for p in lines_of if PurePosixPath(p).suffix in CODE_SUFFIXES and not is_test_path(p)]
    # files many others import come first; size breaks ties
    files.sort(key=lambda p: -(2 * imported_by[p] + math.log1p(lines_of[p])))
    return [{"path": p, "description": summaries.get(p, ""), "lines": lines_of[p],
             "imported_by": imported_by[p], "imports": imports[p]} for p in files[:MAX_CANDIDATES]]


def verify(raw: dict, ws: Workspace, allowed: set[str], edges: list[dict]) -> dict:
    linked: dict[str, set[str]] = {}
    for e in edges:
        linked.setdefault(e["source"], set()).add(e["target"])
        linked.setdefault(e["target"], set()).add(e["source"])
    steps, dropped = [], []
    for s in (raw.get("steps") or [])[:MAX_STEPS * 2]:
        path = str(s.get("path", "")).strip("/")
        if path not in allowed:
            dropped.append(f"{path}: not a candidate file")
            continue
        if any(st["path"] == path for st in steps):
            continue
        earlier = [st["path"] for st in steps]
        via = next((p for p in reversed(earlier) if p in linked.get(path, set())), None)
        # files with no import relation at all (e.g. languages we cannot parse) are allowed anywhere
        if earlier and via is None and linked.get(path):
            dropped.append(f"{path}: no import relation with earlier steps")
            continue
        text = ws.read_text(path) or ""
        look_for = []
        for name in (s.get("look_for") or [])[:3]:
            line = find_definition(text, str(name))
            if line is None:
                dropped.append(f"{path}: {name} is not defined there")
            else:
                look_for.append({"name": str(name), "line": line})
        steps.append({"path": path, "why": str(s.get("why", "")), "look_for": look_for, "via": via})
        if len(steps) == MAX_STEPS:
            break
    return {"steps": steps, "dropped": dropped}


def build_reading_path(ws: Workspace, llm: LLM, cache: JsonCache, *, head: str, edges: list[dict],
                       summaries: dict[str, str], lang: str = "Chinese", refresh: bool = False) -> dict:
    def compute() -> dict:
        lines_of = {}
        for p in set(summaries) | {e["source"] for e in edges} | {e["target"] for e in edges}:
            text = ws.read_text(p)
            if text is not None:
                lines_of[p] = text.count("\n") + 1
        cands = candidates(edges, summaries, lines_of)
        allowed = {c["path"] for c in cands}
        inner = [f"{e['source']} -> {e['target']}" for e in sorted(edges, key=lambda e: -e["weight"])
                 if e["source"] in allowed and e["target"] in allowed][:MAX_EDGES]
        readme = next((ws.read_text(n) for n in ("README.md", "README.rst", "README") if (ws.root / n).is_file()), "")
        raw = llm.json(SYSTEM.format(repo=ws.repo, lang=lang), json.dumps(
            {"readme": (readme or "")[:2000], "files": cands, "imports": inner}, ensure_ascii=False))
        return verify(raw, ws, allowed, edges)

    return cache.get_or_compute("reading_path", f"{head}:{lang}", compute, refresh=refresh)
