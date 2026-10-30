"""One-line file descriptions (fake model, tiny git repo)."""

import subprocess

from reposense.analysis import file_summaries
from reposense.core.cache import JsonCache


class EchoLLM:
    """Describes every file it is sent, plus one path it was never given."""

    def __init__(self):
        self.calls = 0
        self.sent = []

    def json(self, system, user, **_):
        import json
        self.calls += 1
        paths = [f["path"] for f in json.loads(user)["files"]]
        self.sent += paths
        return {"files": {**{p: f"describes {p}" for p in paths}, "invented/ghost.py": "made up"}}


def test_outline_keeps_docstrings_and_definitions():
    src = '"""Command parsing."""\n\nclass Parser:\n    """Turns argv into values."""\n    def parse(self): ...\n\ndef helper(): ...\n'
    out = file_summaries.outline("pkg/parser.py", src)
    assert "Command parsing." in out and "class Parser: Turns argv into values." in out and "def helper()" in out
    assert "export function load" in file_summaries.outline("web/a.ts", "import x from 'y'\nexport function load() {}\n")


def test_doc_title_from_markdown_and_rst():
    assert file_summaries.doc_title("# Quickstart\n\ntext") == "Quickstart"
    assert file_summaries.doc_title("Options\n=======\n\ntext") == "Options"


def test_summaries_are_verified_and_cached(ws, tmp_path):
    (ws.root / "docs").mkdir()
    (ws.root / "docs" / "guide.md").write_text("# User guide\n\none\ntwo\nthree\nfour\n")
    subprocess.run(["git", "-C", str(ws.root), "add", "."], check=True, capture_output=True)
    cache = JsonCache("acme/pkg", root=tmp_path / "c")
    llm = EchoLLM()

    r = file_summaries.build_summaries(ws, llm, cache)
    assert r["summaries"]["src/pkg/core.py"] == "describes src/pkg/core.py"
    assert r["summaries"]["docs/guide.md"] == "User guide"          # no model needed
    assert "invented/ghost.py" not in r["summaries"] and r["dropped"] == 1
    assert "tests/test_core.py" not in llm.sent                      # too short to describe

    again = file_summaries.build_summaries(ws, llm, cache)
    assert llm.calls == 1 and again["summaries"] == r["summaries"]   # second run is served from the cache
