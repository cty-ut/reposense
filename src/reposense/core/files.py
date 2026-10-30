"""Which tracked files the analyses look at (shared so the map and the dependency graph agree)."""

from __future__ import annotations

import re

from reposense.core.git import Git
from reposense.core.workspace import Workspace

SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2", ".ttf", ".eot", ".pdf",
                 ".zip", ".gz", ".lock", ".min.js", ".map", ".pyc", ".so", ".dll", ".exe", ".bin"}
MAX_FILES = 20_000
# keep in sync with isTestFile in vscode-extension/src/webview/map/graphLayout.ts
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|testing)/|(^|/)test_[^/]+\.py$|_test\.(py|go)$"
                        r"|\.(test|spec)\.[cm]?[jt]sx?$")


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path))


def is_skipped(path: str) -> bool:
    low = path.lower()
    return any(low.endswith(s) for s in SKIP_SUFFIXES) or "/node_modules/" in f"/{low}" or "/vendor/" in f"/{low}"


def tracked_files(ws: Workspace) -> list[str]:
    """Tracked files minus binaries and vendored code, capped at MAX_FILES."""
    return [f for f in Git(ws.root).ls_files() if not is_skipped(f)][:MAX_FILES]
