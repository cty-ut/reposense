"""Contribution-planning agent: explores code and project history, then submits a verified plan.

The loop is written by hand (not a prebuilt ReAct agent) so that step/token budgets and
citation verification are enforced in code rather than only requested in the prompt.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from openai import OpenAI

from reposense.agent import code_tools, history_tools
from reposense.config import settings
from reposense.core.cache import JsonCache
from reposense.core.github import GitHubClient
from reposense.core.llm import make_client
from reposense.core.workspace import Workspace

Emit = Callable[[dict], None]

TOOL_RESULT_CHARS = 5000
# Context stays append-only (so the provider's prefix cache keeps hitting) until it exceeds
# CONTEXT_CHAR_LIMIT; then older tool results are replaced by one-line summaries in one go.
CONTEXT_CHAR_LIMIT = 250_000
KEEP_FULL_ROUNDS = 8
GRACE_PER_REJECTION = 2  # rounds to fix a plan rejected in the final phase (look something up, resubmit)


def _fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
    props = {**props, "reason": {"type": "string", "description": "One sentence: why this call (shown to the user)."}}
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": required}}}


CODE_TOOLS = [
    _fn("glob", "Find tracked files by glob pattern, e.g. '**/*.py', 'tests/test_*.py'.",
        {"pattern": {"type": "string"}}, ["pattern"]),
    _fn("grep", "Regex search in file contents. Returns file:line:text. Narrow with path or file_glob.",
        {"pattern": {"type": "string"}, "path": {"type": "string"}, "file_glob": {"type": "string"},
         "ignore_case": {"type": "boolean"}}, ["pattern"]),
    _fn("read", "Read lines start_line..end_line of a file, with line numbers (max 400 lines per call; "
                "default: 200 lines from start_line). Use grep first to find the line numbers.",
        {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
        ["path"]),
    _fn("ls", "List a directory's immediate children.", {"directory": {"type": "string"}}, []),
]

HISTORY_TOOLS = [
    _fn("git_log", "Recent commits touching a file or directory (author, date, subject, PR number).",
        {"path": {"type": "string"}, "max_count": {"type": "integer"}}, ["path"]),
    _fn("git_blame", "Which commit/PR last changed each block of lines start_line..end_line.",
        {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
        ["path", "start_line", "end_line"]),
    _fn("search_prs", "Search this repo's pull requests by keywords.", {"query": {"type": "string"}}, ["query"]),
    _fn("search_issues", "Search this repo's issues by keywords (find duplicates / earlier discussions).",
        {"query": {"type": "string"}}, ["query"]),
    _fn("get_pr", "A pull request's description, changed files, and reviewer feedback.",
        {"number": {"type": "integer"}}, ["number"]),
]

SUBMIT_TOOL = _fn(
    "submit_plan",
    "Submit the final contribution plan. Every cited range is checked against the file; errors are returned for you to fix.",
    {
        "summary": {"type": "string", "description": "What the issue is and the proposed approach (2-4 sentences)."},
        "changes": {"type": "array", "items": {"type": "object", "properties": {
            "file": {"type": "string"},
            "start_line": {"type": "integer"}, "end_line": {"type": "integer"},
            "anchor": {"type": "string", "description": "An exact code line copied from within start_line..end_line."},
            "what": {"type": "string", "description": "The change to make."},
            "why": {"type": "string", "description": "Evidence-based reason, e.g. what grep/blame/PR showed."},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        }, "required": ["file", "start_line", "end_line", "anchor", "what", "why", "confidence"]}},
        "tests": {"type": "array", "items": {"type": "object", "properties": {
            "file": {"type": "string"}, "command": {"type": "string"}, "why": {"type": "string"}},
            "required": ["why"]}},
        "related_prs": {"type": "array", "items": {"type": "object", "properties": {
            "number": {"type": "integer"}, "relevance": {"type": "string"},
            "review_notes": {"type": "array", "items": {"type": "string"},
                             "description": "Reviewer feedback from that PR that applies to this change."}},
            "required": ["number", "relevance"]}},
        "reviewers": {"type": "array", "items": {"type": "object", "properties": {
            "login": {"type": "string"}, "why": {"type": "string"}}, "required": ["login", "why"]}},
        "conventions": {"type": "array", "items": {"type": "string"},
                        "description": "Project rules a newcomer must follow (from CONTRIBUTING or past reviews)."},
        "open_questions": {"type": "array", "items": {"type": "string"},
                           "description": "Things to confirm with maintainers before coding."},
    },
    ["summary", "changes"],
)

SYSTEM_PROMPT = """You help a first-time contributor fix a GitHub issue in {repo}. The repository is checked out locally.

Work like an experienced maintainer onboarding a newcomer:
1. Understand the issue. Extract concrete symbols, error messages, option names.
2. Locate the code with grep/glob, then read only the relevant line ranges. Prefer exact symbol searches over guessing.
3. {history_step}
4. Find the existing tests that cover this code and how to run them (look for CONTRIBUTING, tox.ini, pyproject, Makefile, CI config).
   One relevant test location is enough; do not search exhaustively.
5. Call submit_plan as soon as you can cite the edit locations, a test location, and the relevant history.

Rules:
- Only state facts you saw in tool results. Every change must cite a line range you actually read, with an exact anchor line from it.
- Keep the plan small and concrete: the minimal set of edits a newcomer should make.
- If the issue is unclear or seems already fixed, say so in open_questions instead of inventing work.
- Write summary/what/why/notes in {lang}. Keep code identifiers as-is.
- Budget: at most {max_steps} rounds of tool calls. Batch independent calls in one round.
"""

HISTORY_STEP = ("Check the history of the code you will change: git_blame the target lines, git_log the file, "
                "open the most relevant PRs with get_pr and extract reviewer feedback and conventions that apply. "
                "Look for earlier issues/PRs on the same topic.")
NO_HISTORY_STEP = "(History tools are disabled in this run.)"


@dataclass
class AgentConfig:
    use_history: bool = True
    max_steps: int = 25
    max_tool_calls: int = 60
    max_total_tokens: int = 600_000
    max_plan_rejections: int = 2
    lang: str = "Chinese"
    cutoff: str | None = None  # evaluation: hide anything created after this timestamp


@dataclass
class Ledger:
    """What the agent actually observed — the ground truth for verifying its plan."""
    read_ranges: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    prs: dict[int, dict] = field(default_factory=dict)
    seen_text: list[str] = field(default_factory=list)

    def saw_login(self, login: str) -> bool:
        pat = re.compile(rf"(?<![\w-]){re.escape(login)}(?![\w-])", re.IGNORECASE)
        return any(pat.search(t) for t in self.seen_text)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


class ContributionAgent:
    def __init__(self, ws: Workspace, config: AgentConfig | None = None, emit: Emit | None = None,
                 client: OpenAI | None = None):
        self.ws = ws
        self.cfg = config or AgentConfig()
        self.emit = emit or (lambda e: None)
        self.gh = GitHubClient(ws.repo, cutoff=self.cfg.cutoff, cache=JsonCache(ws.repo))
        self.client = client or make_client()
        self.model = settings.deepseek_model
        self.ledger = Ledger()

    # ------------------------------------------------------------- tools

    def _tools(self) -> list[dict]:
        return CODE_TOOLS + (HISTORY_TOOLS if self.cfg.use_history else []) + [SUBMIT_TOOL]

    def _run_tool(self, name: str, args: dict) -> dict:
        ws, a = self.ws, args
        schema = next((t["function"]["parameters"]["properties"] for t in self._tools()
                       if t["function"]["name"] == name), None)
        if schema is not None:
            unknown = sorted(set(a) - set(schema))
            if unknown:  # never silently ignore arguments: tell the model what is valid
                valid = ", ".join(k for k in schema if k != "reason")
                return {"error": "bad_arguments", "message": f"Unknown argument(s) {unknown}. Valid: {valid}"}
        try:
            if name == "glob":
                return code_tools.glob(ws, a["pattern"])
            if name == "grep":
                return code_tools.grep(ws, a["pattern"], a.get("path", ""), a.get("file_glob", ""),
                                       bool(a.get("ignore_case")))
            if name == "read":
                res = code_tools.read(ws, a["path"], a.get("start_line", 1), a.get("end_line"))
                if "content" in res:
                    self.ledger.read_ranges.setdefault(res["path"], []).append((res["start"], res["end"]))
                return res
            if name == "ls":
                return code_tools.ls(ws, a.get("directory", ""))
            if not self.cfg.use_history:
                return {"error": "unknown_tool", "message": f"{name} is not available"}
            if name == "git_log":
                return history_tools.git_log(ws, a["path"], a.get("max_count", 10))
            if name == "git_blame":
                return history_tools.git_blame(ws, a["path"], a["start_line"], a["end_line"])
            if name in ("search_prs", "search_issues"):
                kind = "pr" if name == "search_prs" else "issue"
                return {"query": a["query"], "results": self.gh.search(a["query"], kind)}
            if name == "get_pr":
                res = self.gh.get_pr(int(a["number"]))
                if "error" not in res:
                    self.ledger.prs[res["number"]] = res
                return res
            return {"error": "unknown_tool", "message": f"No tool named {name}"}
        except KeyError as e:
            return {"error": "bad_arguments", "message": f"Missing argument {e}"}
        except LookupError:
            return {"error": "not_found", "message": "Not found on GitHub"}
        except (ValueError, OSError) as e:
            return {"error": "failed", "message": str(e)[:300]}
        except Exception as e:  # network/API errors are reported to the model, not raised
            return {"error": "tool_failed", "message": f"{type(e).__name__}: {str(e)[:300]}"}

    @staticmethod
    def summarize(name: str, res: dict) -> str:
        if "error" in res:
            return f"✗ {res['error']}: {res.get('message', '')}"[:200]
        if name == "glob":
            return f"{res['total']} files"
        if name == "grep":
            files = sorted({m['file'] for m in res['matches']})
            return f"{res['total']} matches in {len(files)} files" + (f": {', '.join(files[:3])}" if files else "")
        if name == "read":
            return f"{res['path']} lines {res['start']}-{res['end']} of {res['total_lines']}"
        if name == "ls":
            return f"{len(res['entries'])} entries"
        if name == "git_log":
            return f"{len(res['commits'])} commits, latest: {res['commits'][0]['subject'][:60]}"
        if name == "git_blame":
            prs = sorted({b['pr'] for b in res['blocks'] if b.get('pr')})
            return f"{len(res['blocks'])} blocks" + (f", PRs {prs}" if prs else "")
        if name in ("search_prs", "search_issues"):
            return f"{len(res['results'])} results" + (f": #{res['results'][0]['number']} {res['results'][0]['title'][:50]}" if res['results'] else "")
        if name == "get_pr":
            return f"#{res['number']} {res['title'][:60]} ({len(res['reviews']) + len(res['review_comments'])} review notes)"
        return "ok"

    # ------------------------------------------------------------- verification

    def _verify(self, plan: dict) -> tuple[dict, list[str]]:
        """Return (plan with invalid items removed, errors). Errors are fed back to the model."""
        errors: list[str] = []
        good_changes = []
        for i, c in enumerate(plan.get("changes") or []):
            tag = f"changes[{i}] {c.get('file')}:{c.get('start_line')}-{c.get('end_line')}"
            lines = code_tools.read_lines(self.ws, c.get("file", ""))
            if lines is None:
                errors.append(f"{tag}: file does not exist")
                continue
            s, e = int(c.get("start_line", 0)), int(c.get("end_line", 0))
            if not (1 <= s <= e <= len(lines)):
                errors.append(f"{tag}: line range invalid (file has {len(lines)} lines)")
                continue
            if not any(rs <= e and s <= re_ for rs, re_ in self.ledger.read_ranges.get(c["file"], [])):
                errors.append(f"{tag}: you never read these lines; read them before citing")
                continue
            anchor = _norm(c.get("anchor", ""))
            if not anchor or anchor not in _norm("\n".join(lines[s - 1:e])):
                errors.append(f"{tag}: anchor {c.get('anchor')!r} is not in those lines")
                continue
            good_changes.append(c)

        good_prs = []
        for p in plan.get("related_prs") or []:
            if p.get("number") in self.ledger.prs:
                pr = self.ledger.prs[p["number"]]
                good_prs.append({**p, "title": pr["title"], "url": pr["url"], "author": pr["author"],
                                 "merged_at": pr["merged_at"]})
            else:
                errors.append(f"related_prs #{p.get('number')}: not opened with get_pr in this run")

        good_reviewers = []
        for r in plan.get("reviewers") or []:
            if self.ledger.saw_login(r.get("login", "")):
                good_reviewers.append(r)
            else:
                errors.append(f"reviewers {r.get('login')}: this login never appeared in tool results")

        if not good_changes and not plan.get("open_questions"):
            errors.append("plan has no valid change; cite verified code or explain in open_questions")
        return {**plan, "changes": good_changes, "related_prs": good_prs, "reviewers": good_reviewers}, errors

    def _history_for(self, changes: list[dict]) -> dict[str, list[dict]]:
        """Deterministic (non-AI) history around each cited range, for editor annotations."""
        if not self.cfg.use_history:
            return {}
        out: dict[str, list[dict]] = {}
        for c in changes:
            lines = code_tools.read_lines(self.ws, c["file"]) or []
            s, e = max(1, c["start_line"] - 3), min(len(lines), c["end_line"] + 3)
            res = history_tools.git_blame(self.ws, c["file"], s, e)
            blocks = out.setdefault(c["file"], [])
            for b in res.get("blocks", []):
                if b not in blocks:
                    pr = self.ledger.prs.get(b.get("pr") or -1)
                    blocks.append({**b, "pr_title": pr["title"] if pr else None,
                                   "pr_url": pr["url"] if pr else (
                                       f"https://github.com/{self.ws.repo}/pull/{b['pr']}" if b.get("pr") else None)})
        return out

    # ------------------------------------------------------------- loop

    @staticmethod
    def _compact(messages: list[dict], rounds: list[list[int]], summaries: dict[int, str]) -> None:
        """Keep full tool output only for the latest rounds; earlier ones become their summaries.

        What the agent read is still tracked in the ledger, and it can re-read any range when needed.
        """
        for idxs in rounds[:-KEEP_FULL_ROUNDS]:
            for i in idxs:
                if not messages[i]["content"].startswith("[elided]"):
                    messages[i]["content"] = f"[elided] {summaries[i]} (re-run the tool if you need the details again)"

    def run(self, issue_number: int) -> dict:
        t0 = time.time()
        issue = self.gh.get_issue(issue_number)
        self.ledger.seen_text.append(json.dumps(issue, ensure_ascii=False))
        self.emit({"type": "start", "repo": self.ws.repo, "issue": issue, "model": self.model,
                   "use_history": self.cfg.use_history})

        system = SYSTEM_PROMPT.format(
            repo=self.ws.repo, lang=self.cfg.lang, max_steps=self.cfg.max_steps,
            history_step=HISTORY_STEP if self.cfg.use_history else NO_HISTORY_STEP)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": "Issue:\n" + json.dumps(issue, ensure_ascii=False, indent=1)},
        ]
        trace: list[dict] = []
        rounds: list[list[int]] = []  # indices of tool messages, per round
        summaries: dict[int, str] = {}
        tool_calls = tokens = cached_tokens = rejections = 0
        plan: dict | None = None
        stop = "max_steps"
        step = 0
        seen_calls: dict[str, tuple[int, int, str]] = {}  # call key -> (round, message index, summary)
        fix_tools = {"read", "get_pr", "submit_plan"}
        final_tools = [t for t in self._tools() if t["function"]["name"] in fix_tools]

        # After max_steps the agent may only fix a plan the verifier rejected: each rejection in the
        # final phase grants GRACE_PER_REJECTION more rounds, limited to the fix-up tools.
        limit = self.cfg.max_steps
        while step < limit:
            step += 1
            if tokens >= self.cfg.max_total_tokens:
                stop = "token_budget"
                break
            final_phase = step >= self.cfg.max_steps
            if step == self.cfg.max_steps:
                messages.append({"role": "user", "content": "This is your last round. Call submit_plan now."})
            allowed = final_tools if final_phase else self._tools()
            resp = self.client.chat.completions.create(
                model=self.model, messages=messages, tools=allowed, temperature=0)
            if resp.usage:
                tokens += resp.usage.total_tokens
                cached_tokens += getattr(resp.usage, "prompt_cache_hit_tokens", 0) or 0
            msg = resp.choices[0].message
            assistant: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                assistant["tool_calls"] = [{"id": tc.id, "type": "function", "function": {
                    "name": tc.function.name, "arguments": tc.function.arguments}} for tc in msg.tool_calls]
            messages.append(assistant)
            if msg.content:
                self.emit({"type": "thought", "step": step, "text": msg.content})
            if not msg.tool_calls:
                messages.append({"role": "user", "content": "Continue with tools, and finish by calling submit_plan."})
                continue

            rounds.append([])
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError as e:
                    args, res = {}, {"error": "bad_json", "message": str(e)}
                else:
                    reason = args.pop("reason", "")
                    self.emit({"type": "tool_call", "step": step, "name": name, "args": args, "reason": reason})
                    key = name + json.dumps(args, sort_keys=True, ensure_ascii=False)
                    prev = seen_calls.get(key)
                    if final_phase and name not in fix_tools:
                        res = {"error": "not_allowed", "message": f"Only {', '.join(sorted(fix_tools))} are available now."}
                    elif tool_calls >= self.cfg.max_tool_calls and name != "submit_plan":
                        res = {"error": "tool_budget_exhausted", "message": "Tool budget used up; call submit_plan."}
                    elif (name != "submit_plan" and prev is not None
                          and not messages[prev[1]]["content"].startswith("[elided]")):
                        # Repeating an identical call cannot produce new information: break the loop.
                        res = {"error": "duplicate_call", "message": (
                            f"You already made this exact call in round {prev[0]} ({prev[2]}). Use that result, "
                            "try a different query, or call submit_plan if you have enough.")}
                    elif name == "submit_plan":
                        verified, errors = self._verify(args)
                        if errors and rejections < self.cfg.max_plan_rejections:
                            rejections += 1
                            if final_phase:
                                limit = min(limit + GRACE_PER_REJECTION,
                                            self.cfg.max_steps + GRACE_PER_REJECTION * self.cfg.max_plan_rejections)
                            res = {"error": "plan_rejected", "errors": errors}
                            self.emit({"type": "plan_rejected", "step": step, "errors": errors})
                        else:
                            plan = {**verified, "dropped": errors}
                            res = {"ok": True}
                    else:
                        res = self._run_tool(name, args)
                    tool_calls += 1
                summary = "accepted" if name == "submit_plan" and res.get("ok") else self.summarize(name, res)
                text = json.dumps(res, ensure_ascii=False)
                if name != "submit_plan":
                    self.ledger.seen_text.append(text)
                self.emit({"type": "tool_result", "step": step, "name": name, "summary": summary,
                           "ok": "error" not in res})
                trace.append({"step": step, "tool": name, "args": args, "summary": summary})
                if len(text) > TOOL_RESULT_CHARS:
                    text = text[:TOOL_RESULT_CHARS] + ' …(truncated; narrow your query or read a smaller range)'
                if name != "submit_plan":  # verifier errors stay visible until fixed
                    rounds[-1].append(len(messages))
                    summaries[len(messages)] = f"{name}({json.dumps(args, ensure_ascii=False)[:120]}) -> {summary}"
                    if args and res.get("error") != "duplicate_call":
                        seen_calls[name + json.dumps(args, sort_keys=True, ensure_ascii=False)] = (step, len(messages), summary)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": text})
            if plan is not None:
                stop = "submitted"
                break
            if sum(len(m.get("content") or "") for m in messages) > CONTEXT_CHAR_LIMIT:
                self._compact(messages, rounds, summaries)

        result = {
            "repo": self.ws.repo, "commit": self.ws.head(),
            "issue": {k: issue[k] for k in ("number", "title", "url", "labels")},
            "status": "ok" if plan else "incomplete",
            "plan": plan, "history": self._history_for(plan["changes"]) if plan else {},
            "trace": trace,
            "run": {"model": self.model, "use_history": self.cfg.use_history, "steps": step,
                    "tool_calls": tool_calls, "tokens": tokens, "cached_tokens": cached_tokens,
                    "seconds": round(time.time() - t0, 1),
                    "stop_reason": stop, "plan_rejections": rejections},
        }
        self.emit({"type": "result", "result": result})
        self.gh.close()
        return result
