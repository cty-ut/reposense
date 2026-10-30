"""One-line description for every file, shown on the map, the graph and in tooltips.

The model only sees a cheap outline of each file (docstrings, classes, functions), in batches grouped
by directory. Every description is keyed by a path we sent; anything else is dropped. Results are
cached per file content, so after a pull only changed files are described again. Markdown and
reStructuredText pages use their first heading and need no model.
"""

from __future__ import annotations

import ast
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import PurePosixPath
from typing import Callable

from reposense.core.cache import JsonCache, content_hash
from reposense.core.files import tracked_files
from reposense.core.llm import LLM
from reposense.core.workspace import Workspace

CODE_SUFFIXES = {".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte", ".go", ".rs",
                 ".java", ".kt", ".swift", ".rb", ".php", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".scala", ".sh"}
DOC_SUFFIXES = {".md", ".rst"}
MAX_FILES = 600           # biggest files first; keeps the first run within about a minute
MIN_LINES = 5             # nearly empty files (e.g. bare __init__.py) are not worth a description
BATCH_FILES = 30
BATCH_CHARS = 14_000
OUTLINE_CHARS = 1_500
MAX_SUMMARY = 120

SYSTEM = """You write one-line descriptions of files for a developer who is new to the repository {repo}.
For each file you get its path and an outline (docstrings, classes, functions). Return JSON:
{{"files": {{"<path exactly as given>": "<what the file is responsible for, one short sentence>"}}}}
Describe the responsibility, not the implementation, e.g. "Parses command-line arguments into option values".
Do not start with the file name. Keep each description under 25 words (or 40 Chinese characters).
Only use paths from the input. Write in {lang}."""

_SIGNATURE = re.compile(
    r"^\s*(export\s+)?(default\s+)?(async\s+)?(abstract\s+)?"
    r"(function|class|interface|type|enum|const|def|func|fn|struct|impl|trait|module|public|protected)\b")
_COMMENT = re.compile(r"^\s*(//|#|/?\*|<!--)")
_HEADING = re.compile(r"^\s*#{1,3}\s+(.+?)\s*#*\s*$")


def _first_line(doc: str | None) -> str:
    return (doc or "").strip().splitlines()[0][:160] if (doc or "").strip() else ""


def outline(path: str, text: str) -> str:
    """Docstrings and top-level definitions: enough to say what a file is for, cheap to send."""
    if PurePosixPath(path).suffix in (".py", ".pyi"):
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            tree = None
        if tree is not None:
            parts = [_first_line(ast.get_docstring(tree))]
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                    parts.append(f"class {node.name}: {_first_line(ast.get_docstring(node))} "
                                 f"methods: {', '.join(methods[:10])}")
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    parts.append(f"def {node.name}(): {_first_line(ast.get_docstring(node))}")
            return "\n".join(p for p in parts if p)[:OUTLINE_CHARS]
    lines = text.splitlines()
    head = [ln.strip() for ln in lines[:12] if _COMMENT.match(ln)][:5]
    sigs = [ln.strip()[:120] for ln in lines if _SIGNATURE.match(ln)][:30]
    return "\n".join(head + sigs)[:OUTLINE_CHARS] or "\n".join(lines[:15])[:OUTLINE_CHARS]


def doc_title(text: str) -> str | None:
    lines = text.splitlines()
    for i, ln in enumerate(lines[:30]):
        if m := _HEADING.match(ln):
            return m.group(1)[:MAX_SUMMARY]
        # reStructuredText: a title line underlined with = - ~ etc.
        if i + 1 < len(lines) and ln.strip() and re.fullmatch(r"([=\-~^*])\1{2,}", lines[i + 1].strip()):
            return ln.strip()[:MAX_SUMMARY]
    return None


def _batches(items: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """Group (path, outline) pairs in path order so each batch covers neighbouring files."""
    batches: list[list[tuple[str, str]]] = [[]]
    size = 0
    for path, text in sorted(items):
        if batches[-1] and (len(batches[-1]) >= BATCH_FILES or size + len(text) > BATCH_CHARS):
            batches.append([])
            size = 0
        batches[-1].append((path, text))
        size += len(text)
    return [b for b in batches if b]


def verify(raw: dict, sent: set[str]) -> tuple[dict[str, str], int]:
    """Keep descriptions for paths we actually sent; count the rest as dropped."""
    files = raw.get("files") if isinstance(raw.get("files"), dict) else {}
    kept, dropped = {}, 0
    for path, text in files.items():
        if path in sent and isinstance(text, str) and text.strip():
            kept[path] = text.strip()[:MAX_SUMMARY]
        else:
            dropped += 1
    return kept, dropped


def build_summaries(ws: Workspace, llm: LLM, cache: JsonCache, *, lang: str = "Chinese", refresh: bool = False,
                    on_progress: Callable[[int, int], None] | None = None) -> dict:
    summaries: dict[str, str] = {}
    pending: dict[str, tuple[str, str]] = {}  # path -> (cache key, outline)
    candidates = []
    for f in tracked_files(ws):
        suffix = PurePosixPath(f).suffix.lower()
        if suffix not in CODE_SUFFIXES and suffix not in DOC_SUFFIXES:
            continue
        text = ws.read_text(f)
        if text is None or text.count("\n") < MIN_LINES - 1:
            continue
        if suffix in DOC_SUFFIXES:
            if title := doc_title(text):
                summaries[f] = title
            continue
        candidates.append((f, text))

    candidates.sort(key=lambda c: -c[1].count("\n"))
    skipped = max(0, len(candidates) - MAX_FILES)
    for f, text in candidates[:MAX_FILES]:
        key = f"{f}:{content_hash(text)}:{lang}"
        hit = None if refresh else cache.get("summary", key)
        if hit is not None:
            summaries[f] = hit["summary"]
        else:
            pending[f] = (key, outline(f, text))

    batches = _batches([(f, o) for f, (_, o) in pending.items()])
    dropped, errors, done = 0, [], 0

    def describe(batch: list[tuple[str, str]]) -> tuple[dict[str, str], int]:
        raw = llm.json(SYSTEM.format(repo=ws.repo, lang=lang),
                       json.dumps({"files": [{"path": p, "outline": o} for p, o in batch]}, ensure_ascii=False))
        return verify(raw, {p for p, _ in batch})

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(describe, b) for b in batches]
        for future in as_completed(futures):
            done += 1
            try:
                kept, n = future.result()
            except Exception as e:  # one failed batch should not lose the others
                errors.append(f"{type(e).__name__}: {e}")
                continue
            dropped += n
            for path, text in kept.items():
                summaries[path] = text
                cache.set("summary", pending[path][0], {"summary": text})
            if on_progress:
                on_progress(done, len(batches))

    if batches and len(errors) == len(batches):
        raise RuntimeError(f"Could not describe files: {errors[0]}")
    return {"summaries": summaries, "dropped": dropped, "skipped": skipped, "failed_batches": len(errors)}
