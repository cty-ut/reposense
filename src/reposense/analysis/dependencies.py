"""Static dependency graph: which file imports which.

Edges come from parsing import statements: Python with `ast`, JavaScript/TypeScript from the relative
specifiers of import / export-from / require / import(). Only imports that resolve to a file in the
repository become edges; standard-library and third-party imports are ignored. No model is involved.
"""

from __future__ import annotations

import ast
import posixpath
import re
from collections import Counter
from pathlib import PurePosixPath

from reposense.core.cache import JsonCache
from reposense.core.files import tracked_files
from reposense.core.git import Git
from reposense.core.workspace import Workspace

PY_SUFFIXES = {".py", ".pyi"}
JS_SUFFIXES = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte"]
MAX_BYTES = 500_000

_JS_IMPORT = re.compile(
    r"""(?:import|export)\s[^'";]*?from\s*['"]([^'"]+)['"]"""   # import x from '...'; export * from '...'
    r"""|import\s*\(\s*['"]([^'"]+)['"]\s*\)"""                 # import('...')
    r"""|require\s*\(\s*['"]([^'"]+)['"]\s*\)"""                # require('...')
    r"""|import\s+['"]([^'"]+)['"]"""                           # import '...'
)


# ---------------------------------------------------------------- Python

class PythonModules:
    """Maps dotted module names to files the way Python would see them from the repository."""

    def __init__(self, files: list[str]):
        packages = {posixpath.dirname(f) for f in files if PurePosixPath(f).name in ("__init__.py", "__init__.pyi")}
        self.packaged: dict[str, str] = {}      # "click.core" -> "src/click/core.py"
        self.loose: dict[tuple[str, str], str] = {}  # ("tests", "conftest") -> "tests/conftest.py"
        self.name_of: dict[str, list[str]] = {}  # file -> module parts (packaged files only)
        for f in sorted(files, key=lambda p: (p.endswith(".pyi"), p)):  # prefer .py over .pyi
            parent, stem = posixpath.dirname(f), PurePosixPath(f).stem
            if parent not in packages:
                self.loose.setdefault((parent, stem), f)
                continue
            root = parent
            while root in packages:
                root = posixpath.dirname(root)
            pkg = parent[len(root):].strip("/").split("/")
            parts = pkg if stem == "__init__" else [*pkg, stem]
            self.name_of[f] = parts
            self.packaged.setdefault(".".join(parts), f)

    def lookup(self, parts: list[str], importer: str) -> str | None:
        """Longest prefix of `parts` that is a module in the repository."""
        for k in range(len(parts), 0, -1):
            hit = self.packaged.get(".".join(parts[:k]))
            if hit:
                return hit
        return self.loose.get((posixpath.dirname(importer), parts[0])) if parts else None

    def package_of(self, f: str) -> list[str] | None:
        parts = self.name_of.get(f)
        if parts is None:
            return None
        return parts if PurePosixPath(f).stem == "__init__" else parts[:-1]


def _parse(source: str) -> ast.Module | None:
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError):
        return None


def _from_imports(path: str, tree: ast.Module, modules: PythonModules) -> list[tuple[str, str]]:
    """(imported name, file it resolves to) for every `from … import name` in the file."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            pkg = modules.package_of(path)
            if pkg is None or node.level - 1 >= len(pkg):  # beyond the top-level package
                continue
            base = pkg[:len(pkg) - (node.level - 1)]
        else:
            base = []
        full = base + (node.module.split(".") if node.module else [])
        for alias in node.names:
            # `from pkg import mod` imports a submodule; `from mod import name` imports from mod itself
            sub = modules.packaged.get(".".join([*full, alias.name])) if alias.name != "*" else None
            target = sub or (modules.lookup(full, path) if full else None)
            if target:
                out.append((alias.asname or alias.name, target))
    return out


def package_exports(sources: dict[str, str], modules: PythonModules) -> dict[str, dict[str, str]]:
    """For each `__init__.py`: re-exported name -> defining file (`from .core import Option`)."""
    exports = {}
    for f, source in sources.items():
        if PurePosixPath(f).stem == "__init__" and (tree := _parse(source)):
            exports[f] = {name: target for name, target in _from_imports(f, tree, modules) if target != f}
    return exports


def python_imports(path: str, source: str, modules: PythonModules,
                   exports: dict[str, dict[str, str]] | None = None) -> list[str]:
    tree = _parse(source)
    if tree is None:
        return []
    exports = exports or {}
    targets: list[str] = []
    bound: dict[str, str] = {}  # local name -> package __init__ it refers to (`import click`)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = modules.lookup(alias.name.split("."), path)
                targets.append(target)
                if target in exports and "." not in alias.name:
                    bound[alias.asname or alias.name] = target
    for name, target in _from_imports(path, tree, modules):
        # `from click import Option` really depends on click/core.py, where Option is defined
        targets.append(exports.get(target, {}).get(name, target))
    if bound:
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in bound:
                if defined := exports[bound[node.value.id]].get(node.attr):
                    targets.append(defined)  # `click.Option(...)`
    return [t for t in targets if t and t != path]


# ---------------------------------------------------------------- JavaScript / TypeScript

def js_imports(path: str, source: str, files: set[str]) -> list[str]:
    targets = []
    for m in _JS_IMPORT.finditer(source):
        spec = next(g for g in m.groups() if g)
        if not spec.startswith("."):
            continue  # package import
        base = posixpath.normpath(posixpath.join(posixpath.dirname(path), spec))
        stem = re.sub(r"\.(m|c)?js$", "", base)  # TS sources import "./x.js" for "./x.ts"
        candidates = [base, *(stem + s for s in JS_SUFFIXES), *(f"{base}/index{s}" for s in JS_SUFFIXES)]
        hit = next((c for c in candidates if c in files), None)
        if hit and hit != path:
            targets.append(hit)
    return targets


# ---------------------------------------------------------------- graph

def build_dependencies(ws: Workspace, cache: JsonCache, *, refresh: bool = False) -> dict:
    head = Git(ws.root).head()

    def compute() -> dict:
        files = tracked_files(ws)
        file_set = set(files)
        sources = {}
        for f in files:
            p = ws.root / f
            if (PurePosixPath(f).suffix in PY_SUFFIXES or PurePosixPath(f).suffix in JS_SUFFIXES) \
                    and p.is_file() and p.stat().st_size <= MAX_BYTES:
                sources[f] = p.read_text(encoding="utf-8", errors="replace")
        modules = PythonModules([f for f in sources if PurePosixPath(f).suffix in PY_SUFFIXES])
        exports = package_exports(sources, modules)
        edges: Counter[tuple[str, str]] = Counter()
        for f, source in sources.items():
            if PurePosixPath(f).suffix in PY_SUFFIXES:
                targets = python_imports(f, source, modules, exports)
            else:
                targets = js_imports(f, source, file_set)
            for t in targets:
                edges[(f, t)] += 1
        return {
            "head": head,
            "parsed": len(sources),
            "edges": [{"source": s, "target": t, "weight": w} for (s, t), w in sorted(edges.items())],
        }

    return cache.get_or_compute("dependencies", head, compute, refresh=refresh)
