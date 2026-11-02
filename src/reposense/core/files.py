"""Which tracked files the analyses look at (shared so the map and the dependency graph agree)."""

from __future__ import annotations

import re
from collections.abc import Iterable

from reposense.core.git import Git
from reposense.core.workspace import Workspace

SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2", ".ttf", ".eot", ".pdf",
                 ".zip", ".gz", ".lock", ".min.js", ".map", ".pyc", ".so", ".dll", ".exe", ".bin"}
MAX_FILES = 20_000
# Test files are recognised two ways: by name (test_*.py, *_test.go, *.test.ts, ...) and by living
# in a directory called test/tests/__tests__/spec/testing. This module is the only place that
# knows the rules: the backend flags every file with is_test (see analysis/structure.py) and the
# frontend reads that flag instead of keeping its own copy of the patterns.
_TEST_NAME = re.compile(r"(^|/)test_[^/]+\.py$|_test\.(py|go)$|\.(test|spec)\.[cm]?[jt]sx?$")
_TEST_DIRS = {"test", "tests", "__tests__", "spec", "testing"}


def _package_dirs(paths: Iterable[str]) -> set[str]:
    """Directories that are Python packages, i.e. contain an __init__.py (repo root excluded)."""
    return {p[: -len("/__init__.py")] for p in paths if p.endswith("/__init__.py")}


def _is_test(path: str, package_dirs: set[str]) -> bool:
    if _TEST_NAME.search(path):
        return True
    parts = path.split("/")
    for i, part in enumerate(parts[:-1]):
        if part in _TEST_DIRS:
            # A test-named directory inside a source package is the framework's own code, not
            # the project's tests: django/test/ holds Django's testing framework because
            # django/ is a package, while a top-level tests/ is a real test directory.
            return "/".join(parts[:i]) not in package_dirs
    return False


def is_test_path(path: str, paths: Iterable[str] | None = None) -> bool:
    """Whether `path` is a test file. Pass the repository's file list as `paths` when known:
    telling a package's own test framework (django/test/) apart from a real test directory
    (tests/) requires knowing which directories are packages."""
    return _is_test(path, _package_dirs(paths) if paths is not None else set())


def test_paths(paths: Iterable[str]) -> set[str]:
    """The subset of `paths` that are test files, judging each against the whole list."""
    paths = list(paths)
    package_dirs = _package_dirs(paths)
    return {p for p in paths if _is_test(p, package_dirs)}


def is_skipped(path: str) -> bool:
    low = path.lower()
    return any(low.endswith(s) for s in SKIP_SUFFIXES) or "/node_modules/" in f"/{low}" or "/vendor/" in f"/{low}"


def tracked_files(ws: Workspace) -> list[str]:
    """Tracked files minus binaries and vendored code, capped at MAX_FILES."""
    return [f for f in Git(ws.root).ls_files() if not is_skipped(f)][:MAX_FILES]
