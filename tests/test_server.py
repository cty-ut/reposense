"""JSON-RPC dispatch (in-process, no subprocess)."""

import io
import json

from reposense.server import Server


def call(server, method, params=None, id=1):
    return server.handle({"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}})


def test_unknown_method_and_missing_repo():
    s = Server(io.StringIO(), io.StringIO())
    assert call(s, "nope")["error"]["code"] == -32601
    assert call(s, "file.history", {"path": "x"})["error"]["code"] == -32001


def test_open_repo_then_history(ws, monkeypatch):
    s = Server(io.StringIO(), io.StringIO())
    monkeypatch.setattr("reposense.core.workspace.Workspace.from_path", classmethod(lambda cls, p: ws))
    opened = call(s, "repo.open", {"path": str(ws.root)})["result"]
    assert opened["repo"] == "acme/pkg"
    h = call(s, "file.history", {"path": "src/pkg/core.py", "limit": 3})["result"]
    assert len(h["commits"]) == 2
    assert call(s, "file.history", {"path": "missing.py"})["error"]["code"] == -32602


def test_serve_forever_round_trip(ws, monkeypatch):
    monkeypatch.setattr("reposense.core.workspace.Workspace.from_path", classmethod(lambda cls, p: ws))
    requests = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "repo.open", "params": {"path": "x"}}),
        # sent right after repo.open without waiting: must still see the opened repository
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "file.history", "params": {"path": "src/pkg/core.py"}}),
        "{not json",
        json.dumps("a string, not a request object"),
    ]) + "\n"
    out = io.StringIO()
    Server(io.StringIO(requests), out).serve_forever()
    replies = {r.get("id"): r for r in map(json.loads, out.getvalue().splitlines()) if r.get("id")}
    errors = [json.loads(line)["error"]["code"] for line in out.getvalue().splitlines() if '"id": null' in line]
    assert replies[1]["result"]["repo"] == "acme/pkg"
    assert len(replies[2]["result"]["commits"]) == 2
    assert sorted(errors) == [-32700, -32600]
