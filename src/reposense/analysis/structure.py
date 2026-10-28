"""Repository map: repo → architecture layers → directories → files.

Facts (line counts, languages, change frequency) are computed from the checkout. The model is only
asked to group top-level paths into architecture layers and describe modules; every path it returns
is checked against the real file tree, and anything unknown is dropped.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from reposense.core.cache import JsonCache
from reposense.core.git import Git
from reposense.core.llm import LLM
from reposense.core.workspace import Workspace

LANGUAGES = {
    ".py": "Python", ".pyi": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".vue": "Vue", ".svelte": "Svelte",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".swift": "Swift", ".rb": "Ruby",
    ".php": "PHP", ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".hpp": "C++", ".cs": "C#",
    ".scala": "Scala", ".sh": "Shell", ".html": "HTML", ".css": "CSS", ".scss": "CSS", ".md": "Markdown",
    ".rst": "reStructuredText", ".yml": "YAML", ".yaml": "YAML", ".toml": "TOML", ".json": "JSON", ".sql": "SQL",
}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2", ".ttf", ".eot", ".pdf",
                 ".zip", ".gz", ".lock", ".min.js", ".map", ".pyc", ".so", ".dll", ".exe", ".bin"}
LAYER_KINDS = ["frontend", "backend", "core", "cli", "api", "data", "tests", "docs", "examples", "infra",
               "tooling", "other"]
MAX_FILES = 20_000

SYSTEM = """You are mapping the architecture of a code repository for a newcomer.
Group the given paths into 3-8 architecture layers, and describe the important modules.

Return JSON:
{
  "summary": "what this project is, in 1-2 sentences",
  "layers": [{"id": "short-kebab-id", "name": "display name", "kind": one of %s,
              "description": "what this layer does, 1 sentence", "paths": ["paths from the input that belong here"]}],
  "modules": {"<directory path from the input>": "what this directory contains, 1 short sentence"}
}
Rules: only use paths that appear in the input; a path belongs to at most one layer; prefer the most
specific paths (e.g. "src/app/frontend" rather than "src" when they differ in role). Write text in %s."""


@dataclass
class _Node:
    name: str
    path: str
    is_file: bool = False
    lines: int = 0
    churn: int = 0
    language: str | None = None
    children: dict[str, "_Node"] = field(default_factory=dict)


def _language(path: str) -> str | None:
    return LANGUAGES.get(PurePosixPath(path).suffix.lower())


def _skip(path: str) -> bool:
    low = path.lower()
    return any(low.endswith(s) for s in SKIP_SUFFIXES) or "/node_modules/" in f"/{low}" or "/vendor/" in f"/{low}"


def _count_lines(ws: Workspace, path: str) -> int:
    try:
        data = (ws.root / path).read_bytes()
    except OSError:
        return 0
    if b"\x00" in data[:8000]:
        return 0
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def collect_files(ws: Workspace) -> list[dict]:
    """Tracked text files with line count, language and 12-month change count."""
    git = Git(ws.root)
    churn = git.churn("12 months ago")
    files = [f for f in git.ls_files() if not _skip(f)][:MAX_FILES]
    return [{"path": f, "lines": _count_lines(ws, f), "churn": churn.get(f, 0), "language": _language(f)}
            for f in files]


def _dir_overview(files: list[dict], depth: int = 3) -> list[dict]:
    """Directories up to `depth` with file counts, languages and sample names — the model's input."""
    stats: dict[str, dict] = {}
    for f in files:
        parts = f["path"].split("/")
        for d in range(1, min(depth, len(parts) - 1) + 1):
            key = "/".join(parts[:d])
            s = stats.setdefault(key, {"path": key, "files": 0, "lines": 0, "languages": Counter(), "samples": []})
            s["files"] += 1
            s["lines"] += f["lines"]
            if f["language"]:
                s["languages"][f["language"]] += 1
            if d == len(parts) - 1 and len(s["samples"]) < 6:
                s["samples"].append(parts[-1])
        if len(parts) == 1:  # top-level files are candidates too
            stats[f["path"]] = {"path": f["path"], "files": 1, "lines": f["lines"], "languages": Counter(),
                                "samples": []}
    out = []
    for s in sorted(stats.values(), key=lambda s: s["path"]):
        if s["files"] >= 1:
            out.append({"path": s["path"], "files": s["files"], "lines": s["lines"],
                        "languages": [lang for lang, _ in s["languages"].most_common(3)], "samples": s["samples"]})
    return out[:400]


def _heuristic_layers(files: list[dict]) -> dict:
    """Fallback when the model is unavailable: classify by well-known top-level names."""
    rules = [("tests", "tests", ("test", "tests", "spec", "__tests__")),
             ("docs", "docs", ("doc", "docs", "documentation")),
             ("examples", "examples", ("example", "examples", "samples")),
             ("infra", "infra", (".github", ".circleci", "docker", "deploy", "scripts", "ci")),
             ("frontend", "frontend", ("web", "frontend", "client", "ui", "app")),
             ("core", "core", ("src", "lib", "pkg", "server", "backend"))]
    layers: dict[str, dict] = {}
    for top in sorted({f["path"].split("/")[0] for f in files if "/" in f["path"]}):
        for lid, kind, names in rules:
            if top.lower() in names:
                layers.setdefault(lid, {"id": lid, "name": lid.title(), "kind": kind, "description": "",
                                        "paths": []})["paths"].append(top)
                break
    return {"summary": "", "layers": list(layers.values()), "modules": {}}


def _validate_layout(raw: dict, known_paths: set[str]) -> dict:
    """Keep only layers/paths/modules that exist in the repository; a path is claimed by one layer."""
    claimed: set[str] = set()
    layers = []
    for i, layer in enumerate(raw.get("layers") or []):
        paths = [p.strip("/") for p in layer.get("paths") or [] if p.strip("/") in known_paths]
        paths = [p for p in paths if p not in claimed]
        claimed.update(paths)
        if not paths:
            continue
        kind = layer.get("kind") if layer.get("kind") in LAYER_KINDS else "other"
        layers.append({"id": str(layer.get("id") or f"layer-{i}")[:40], "name": str(layer.get("name") or kind)[:40],
                       "kind": kind, "description": str(layer.get("description") or "")[:300], "paths": paths})
    modules = {p.strip("/"): str(d)[:200] for p, d in (raw.get("modules") or {}).items() if p.strip("/") in known_paths}
    return {"summary": str(raw.get("summary") or "")[:500], "layers": layers, "modules": modules}


def _layer_of(path: str, prefixes: list[tuple[str, str]]) -> str:
    """Layer id by longest matching path prefix ('other' when none matches)."""
    for prefix, lid in prefixes:
        if path == prefix or path.startswith(prefix + "/"):
            return lid
    return "other"


def _build_tree(files: list[dict], base: str = "") -> _Node:
    root = _Node(name=base or "/", path=base)
    for f in files:
        parts = f["path"].split("/")
        node = root
        for i, part in enumerate(parts[:-1]):
            node = node.children.setdefault(part, _Node(name=part, path="/".join(parts[: i + 1])))
        node.children[parts[-1]] = _Node(name=parts[-1], path=f["path"], is_file=True, lines=f["lines"],
                                         churn=f["churn"], language=f["language"])
    return root


def _collapse(node: _Node) -> _Node:
    """Merge single-child directory chains: src → click becomes 'src/click'."""
    for key, child in list(node.children.items()):
        node.children[key] = _collapse(child)
    if not node.is_file and len(node.children) == 1:
        (only,) = node.children.values()
        if not only.is_file and node.path:
            only.name = f"{node.name}/{only.name}"
            return only
    return node


def _to_json(node: _Node, layer: str, modules: dict[str, str]) -> dict:
    if node.is_file:
        return {"id": node.path, "name": node.name, "path": node.path, "kind": "file", "layer": layer,
                "lines": node.lines, "files": 1, "churn": node.churn, "language": node.language}
    children = sorted((_to_json(c, layer, modules) for c in node.children.values()),
                      key=lambda c: (c["kind"] == "file", -c["lines"]))
    langs = Counter()
    for c in children:
        if c.get("language"):
            langs[c["language"]] += c["lines"]
        for lang, n in (c.get("languages") or {}).items():
            langs[lang] += n
    return {"id": node.path, "name": node.name, "path": node.path, "kind": "dir", "layer": layer,
            "description": modules.get(node.path), "lines": sum(c["lines"] for c in children),
            "files": sum(c["files"] for c in children), "churn": sum(c["churn"] for c in children),
            "languages": dict(langs.most_common(4)), "children": children}


def assemble(repo: str, head: str, files: list[dict], layout: dict) -> dict:
    """Combine file facts with a validated layout into the map tree."""
    prefixes = sorted(((p, layer["id"]) for layer in layout["layers"] for p in layer["paths"]),
                      key=lambda x: -len(x[0]))
    by_layer: dict[str, list[dict]] = {}
    for f in files:
        by_layer.setdefault(_layer_of(f["path"], prefixes), []).append(f)

    meta = {layer["id"]: layer for layer in layout["layers"]}
    if "other" in by_layer:
        meta["other"] = {"id": "other", "name": "Other", "kind": "other", "description": "", "paths": []}

    layer_nodes = []
    for lid, lfiles in by_layer.items():
        tree = _collapse(_build_tree(lfiles))
        content = _to_json(tree, lid, layout["modules"])
        m = meta[lid]
        layer_nodes.append({**content, "id": f"layer:{lid}", "name": m["name"], "path": "", "kind": "layer",
                            "layer": lid, "layer_kind": m["kind"], "description": m["description"]})
    layer_nodes.sort(key=lambda n: (n["layer"] == "other", -n["lines"]))
    return {
        "repo": repo, "head": head, "summary": layout["summary"],
        "root": {"id": "repo", "name": repo, "path": "", "kind": "repo", "layer": None,
                 "description": layout["summary"], "lines": sum(n["lines"] for n in layer_nodes),
                 "files": sum(n["files"] for n in layer_nodes), "churn": sum(n["churn"] for n in layer_nodes),
                 "children": layer_nodes},
    }


def build_structure(ws: Workspace, llm: LLM | None, cache: JsonCache, *, lang: str = "Chinese",
                    refresh: bool = False) -> dict:
    head = Git(ws.root).head()

    def compute() -> dict:
        files = collect_files(ws)
        overview = _dir_overview(files)
        known = {d["path"] for d in overview}
        readme = next((ws.read_text(n) for n in ("README.md", "README.rst", "README") if (ws.root / n).is_file()), "")
        layout = None
        if llm is not None:
            try:
                raw = llm.json(SYSTEM % (LAYER_KINDS, lang), json.dumps(
                    {"repository": ws.repo, "readme": (readme or "")[:2500], "paths": overview}, ensure_ascii=False))
                layout = _validate_layout(raw, known)
            except Exception:
                layout = None
        if not layout or not layout["layers"]:
            layout = _validate_layout(_heuristic_layers(files), known)
        return assemble(ws.repo, head, files, layout)

    return cache.get_or_compute("structure", f"{head}:{lang}", compute, refresh=refresh)
