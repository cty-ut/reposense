"""Tools available to the ReAct Q&A agent."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from reposense.config import settings
from reposense.rag.hybrid import HybridRetriever
from reposense.rag.indexer import CodeIndexer
from reposense.tools.github_api import GitHubClient

logger = logging.getLogger(__name__)

_hybrid: HybridRetriever | None = None
_repo_name: str = ""
_file_tree: list[dict] = []


def init_tools(
    hybrid_retriever: HybridRetriever,
    repo_name: str,
    file_tree: list[dict],
) -> None:
    """Initialize module-level state so tools can access the index and repo."""
    global _hybrid, _repo_name, _file_tree
    _hybrid = hybrid_retriever
    _repo_name = repo_name
    _file_tree = file_tree


@tool
def semantic_search(query: str) -> str:
    """Search the codebase for code related to a natural language query or keyword.

    Use this when you need to find code relevant to a concept, function name,
    class name, or any question about the codebase. Returns the most relevant
    code chunks with file paths and line numbers.

    Args:
        query: A natural language question or keyword to search for.
    """
    if _hybrid is None:
        return "Error: search index not initialized."

    logger.info("[Tool] semantic_search(query=%r)", query[:80])
    results = _hybrid.search(_repo_name, query, top_k=6)
    if not results:
        logger.info("[Tool] semantic_search → 0 results")
        return f"No results found for: {query}"

    parts: list[str] = []
    for r in results:
        header = f"[{r.file_path} lines {r.start_line}-{r.end_line}] ({r.symbol_name}, via {r.source})"
        parts.append(f"{header}\n```{r.language}\n{r.content}\n```")
    logger.info("[Tool] semantic_search → %d results: %s", len(results), [r.file_path for r in results])
    return "\n\n".join(parts)


@tool
def read_file(file_path: str) -> str:
    """Read the full content of a specific file from the GitHub repository.

    Use this when you already know which file you need to look at.
    The file_path must be a valid path that exists in the repository.

    Args:
        file_path: The exact path to the file (e.g. "src/click/core.py").
    """
    logger.info("[Tool] read_file(path=%r)", file_path)
    valid_paths = {n["path"] for n in _file_tree if n["type"] == "blob"}
    if file_path not in valid_paths:
        suggestions = [p for p in valid_paths if file_path.split("/")[-1] in p][:5]
        msg = f"File not found: {file_path}"
        if suggestions:
            msg += f"\nDid you mean: {', '.join(suggestions)}"
        logger.info("[Tool] read_file → NOT FOUND")
        return msg

    try:
        with GitHubClient(token=settings.github_token) as client:
            content = client.get_file_content(_repo_name, file_path)
    except Exception as exc:
        return f"Error reading {file_path}: {exc}"

    if content is None:
        return f"Could not read {file_path} (binary or too large)."

    lines = content.split("\n")
    if len(lines) > 300:
        content = "\n".join(lines[:300]) + f"\n\n... ({len(lines) - 300} more lines truncated)"

    return f"[{file_path}] ({len(lines)} lines)\n```\n{content}\n```"


@tool
def list_files(directory: str = "") -> str:
    """List files in a directory of the repository.

    Use this to explore the repository structure when you need to find
    what files exist in a particular directory.

    Args:
        directory: Directory path to list (e.g. "src/click"). Empty string for root.
    """
    logger.info("[Tool] list_files(dir=%r)", directory)
    directory = directory.strip().rstrip("/")

    matches: list[str] = []
    for node in _file_tree:
        path = node["path"]
        if directory:
            if not path.startswith(directory + "/"):
                continue
            remainder = path[len(directory) + 1:]
        else:
            remainder = path

        if "/" not in remainder:
            kind = "📁" if node["type"] == "tree" else "  "
            size = f" ({node['size']}B)" if node["type"] == "blob" and node.get("size", 0) > 0 else ""
            matches.append(f"{kind} {path}{size}")

    if not matches:
        return f"No files found in directory: {directory or '(root)'}"

    return "\n".join(sorted(matches)[:50])


def get_qa_tools() -> list:
    """Return the list of tools available to the Q&A agent."""
    return [semantic_search, read_file, list_files]
