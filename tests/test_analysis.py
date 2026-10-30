"""Analysis layer on a tiny local git repo (no network, no model)."""

from reposense.analysis import file_insight, opportunities, structure
from reposense.analysis.file_history import commit_detail, file_history
from reposense.core.cache import JsonCache


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def json(self, system, user, **_):
        self.calls += 1
        return self.reply


def test_file_history_maps_current_lines_to_commits(ws):
    h = file_history(ws, "src/pkg/core.py", gh=None, limit=5)
    newest, oldest = h["commits"]
    assert newest["subject"] == "Add ellipsis for nargs (#42)"
    assert newest["pr"]["number"] == 42
    assert newest["ranges"] == [[5, 6]] and newest["owned_lines"] == 2
    assert oldest["ranges"] == [[1, 4], [7, 7]]
    assert h["total_lines"] == 7 and h["older_lines"] == 0


def test_commit_detail_has_diff_without_github(ws):
    sha = file_history(ws, "src/pkg/core.py", gh=None)["commits"][0]["sha"]
    d = commit_detail(ws, sha, "src/pkg/core.py", gh=None)
    assert '+        metavar += "..."' in d["diff"] and d["pr"] is None


def test_structure_falls_back_to_heuristic_layers_without_model(ws, tmp_path):
    s = structure.build_structure(ws, llm=None, cache=JsonCache("acme/pkg", root=tmp_path / "c"))
    layers = {n["layer"]: n for n in s["root"]["children"]}
    assert set(layers) == {"core", "tests"}
    assert layers["core"]["children"][0]["path"] == "src/pkg"  # src → pkg chain is collapsed
    assert s["root"]["lines"] == 7 + 2


def test_structure_drops_paths_the_model_invented(ws, tmp_path):
    llm = FakeLLM({"summary": "demo", "layers": [
        {"id": "lib", "name": "Library", "kind": "core", "paths": ["src", "made/up"]},
        {"id": "dup", "name": "Dup", "kind": "weird", "paths": ["src"]},
    ], "modules": {"src/pkg": "the package", "nope": "x"}})
    s = structure.build_structure(ws, llm, JsonCache("acme/pkg", root=tmp_path / "c"))
    layers = {n["layer"]: n for n in s["root"]["children"]}
    assert set(layers) == {"lib", "other"}  # "dup" lost its only (already claimed) path; tests → other
    assert layers["lib"]["children"][0]["description"] == "the package"


def test_insight_fixes_or_drops_wrong_citations(ws, tmp_path):
    llm = FakeLLM({"summary": "s", "symbols": [
        {"name": "make_metavar", "kind": "function", "line": 2, "description": "ok, one line off"},
        {"name": "ghost", "kind": "function", "line": 3, "description": "invented"},
    ], "collaborators": [{"path": "tests/test_core.py", "relation": "tests"}, {"path": "nope.py", "relation": "x"}]})
    cache = JsonCache("acme/pkg", root=tmp_path / "c")
    r = file_insight.file_insight(ws, "src/pkg/core.py", llm, cache)
    assert [(s["name"], s["line"]) for s in r["symbols"]] == [("make_metavar", 1)]
    assert [c["path"] for c in r["collaborators"]] == ["tests/test_core.py"]
    assert len(r["dropped"]) == 2
    file_insight.file_insight(ws, "src/pkg/core.py", llm, cache)
    assert llm.calls == 1  # second call served from cache


def test_suggestions_need_a_real_anchor(ws, tmp_path):
    llm = FakeLLM({"suggestions": [
        {"title": "good", "kind": "test", "difficulty": "easy", "start_line": 5, "end_line": 6,
         "anchor": "if self.nargs != 1:", "detail": "d"},
        {"title": "bad", "kind": "test", "difficulty": "easy", "start_line": 1, "end_line": 2,
         "anchor": "if self.nargs != 1:", "detail": "d"},
    ]})
    r = opportunities.code_suggestions(ws, "src/pkg/core.py", llm, JsonCache("acme/pkg", root=tmp_path / "c"))
    assert [s["title"] for s in r["suggestions"]] == ["good"]
    assert len(r["dropped"]) == 1
