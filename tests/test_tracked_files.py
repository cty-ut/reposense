"""Lock files, snapshots, build output and linguist-marked files are not analysed as code (issue #20)."""

import subprocess

from reposense.core.files import is_skipped, tracked_files


def test_paths_that_are_never_code():
    for path in ["package-lock.json", "web/pnpm-lock.yaml", "go.sum", "Cargo.lock",
                 "web/dist/app.js", "src/__snapshots__/view.test.ts.snap", "ui/button.snap",
                 "web/node_modules/x/index.js", "third_party/vendor/lib.c"]:
        assert is_skipped(path), path
    for path in ["src/app.py", "build/gulpfile.js", "docs/dist-guide.md", "lockfile.py"]:
        assert not is_skipped(path), path


def test_gitattributes_linguist_markers(ws):
    files = {
        ".gitattributes": "gen/** linguist-generated\nthird/** linguist-vendored\ngen/keep.py linguist-generated=false\n",
        "gen/schema_pb2.py": "x = 1\n",
        "gen/keep.py": "x = 1\n",
        "third/lib.js": "x\n",
        "web/.gitattributes": "api.ts linguist-generated=true\n",
        "web/api.ts": "export {}\n",
        "web/app.ts": "export {}\n",
        "package-lock.json": "{}\n",
    }
    for path, text in files.items():
        (ws.root / path).parent.mkdir(parents=True, exist_ok=True)
        (ws.root / path).write_text(text)
    subprocess.run(["git", "-C", str(ws.root), "add", "."], check=True, capture_output=True)

    kept = set(tracked_files(ws))
    assert {"gen/keep.py", "web/app.ts", "src/pkg/core.py"} <= kept
    assert not kept & {"gen/schema_pb2.py", "third/lib.js", "web/api.ts", "package-lock.json"}
