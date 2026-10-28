"""Code exploration tools in the style of agentic search: glob / grep / read / ls on a local checkout.

No index is built. The model narrows down by itself: find candidate files, grep for symbols,
then read only the line ranges it needs.
"""

from __future__ import annotations

import fnmatch
import shutil
import subprocess

from reposense.workspace import Workspace, git

MAX_RESULTS = 60
READ_DEFAULT_LIMIT = 200
READ_MAX_LIMIT = 400


def _tracked_files(ws: Workspace) -> list[str]:
    return [f for f in git(ws.root, "ls-files").splitlines() if f]


def glob(ws: Workspace, pattern: str) -> dict:
    """Tracked files matching a glob such as '**/*.py' or 'src/click/*.py'."""
    files = _tracked_files(ws)
    pat = pattern.strip().lstrip("/")
    hits = [f for f in files if fnmatch.fnmatch(f, pat) or fnmatch.fnmatch(f, pat.replace("**/", ""))]
    return {"pattern": pattern, "total": len(hits), "files": hits[:MAX_RESULTS],
            "truncated": len(hits) > MAX_RESULTS}


def grep(ws: Workspace, pattern: str, path: str = "", file_glob: str = "", ignore_case: bool = False) -> dict:
    """Regex search over tracked files. Uses ripgrep when available, otherwise git grep."""
    target = str(ws.resolve(path)) if path else str(ws.root)
    if shutil.which("rg"):
        cmd = ["rg", "--line-number", "--no-heading", "--color=never", "--max-columns=240"]
        if ignore_case:
            cmd.append("-i")
        if file_glob:
            cmd += ["--glob", file_glob]
        cmd += ["--", pattern, target]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        prefix = str(ws.root) + "\\" if "\\" in target else str(ws.root) + "/"
        raw = [ln.replace(prefix, "", 1).replace("\\", "/") for ln in proc.stdout.splitlines()]
    else:
        args = ["grep", "-n", "-I", "-E"] + (["-i"] if ignore_case else []) + ["--", pattern]
        if path or file_glob:
            args += [f"{path.rstrip('/')}/{file_glob}" if path and file_glob else (path or file_glob)]
        raw = git(ws.root, *args, check=False).splitlines()

    matches = []
    for ln in raw:
        parts = ln.split(":", 2)
        if len(parts) == 3 and parts[1].isdigit():
            matches.append({"file": parts[0], "line": int(parts[1]), "text": parts[2].strip()[:200]})
    res = {"pattern": pattern, "total": len(matches), "matches": matches[:MAX_RESULTS],
           "truncated": len(matches) > MAX_RESULTS}
    if not matches:
        res["hint"] = "No matches. Try a shorter or broader pattern, ignore_case, or a different path."
    return res


def read(ws: Workspace, path: str, start_line: int = 1, end_line: int | None = None) -> dict:
    """Read lines start_line..end_line (1-based, inclusive) with line numbers; at most READ_MAX_LIMIT lines."""
    p = ws.resolve(path)
    if not p.is_file():
        return {"error": "not_found", "message": f"File not found: {path}"}
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, int(start_line))
    if start > len(lines):
        return {"error": "out_of_range", "message": f"{path} has only {len(lines)} lines"}
    end = int(end_line) if end_line else start + READ_DEFAULT_LIMIT - 1
    end = min(len(lines), max(start, end), start + READ_MAX_LIMIT - 1)
    body = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(start, end + 1))
    return {"path": ws.rel(p), "total_lines": len(lines), "start": start, "end": end, "content": body,
            "more": end < len(lines)}


def ls(ws: Workspace, directory: str = "") -> dict:
    """Immediate children (files and sub-directories) of a directory."""
    prefix = directory.strip().strip("/").removeprefix("./").strip(".")
    prefix = prefix + "/" if prefix else ""
    entries: set[str] = set()
    for f in _tracked_files(ws):
        if f.startswith(prefix):
            rest = f[len(prefix):]
            entries.add(rest.split("/", 1)[0] + ("/" if "/" in rest else ""))
    if not entries:
        return {"error": "not_found", "message": f"No such directory: {directory or '(root)'}"}
    return {"directory": directory or "", "entries": sorted(entries)[:200]}


def read_lines(ws: Workspace, path: str) -> list[str] | None:
    """Raw file lines, for citation verification."""
    try:
        p = ws.resolve(path)
    except ValueError:
        return None
    if not p.is_file():
        return None
    return p.read_text(encoding="utf-8", errors="replace").splitlines()
