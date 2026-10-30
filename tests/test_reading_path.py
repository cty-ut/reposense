"""Reading order: every step is checked against the files and the import graph (fake model)."""

from reposense.analysis import reading_path
from reposense.core.cache import JsonCache
from reposense.core.workspace import Workspace


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def json(self, system, user, **_):
        self.calls += 1
        return self.reply


def make_ws(tmp_path):
    files = {
        "pkg/api.py": "from pkg.core import Engine\n\ndef run():\n    return Engine()\n",
        "pkg/core.py": "from pkg.util import helper\n\nclass Engine:\n    pass\n",
        "pkg/util.py": "def helper():\n    pass\n",
        "pkg/plugins.py": "LOADED = []\n",
        "tests/test_api.py": "from pkg.api import run\n",
    }
    for path, text in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text)
    edges = [{"source": "pkg/api.py", "target": "pkg/core.py", "weight": 1},
             {"source": "pkg/core.py", "target": "pkg/util.py", "weight": 1},
             {"source": "tests/test_api.py", "target": "pkg/api.py", "weight": 1},
             {"source": "pkg/plugins.py", "target": "pkg/util.py", "weight": 1}]
    return Workspace(repo="acme/pkg", root=tmp_path), edges


def test_steps_are_verified(tmp_path):
    ws, edges = make_ws(tmp_path)
    llm = FakeLLM({"steps": [
        {"path": "pkg/api.py", "why": "entry point", "look_for": ["run", "invented"]},
        {"path": "tests/test_api.py", "why": "tests are not candidates"},
        {"path": "pkg/plugins.py", "why": "jumps somewhere unrelated"},
        {"path": "pkg/core.py", "why": "what run builds", "look_for": ["Engine"]},
        {"path": "pkg/util.py", "why": "used by core"},
    ]})
    summaries = {"pkg/api.py": "Public entry point"}
    r = reading_path.build_reading_path(ws, llm, JsonCache("acme/pkg", root=tmp_path / "c"), head="h1",
                                        edges=edges, summaries=summaries)
    assert [s["path"] for s in r["steps"]] == ["pkg/api.py", "pkg/core.py", "pkg/util.py"]
    assert r["steps"][0]["look_for"] == [{"name": "run", "line": 3}]
    assert r["steps"][1] == {"path": "pkg/core.py", "why": "what run builds",
                             "look_for": [{"name": "Engine", "line": 3}], "via": "pkg/api.py"}
    assert len(r["dropped"]) == 3  # invented symbol, test file, unrelated jump


def test_find_definition():
    text = "import x\n\nexport async function load() {}\nconst LIMIT = 3\nclass Foo:\n"
    assert reading_path.find_definition(text, "load") == 3
    assert reading_path.find_definition(text, "LIMIT") == 4
    assert reading_path.find_definition(text, "Foo") == 5
    assert reading_path.find_definition(text, "x") is None
    assert reading_path.find_definition(text, "not a name") is None
