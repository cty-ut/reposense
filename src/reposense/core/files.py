"""Which tracked files the analyses look at (shared so the map and the dependency graph agree)."""

from __future__ import annotations

import re
from collections.abc import Iterable

from reposense.core.git import Git
from reposense.core.workspace import Workspace

SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2", ".ttf", ".eot", ".pdf",
                 ".zip", ".gz", ".lock", ".min.js", ".map", ".pyc", ".so", ".dll", ".exe", ".bin"}
MAX_FILES = 20_000
# Not code even though they are text: dependency lock files, test snapshots, build output.
LOCK_FILES = {"package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb",
              "poetry.lock", "Pipfile.lock", "uv.lock", "Cargo.lock", "Gemfile.lock", "composer.lock",
              "go.sum", "mix.lock", "pubspec.lock", "Podfile.lock", "flake.lock"}
GENERATED_DIRS = ("node_modules", "vendor", "dist", "__snapshots__")  # not "build": often real scripts
# GitHub's own markers for generated and vendored files (.gitattributes)
LINGUIST_ATTRS = ["linguist-generated", "linguist-vendored"]
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
    """Binaries, lock files, snapshots and build output, judged by the path alone."""
    low = path.lower()
    name = path.rsplit("/", 1)[-1]
    return (any(low.endswith(s) for s in SKIP_SUFFIXES) or low.endswith(".snap") or name in LOCK_FILES
            or any(f"/{d}/" in f"/{low}" for d in GENERATED_DIRS))


def _marked_generated(git: Git, paths: list[str]) -> set[str]:
    """Files .gitattributes marks as linguist-generated or linguist-vendored (and not explicitly false)."""
    if not any(p == ".gitattributes" or p.endswith("/.gitattributes") for p in paths):
        return set()  # nothing can be marked: skip the git call
    attrs = git.attributes(paths, LINGUIST_ATTRS)
    return {p for p, a in attrs.items()
            if any(a.get(n) not in (None, "unspecified", "unset", "false") for n in LINGUIST_ATTRS)}


def tracked_files(ws: Workspace) -> list[str]:
    """Tracked files minus binaries, lock files, generated and vendored code, capped at MAX_FILES."""
    git = Git(ws.root)
    files = [f for f in git.ls_files() if not is_skipped(f)]
    marked = _marked_generated(git, files)
    return [f for f in files if f not in marked][:MAX_FILES]
