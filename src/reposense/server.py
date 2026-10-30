"""JSON-RPC 2.0 server over stdio (one JSON object per line), used by the VS Code extension.

Requests run on a thread pool so a long analysis does not block quick ones. Progress is pushed
with notifications: `progress` {task, message, percent?} and `plan.event` {event}.
stdout carries protocol messages only; everything else must go to stderr.
"""

from __future__ import annotations

import json
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from reposense.agent.contribution import AgentConfig, ContributionAgent
from reposense.analysis import dependencies, file_history, file_insight, opportunities, structure
from reposense.config import settings
from reposense.core.cache import JsonCache
from reposense.core.github import GitHubClient
from reposense.core.llm import LLM
from reposense.core.workspace import Workspace


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class Session:
    """State for one opened repository, and the handlers for every RPC method."""

    def __init__(self, notify: Callable[[str, dict], None]):
        self.notify = notify
        self.ws: Workspace | None = None
        self.cache: JsonCache | None = None
        self.gh: GitHubClient | None = None
        self._llm: LLM | None = None
        self.lang = "Chinese"

    # ------------------------------------------------------------- helpers

    def _require_repo(self) -> Workspace:
        if self.ws is None:
            raise RpcError(-32001, "No repository is open. Call repo.open first.")
        return self.ws

    def _llm_or_fail(self) -> LLM:
        if not settings.deepseek_api_key:
            raise RpcError(-32002, "DEEPSEEK_API_KEY is not configured.")
        if self._llm is None:
            self._llm = LLM()
        return self._llm

    # ------------------------------------------------------------- methods

    def config_check(self, **_) -> dict:
        return {"missing": settings.validate(), "model": settings.deepseek_model}

    def repo_clone(self, url: str, parent_dir: str) -> dict:
        ws = Workspace.clone_to(url, parent_dir, on_progress=lambda stage, pct: self.notify(
            "progress", {"task": "clone", "message": stage, "percent": pct}))
        return {"repo": ws.repo, "root": str(ws.root)}

    def repo_open(self, path: str, lang: str = "Chinese") -> dict:
        ws = Workspace.from_path(path)
        if self.ws is not None and self.ws.root == ws.root and self.lang == lang:
            return {"repo": ws.repo, "root": str(ws.root), "head": ws.head()}  # already open: keep clients
        self.ws, self.lang = ws, lang
        self.cache = JsonCache(ws.repo)
        if self.gh is not None:
            self.gh.close()
        self.gh = GitHubClient(ws.repo, cache=self.cache) if settings.github_token else None
        return {"repo": ws.repo, "root": str(ws.root), "head": ws.head()}

    def repo_structure(self, refresh: bool = False) -> dict:
        ws = self._require_repo()
        self.notify("progress", {"task": "structure", "message": "Reading files and history…"})
        llm = self._llm_or_fail() if settings.deepseek_api_key else None
        return structure.build_structure(ws, llm, self.cache, lang=self.lang, refresh=refresh)

    def repo_dependencies(self, refresh: bool = False) -> dict:
        return dependencies.build_dependencies(self._require_repo(), self.cache, refresh=refresh)

    def file_history(self, path: str, limit: int = 10) -> dict:
        return file_history.file_history(self._require_repo(), path, self.gh, limit=limit)

    def commit_detail(self, sha: str, path: str) -> dict:
        return file_history.commit_detail(self._require_repo(), sha, path, self.gh)

    def file_insight(self, path: str, refresh: bool = False) -> dict:
        return file_insight.file_insight(self._require_repo(), path, self._llm_or_fail(), self.cache,
                                         lang=self.lang, refresh=refresh)

    def file_opportunities(self, path: str, symbols: list[str] | None = None, refresh: bool = False) -> dict:
        ws = self._require_repo()
        issues = opportunities.related_issues(self.gh, path, symbols) if self.gh else []
        ideas = opportunities.code_suggestions(ws, path, self._llm_or_fail(), self.cache, lang=self.lang,
                                               refresh=refresh)
        return {"path": path, "issues": issues, **ideas}

    def issue_plan(self, number: int) -> dict:
        ws = self._require_repo()
        self._llm_or_fail()
        agent = ContributionAgent(ws, AgentConfig(lang=self.lang),
                                  emit=lambda e: self.notify("plan.event", {"event": e}))
        return agent.run(int(number))


METHODS = {
    "config.check": "config_check", "repo.clone": "repo_clone", "repo.open": "repo_open",
    "repo.structure": "repo_structure", "repo.dependencies": "repo_dependencies",
    "file.history": "file_history", "commit.detail": "commit_detail",
    "file.insight": "file_insight", "file.opportunities": "file_opportunities", "issue.plan": "issue_plan",
}
SEQUENTIAL = {"repo.open"}  # handled in order on the reader thread, never concurrently with other requests


class Server:
    def __init__(self, stdin=None, stdout=None):
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._lock = threading.Lock()
        self.session = Session(self.notify)
        self.pool = ThreadPoolExecutor(max_workers=4)

    def _send(self, msg: dict) -> None:
        line = json.dumps(msg, ensure_ascii=False)
        with self._lock:
            self.stdout.write(line + "\n")
            self.stdout.flush()

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def handle(self, msg: dict) -> dict | None:
        """Run one request and build its response (None for notifications from the client)."""
        req_id = msg.get("id")
        name = METHODS.get(msg.get("method", ""))
        try:
            if name is None:
                raise RpcError(-32601, f"Unknown method {msg.get('method')}")
            params: Any = msg.get("params") or {}
            result = getattr(self.session, name)(**params)
            response = {"jsonrpc": "2.0", "id": req_id, "result": result}
        except RpcError as e:
            response = {"jsonrpc": "2.0", "id": req_id, "error": {"code": e.code, "message": str(e)}}
        except (FileNotFoundError, ValueError, LookupError, TypeError) as e:
            response = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": f"{type(e).__name__}: {e}"}}
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            response = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": f"{type(e).__name__}: {e}"}}
        return response if req_id is not None else None

    def _run(self, msg: dict) -> None:
        response = self.handle(msg)
        if response is not None:
            self._send(response)

    def serve_forever(self) -> None:
        for line in self.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                self._send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            if not isinstance(msg, dict):
                self._send({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}})
                continue
            if msg.get("method") in SEQUENTIAL:
                self._run(msg)  # changes session state: later requests must see the result
            else:
                self.pool.submit(self._run, msg)
        self.pool.shutdown(wait=True)


def main() -> None:
    # Make sure nothing else writes to the protocol channel.
    stdout = sys.stdout
    sys.stdout = sys.stderr
    stdin = open(sys.stdin.fileno(), encoding="utf-8", closefd=False)
    out = open(stdout.fileno(), "w", encoding="utf-8", closefd=False)
    Server(stdin, out).serve_forever()
