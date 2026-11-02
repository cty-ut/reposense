"""Test-file detection: a package's own test framework is not the project's tests (issue #19)."""

import subprocess

from reposense.analysis import reading_path, structure
from reposense.core.cache import JsonCache
from reposense.core.files import is_test_path
from reposense.core.files import test_paths as repo_test_paths  # aliased: pytest would collect it

# The interesting shapes of a Django checkout: django/ is a package, and django/test/ inside it
# is Django's testing framework (TestCase, Client), while the repo's own tests live in tests/.
DJANGO = [
    "django/__init__.py",
    "django/test/__init__.py",
    "django/test/client.py",
    "django/test/testcases.py",
    "django/test/utils.py",
    "django/core/__init__.py",
    "django/core/handlers/base.py",
    "tests/basic/models.py",
    "tests/test_queries.py",
]


def test_framework_source_inside_a_package_is_not_a_test():
    tests = repo_test_paths(DJANGO)
    assert "django/test/client.py" not in tests
    assert "django/test/testcases.py" not in tests
    assert "django/test/utils.py" not in tests


def test_top_level_tests_directory_still_counts():
    tests = repo_test_paths(DJANGO)
    assert "tests/basic/models.py" in tests  # by directory; the name alone would not match
    assert "tests/test_queries.py" in tests


def test_filename_rules_are_unchanged():
    names = ["pkg/test_client.py", "pkg/client_test.py", "pkg/client_test.go",
             "web/app.test.ts", "web/app.spec.tsx", "django/test/test_signals.py"]
    assert repo_test_paths(DJANGO + names) >= set(names)  # name rules apply even inside a package


def test_the_package_marker_is_what_makes_the_difference():
    # Without the repository's file list the parent package is invisible, so the bare
    # path still looks like a test file; with the list, django/ being a package decides it.
    assert is_test_path("django/test/client.py")
    assert not is_test_path("django/test/client.py", DJANGO)
    assert is_test_path("tests/basic/models.py", DJANGO)


def test_test_dir_inside_a_plain_directory_still_counts():
    paths = ["src/app/tests/views.py", "lib/testing/helpers.py", "web/__tests__/app.ts"]
    assert repo_test_paths(paths) == set(paths)


def test_nested_package_test_dir_is_framework_code():
    paths = ["a/__init__.py", "a/b/__init__.py", "a/b/test/helpers.py", "a/b/spec/data.py"]
    assert repo_test_paths(paths) == set()


def test_reading_path_candidates_include_framework_source_but_not_tests():
    lines_of = {p: 10 for p in DJANGO}
    cands = {c["path"] for c in reading_path.candidates([], {}, lines_of)}
    assert "django/test/client.py" in cands
    assert "django/test/testcases.py" in cands
    assert "tests/basic/models.py" not in cands
    assert "tests/test_queries.py" not in cands


def test_structure_ships_the_flag_to_the_frontend(ws, tmp_path):
    for rel in ("django/__init__.py", "django/test/__init__.py"):
        p = ws.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("")
    (ws.root / "django" / "test" / "client.py").write_text("class Client:\n    pass\n")
    subprocess.run(["git", "-C", str(ws.root), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(ws.root), "commit", "-q", "-m", "vendored framework"],
                   check=True, capture_output=True)

    s = structure.build_structure(ws, llm=None, cache=JsonCache("acme/pkg", root=tmp_path / "c"))
    flags = {}

    def walk(node):
        if node["kind"] == "file":
            flags[node["path"]] = node["is_test"]
        for c in node.get("children") or []:
            walk(c)

    walk(s["root"])
    assert flags["django/test/client.py"] is False
    assert flags["tests/test_core.py"] is True
    assert flags["src/pkg/core.py"] is False
