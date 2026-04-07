"""Q&A graph: ReAct agent with tool calling for codebase Q&A."""

from __future__ import annotations

import logging
import os
import time

from langchain_core.callbacks import BaseCallbackHandler
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from reposense.config import settings
from reposense.graph.state import RepoSenseState
from reposense.llm import get_llm, force_provider
from reposense.metrics import get_collector
from reposense.rag.hybrid import HybridRetriever
from reposense.rag.indexer import CodeIndexer
from reposense.tools.qa_tools import get_qa_tools, init_tools


class _MetricsCallbackHandler(BaseCallbackHandler):
    """Records ReAct agent LLM calls into MetricsCollector."""

    def __init__(self) -> None:
        self._call_start: dict[str, float] = {}

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        self._call_start[str(run_id)] = time.time()

    def on_llm_end(self, response, *, run_id, **kwargs):
        elapsed = time.time() - self._call_start.pop(str(run_id), time.time())
        collector = get_collector()
        input_tok, output_tok, model = 0, 0, "unknown"

        if response.generations:
            for gen_list in response.generations:
                for gen in gen_list:
                    msg = getattr(gen, "message", None)
                    if msg:
                        usage = getattr(msg, "usage_metadata", None)
                        if isinstance(usage, dict):
                            input_tok += usage.get("input_tokens", 0)
                            output_tok += usage.get("output_tokens", 0)
                        meta = getattr(msg, "response_metadata", None)
                        if isinstance(meta, dict) and meta.get("model_name"):
                            model = meta["model_name"]
                    info = getattr(gen, "generation_info", {}) or {}
                    if model == "unknown" and info.get("model_name"):
                        model = info["model_name"]

        if not input_tok and response.llm_output:
            usage = response.llm_output.get("token_usage") or response.llm_output.get("usage", {})
            if isinstance(usage, dict):
                input_tok = usage.get("prompt_tokens", 0) or usage.get("input_tokens", 0)
                output_tok = usage.get("completion_tokens", 0) or usage.get("output_tokens", 0)
            if response.llm_output.get("model_name"):
                model = response.llm_output["model_name"]

        collector.record_llm_call(
            provider="ReAct-agent", model=model,
            input_tokens=input_tok, output_tokens=output_tok,
            elapsed_s=elapsed, success=True,
        )

    def on_llm_error(self, error, *, run_id, **kwargs):
        elapsed = time.time() - self._call_start.pop(str(run_id), time.time())
        get_collector().record_llm_call(
            provider="ReAct-agent", model="unknown",
            input_tokens=0, output_tokens=0,
            elapsed_s=elapsed, success=False, error=str(error)[:200],
        )

logger = logging.getLogger(__name__)

QA_SYSTEM_PROMPT = """\
You are RepoSense, an AI assistant that helps developers understand open-source codebases.

You have access to a code search index and can read files directly from the GitHub repository.

## Your tools:
1. **semantic_search(query)** — Search for code by meaning or keyword. Good for finding relevant functions, classes, or concepts.
2. **read_file(file_path)** — Read the full content of a specific file. Use when you know which file to look at.
3. **list_files(directory)** — List files in a directory. Use to explore the repository structure.

## Rules:
- ALWAYS use tools to find information before answering. Do NOT guess or make up code.
- Every file path and code snippet you mention in your answer MUST come from tool results.
- If you cannot find the information, say so honestly rather than inventing an answer.
- ALWAYS cite specific file paths using backtick format (e.g. `src/module/file.py`) in your answer. Every answer MUST reference at least one concrete file path.
- Include line numbers when referencing specific code (e.g. `src/file.py:42`).
- Answer in the same language as the user's question (Chinese if asked in Chinese).
- Be concise but thorough. Use markdown formatting.
- Maximum {max_loops} tool calls per question.

## Repository: {repo_name}
{repo_description}
"""


_DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "reposense")


class QAEngine:
    """Manages the ReAct Q&A agent with RAG index and multi-turn memory."""

    def __init__(self, persist_directory: str | None = None) -> None:
        self._cache_dir = persist_directory or _DEFAULT_CACHE_DIR
        os.makedirs(self._cache_dir, exist_ok=True)
        chroma_dir = os.path.join(self._cache_dir, "chroma")
        self._indexer = CodeIndexer(chroma_dir)
        self._hybrid = HybridRetriever(self._indexer)
        self._memory = MemorySaver()
        self._agent = None
        self._repo_name: str = ""
        self._repo_meta: dict = {}
        self._file_tree: list[dict] = []
        self._indexed: bool = False

    @property
    def is_indexed(self) -> bool:
        return self._indexed

    def _bm25_cache_path(self, repo_name: str) -> str:
        safe = repo_name.replace("/", "_")
        return os.path.join(self._cache_dir, f"bm25_{safe}.pkl")

    def _has_existing_index(self, repo_name: str) -> bool:
        """Check if both vector and BM25 indexes already exist for this repo."""
        from reposense.rag.indexer import _collection_name
        try:
            self._indexer.chroma_client.get_collection(_collection_name(repo_name))
        except Exception:
            return False
        return os.path.exists(self._bm25_cache_path(repo_name))

    def build_index(
        self,
        repo_name: str,
        file_tree: list[dict],
        file_contents: dict[str, str] | None = None,
        *,
        force_rebuild: bool = False,
    ) -> dict:
        """Build both vector and BM25 indexes, or load from cache."""
        self._repo_name = repo_name
        self._file_tree = file_tree

        if not force_rebuild and self._has_existing_index(repo_name):
            bm25_path = self._bm25_cache_path(repo_name)
            self._hybrid._bm25.load(bm25_path)
            init_tools(self._hybrid, repo_name, file_tree)
            self._indexed = True
            count = len(self._hybrid._bm25._chunks)
            logger.info("Loaded cached index for %s (%d chunks)", repo_name, count)
            return {"total_chunks": count, "cached": True}

        if file_contents is None:
            stats = self._indexer.build_index_from_github(
                repo_name, file_tree=file_tree, max_files=200,
            )
        else:
            stats = self._indexer.build_index(repo_name, file_contents)

        bm25_count = self._hybrid.build_bm25_index(
            file_contents or self._fetch_files(repo_name, file_tree)
        )
        stats["bm25_chunks"] = bm25_count

        self._hybrid._bm25.save(self._bm25_cache_path(repo_name))

        init_tools(self._hybrid, repo_name, file_tree)
        self._indexed = True
        logger.info("Index built for %s: %s", repo_name, stats)
        return stats

    def _fetch_files(self, repo_name: str, file_tree: list[dict]) -> dict[str, str]:
        from reposense.tools.github_api import GitHubClient

        with GitHubClient(token=settings.github_token) as client:
            return client.download_repo_files(repo_name, file_tree, max_files=200)

    def _ensure_agent(self, force_rebuild: bool = False) -> None:
        """Lazily build the ReAct agent."""
        if self._agent is not None and not force_rebuild:
            return

        llm = get_llm(temperature=0, max_output_tokens=4096)
        if not llm.callbacks:
            llm.callbacks = []
        llm.callbacks.append(_MetricsCallbackHandler())

        tools = get_qa_tools()

        desc = self._repo_meta.get("description", "") or ""
        system = QA_SYSTEM_PROMPT.format(
            repo_name=self._repo_name,
            repo_description=f"Description: {desc}" if desc else "",
            max_loops=settings.max_react_loops,
        )

        self._agent = create_react_agent(
            llm,
            tools,
            prompt=system,
            checkpointer=self._memory,
        )
        self._current_llm_label = getattr(llm, "model_name", "unknown")

    def ask(
        self,
        question: str,
        *,
        thread_id: str = "default",
        repo_meta: dict | None = None,
    ) -> dict:
        """Ask a question about the repository.

        Returns:
            {
                "answer": str,
                "iterations": int,
                "thread_id": str,
            }
        """
        if not self._indexed:
            return {"answer": "Error: No repository has been indexed yet. Run `analyze` first.", "iterations": 0, "thread_id": thread_id}

        if repo_meta:
            self._repo_meta = repo_meta

        self._ensure_agent()

        config = {"configurable": {"thread_id": thread_id}}
        input_msg = {"messages": [("user", question)]}

        result = None
        for attempt in range(2):
            try:
                result = self._agent.invoke(input_msg, config=config)
                break
            except Exception as exc:
                logger.error("ReAct agent failed: %s", exc)
                is_rate_limit = "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)
                if attempt == 0 and settings.has_fallback_llm and is_rate_limit:
                    logger.info("Rate limited — switching to fallback LLM (Poe)...")
                    force_provider("poe")
                    self._agent = None
                    self._memory = MemorySaver()
                    self._ensure_agent(force_rebuild=True)
                    input_msg = {"messages": [("user", question)]}
                else:
                    return {"answer": f"Agent error: {exc}", "iterations": 0, "thread_id": thread_id}

        if result is None:
            return {"answer": "Agent failed after retries.", "iterations": 0, "thread_id": thread_id}

        messages = result.get("messages", [])

        # Extract tool usage details
        tool_call_count = 0
        tool_usage: list[dict] = []
        for msg in messages:
            calls = getattr(msg, "tool_calls", None)
            if calls:
                tool_call_count += 1
                for tc in calls:
                    tool_usage.append({
                        "tool": tc.get("name", "unknown"),
                        "args": {k: (v[:60] + "…" if isinstance(v, str) and len(v) > 60 else v)
                                 for k, v in tc.get("args", {}).items()},
                    })

        # Get the final AI message content
        answer = ""
        for msg in reversed(messages):
            content = getattr(msg, "content", None)
            if content and not getattr(msg, "tool_calls", None):
                if isinstance(content, list):
                    text_parts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in content]
                    answer = "\n".join(t for t in text_parts if t)
                else:
                    answer = str(content)
                if answer.strip():
                    break

        if not answer:
            last = messages[-1] if messages else None
            if last:
                c = getattr(last, "content", "")
                answer = str(c) if c else "No answer generated."
            else:
                answer = "No answer generated."

        return {
            "answer": answer,
            "iterations": tool_call_count,
            "tool_usage": tool_usage,
            "thread_id": thread_id,
        }

    def get_conversation_history(self, thread_id: str = "default") -> list[dict]:
        """Retrieve conversation history for a thread."""
        if self._agent is None:
            return []
        config = {"configurable": {"thread_id": thread_id}}
        try:
            state = self._agent.get_state(config)
            messages = state.values.get("messages", [])
            history = []
            for msg in messages:
                if hasattr(msg, "type"):
                    if msg.type == "human":
                        history.append({"role": "user", "content": msg.content})
                    elif msg.type == "ai" and msg.content and not getattr(msg, "tool_calls", None):
                        history.append({"role": "assistant", "content": msg.content})
            return history
        except Exception:
            return []
