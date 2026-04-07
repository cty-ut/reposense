"""Unit tests for the MCP server tool definitions and dispatch."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from reposense.mcp_server import _dispatch_tool, app


class TestToolDispatch:
    @patch("reposense.mcp_server._get_github_client")
    def test_get_repo_meta(self, mock_get_client):
        from reposense.tools.github_api import RepoMeta
        mock_client = MagicMock()
        mock_client.get_repo_meta.return_value = RepoMeta(
            full_name="owner/repo",
            description="A test repo",
            language="Python",
            languages={"Python": 10000},
            stars=42,
            forks=5,
            open_issues=3,
            default_branch="main",
            created_at="2024-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
            topics=["test"],
            license="MIT",
            contributors_count=2,
        )
        mock_get_client.return_value = mock_client

        result = _dispatch_tool("get_repo_meta", {"repo_name": "owner/repo"})
        data = json.loads(result)
        assert data["full_name"] == "owner/repo"
        assert data["stars"] == 42

    @patch("reposense.mcp_server._get_github_client")
    def test_get_file_tree(self, mock_get_client):
        from reposense.tools.github_api import FileNode
        mock_client = MagicMock()
        mock_client.get_file_tree.return_value = [
            FileNode(path="src/main.py", type="blob", size=500),
            FileNode(path="README.md", type="blob", size=1000),
        ]
        mock_get_client.return_value = mock_client

        result = _dispatch_tool("get_file_tree", {"repo_name": "owner/repo"})
        data = json.loads(result)
        assert len(data) == 2
        assert data[0]["path"] == "src/main.py"

    @patch("reposense.mcp_server._get_github_client")
    def test_get_file_content(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.get_file_content.return_value = "print('hello')"
        mock_get_client.return_value = mock_client

        result = _dispatch_tool("get_file_content", {
            "repo_name": "owner/repo",
            "file_path": "main.py",
        })
        assert "print('hello')" in result

    @patch("reposense.mcp_server._get_github_client")
    def test_get_file_content_binary(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.get_file_content.return_value = None
        mock_get_client.return_value = mock_client

        result = _dispatch_tool("get_file_content", {
            "repo_name": "owner/repo",
            "file_path": "image.png",
        })
        data = json.loads(result)
        assert data["error"] is True

    @patch("reposense.mcp_server._get_github_client")
    def test_get_issues(self, mock_get_client):
        from reposense.tools.github_api import RepoIssue
        mock_client = MagicMock()
        mock_client.get_issues.return_value = [
            RepoIssue(
                number=1, title="Bug", body="Fix it",
                labels=["bug"], state="open",
                created_at="2025-01-01", comments_count=2,
                url="https://github.com/owner/repo/issues/1",
            ),
        ]
        mock_get_client.return_value = mock_client

        result = _dispatch_tool("get_issues", {"repo_name": "owner/repo"})
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["number"] == 1

    def test_search_code_without_index(self):
        from reposense.mcp_server import _indexer_cache
        _indexer_cache.clear()

        result = _dispatch_tool("search_code", {
            "repo_name": "owner/repo",
            "query": "main function",
        })
        data = json.loads(result)
        assert data["error"] is True
        assert "not indexed" in data["message"]

    def test_unknown_tool(self):
        with pytest.raises(ValueError, match="Unknown tool"):
            _dispatch_tool("nonexistent_tool", {})


class TestToolListing:
    @pytest.mark.asyncio
    async def test_lists_all_tools(self):
        from reposense.mcp_server import list_tools
        tools = await list_tools()
        names = {t.name for t in tools}
        assert "get_repo_meta" in names
        assert "get_file_tree" in names
        assert "get_file_content" in names
        assert "get_issues" in names
        assert "search_code" in names
        assert "index_repo" in names

    @pytest.mark.asyncio
    async def test_tool_schemas_have_required_fields(self):
        from reposense.mcp_server import list_tools
        tools = await list_tools()
        for tool in tools:
            assert tool.name
            assert tool.description
            assert "properties" in tool.inputSchema
