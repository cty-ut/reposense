"""MCP Server — exposes RepoSense tools via the Model Context Protocol.

Start with:
    python -m reposense.mcp_server

Or via the CLI:
    reposense mcp-serve
"""

from __future__ import annotations

import json
import logging
import os

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    TextContent,
    Tool,
)

from reposense.config import settings
from reposense.tools.github_api import GitHubClient

logger = logging.getLogger(__name__)

app = Server("reposense")

_github_client: GitHubClient | None = None


def _get_github_client() -> GitHubClient:
    global _github_client
    if _github_client is None:
        _github_client = GitHubClient(token=settings.github_token)
    return _github_client


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_repo_meta",
            description="Get metadata about a GitHub repository: name, description, language, stars, forks, license, topics, etc.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {
                        "type": "string",
                        "description": "Repository in owner/repo format, e.g. 'pallets/click'",
                    },
                },
                "required": ["repo_name"],
            },
        ),
        Tool(
            name="get_file_tree",
            description="Get the full file tree of a GitHub repository. Returns all files and directories with their paths, types, and sizes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {
                        "type": "string",
                        "description": "Repository in owner/repo format",
                    },
                },
                "required": ["repo_name"],
            },
        ),
        Tool(
            name="get_file_content",
            description="Read the content of a specific file from a GitHub repository. Automatically skips binary and oversized files.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {
                        "type": "string",
                        "description": "Repository in owner/repo format",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file within the repository, e.g. 'src/main.py'",
                    },
                },
                "required": ["repo_name", "file_path"],
            },
        ),
        Tool(
            name="get_issues",
            description="Get open issues from a GitHub repository, optionally filtered by labels like 'good first issue' or 'help wanted'.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {
                        "type": "string",
                        "description": "Repository in owner/repo format",
                    },
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by label names (optional)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max issues to return (default 20)",
                        "default": 20,
                    },
                },
                "required": ["repo_name"],
            },
        ),
        Tool(
            name="search_code",
            description="Semantic + keyword hybrid search over a repository's code. Requires the repo to be indexed first via index_repo.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {
                        "type": "string",
                        "description": "Repository in owner/repo format (must be indexed first)",
                    },
                    "query": {
                        "type": "string",
                        "description": "Natural language query or keyword to search for",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return (default 6)",
                        "default": 6,
                    },
                },
                "required": ["repo_name", "query"],
            },
        ),
        Tool(
            name="index_repo",
            description="Build a code search index (vector + BM25) for a repository. Must be called before search_code. Uses cache if available.",
            inputSchema={
                "type": "object",
                "properties": {
                    "repo_name": {
                        "type": "string",
                        "description": "Repository in owner/repo format",
                    },
                    "force_rebuild": {
                        "type": "boolean",
                        "description": "Force rebuild even if cache exists (default false)",
                        "default": False,
                    },
                },
                "required": ["repo_name"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

_indexer_cache: dict = {}


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        result = _dispatch_tool(name, arguments)
        return [TextContent(type="text", text=result)]
    except Exception as exc:
        logger.error("Tool %s failed: %s", name, exc)
        error_response = json.dumps({
            "error": True,
            "tool": name,
            "message": str(exc),
        })
        return [TextContent(type="text", text=error_response)]


def _dispatch_tool(name: str, arguments: dict) -> str:
    if name == "get_repo_meta":
        return _tool_get_repo_meta(arguments["repo_name"])
    elif name == "get_file_tree":
        return _tool_get_file_tree(arguments["repo_name"])
    elif name == "get_file_content":
        return _tool_get_file_content(arguments["repo_name"], arguments["file_path"])
    elif name == "get_issues":
        return _tool_get_issues(
            arguments["repo_name"],
            labels=arguments.get("labels"),
            limit=arguments.get("limit", 20),
        )
    elif name == "search_code":
        return _tool_search_code(
            arguments["repo_name"],
            arguments["query"],
            top_k=arguments.get("top_k", 6),
        )
    elif name == "index_repo":
        return _tool_index_repo(
            arguments["repo_name"],
            force_rebuild=arguments.get("force_rebuild", False),
        )
    else:
        raise ValueError(f"Unknown tool: {name}")


def _tool_get_repo_meta(repo_name: str) -> str:
    from dataclasses import asdict
    client = _get_github_client()
    meta = client.get_repo_meta(repo_name)
    return json.dumps(asdict(meta), indent=2, ensure_ascii=False)


def _tool_get_file_tree(repo_name: str) -> str:
    client = _get_github_client()
    nodes = client.get_file_tree(repo_name)
    tree = [{"path": n.path, "type": n.type, "size": n.size} for n in nodes]
    return json.dumps(tree, indent=2)


def _tool_get_file_content(repo_name: str, file_path: str) -> str:
    client = _get_github_client()
    content = client.get_file_content(repo_name, file_path)
    if content is None:
        return json.dumps({"error": True, "message": f"Cannot read {file_path} (binary, too large, or not found)"})
    return content


def _tool_get_issues(repo_name: str, labels: list[str] | None = None, limit: int = 20) -> str:
    from dataclasses import asdict
    client = _get_github_client()
    issues = client.get_issues(repo_name, labels=labels or [], limit=limit)
    return json.dumps([asdict(i) for i in issues], indent=2, ensure_ascii=False)


def _tool_search_code(repo_name: str, query: str, top_k: int = 6) -> str:
    if repo_name not in _indexer_cache:
        return json.dumps({"error": True, "message": f"Repository {repo_name} not indexed. Call index_repo first."})

    hybrid = _indexer_cache[repo_name]["hybrid"]
    results = hybrid.search(repo_name, query, top_k=top_k)
    if not results:
        return json.dumps({"results": [], "message": f"No results for: {query}"})

    output = []
    for r in results:
        output.append({
            "file_path": r.file_path,
            "start_line": r.start_line,
            "end_line": r.end_line,
            "symbol_name": r.symbol_name,
            "language": r.language,
            "source": r.source,
            "content": r.content[:500],
        })
    return json.dumps(output, indent=2, ensure_ascii=False)


def _tool_index_repo(repo_name: str, force_rebuild: bool = False) -> str:
    from reposense.rag.indexer import CodeIndexer
    from reposense.rag.hybrid import HybridRetriever

    if repo_name in _indexer_cache and not force_rebuild:
        count = _indexer_cache[repo_name]["chunks"]
        return json.dumps({"status": "already_indexed", "total_chunks": count})

    cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "reposense")
    chroma_dir = os.path.join(cache_dir, "chroma")
    indexer = CodeIndexer(chroma_dir)
    hybrid = HybridRetriever(indexer)

    client = _get_github_client()
    tree_nodes = client.get_file_tree(repo_name)
    file_tree = [{"path": n.path, "type": n.type, "size": n.size} for n in tree_nodes]

    stats = indexer.build_index_from_github(repo_name, file_tree=file_tree, max_files=200)

    from reposense.rag.bm25 import BM25Searcher
    bm25_path = os.path.join(cache_dir, f"bm25_{repo_name.replace('/', '_')}.pkl")
    bm25 = BM25Searcher()
    if not bm25.load(bm25_path):
        from reposense.rag.indexer import _is_indexable
        paths = [n["path"] for n in file_tree if n["type"] == "blob" and _is_indexable(n["path"])][:200]
        contents: dict[str, str] = {}
        for p in paths:
            c = client.get_file_content(repo_name, p)
            if c is not None:
                contents[p] = c
        hybrid.build_bm25_index(contents)
        hybrid._bm25.save(bm25_path)
    else:
        hybrid._bm25 = bm25

    _indexer_cache[repo_name] = {
        "hybrid": hybrid,
        "chunks": stats.get("total_chunks", 0),
    }

    return json.dumps({
        "status": "indexed",
        "total_chunks": stats.get("total_chunks", 0),
        "total_files": stats.get("total_files", 0),
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
