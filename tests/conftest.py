import json
import subprocess
from types import SimpleNamespace

import pytest

from reposense.workspace import Workspace

CORE = """\
def make_metavar(self):
    if self.metavar is not None:
        return self.metavar
    metavar = self.type.get_metavar(self)
    if self.nargs != 1:
        metavar += "..."
    return metavar
"""


def _git(root, *args, env=None):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A tiny git repository with two commits; the second one comes from 'PR #42'."""
    monkeypatch.setenv("GIT_AUTHOR_NAME", "alice")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "alice@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "alice")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "alice@example.com")
    root = tmp_path / "repo"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "src" / "pkg" / "core.py").write_text(CORE.replace('    if self.nargs != 1:\n        metavar += "..."\n', ""))
    (root / "tests" / "test_core.py").write_text("def test_metavar():\n    assert True\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    (root / "src" / "pkg" / "core.py").write_text(CORE)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "bob")
    _git(root, "commit", "-q", "-am", "Add ellipsis for nargs (#42)")
    return Workspace(repo="acme/pkg", root=root.resolve())


class FakeGitHub:
    """Stands in for GitHubHistory so tests never touch the network."""

    def __init__(self):
        self.issue = {"number": 7, "title": "Show ... for multiple options", "state": "open", "author": "carol",
                      "created_at": "2026-01-01T00:00:00Z", "labels": [], "body": "metavar should end with ...",
                      "comments": [{"author": "dave", "body": "Not sure this is a good idea"}],
                      "url": "https://github.com/acme/pkg/issues/7"}

    def get_issue(self, number):
        return self.issue

    def get_pr(self, number):
        if number != 42:
            raise LookupError
        return {"number": 42, "title": "Add ellipsis for nargs", "author": "bob", "state": "merged",
                "merged_at": "2025-06-01", "url": "https://github.com/acme/pkg/pull/42", "body": "",
                "files": [], "reviews": [{"reviewer": "erin", "state": "APPROVED", "body": "Please add a test"}],
                "review_comments": []}

    def search(self, query, kind="pr"):
        return {"query": query, "kind": kind, "results": []}

    def close(self):
        pass


class ScriptedClient:
    """OpenAI-compatible client that replays a fixed list of tool-call rounds."""

    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        calls = self.rounds.pop(0) if self.rounds else []
        tool_calls = [SimpleNamespace(id=f"c{len(self.requests)}_{i}",
                                      function=SimpleNamespace(name=n, arguments=json.dumps(a)))
                      for i, (n, a) in enumerate(calls)] or None
        msg = SimpleNamespace(content="", tool_calls=tool_calls)
        usage = SimpleNamespace(total_tokens=1000, prompt_cache_hit_tokens=600)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage)
