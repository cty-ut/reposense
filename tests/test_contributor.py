"""Unit tests for the contribution analysis engine."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from reposense.graph.nodes.contributor import (
    _format_top_level_tree,
    _fallback_roadmap,
    _issue_to_dict,
    BEGINNER_LABELS,
    MAX_ISSUES_TO_ANALYZE,
)
from reposense.tools.github_api import RepoIssue


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_issue(
    number: int = 1,
    title: str = "Fix bug",
    body: str = "There is a bug in the parser.",
    labels: list[str] | None = None,
    comments_count: int = 2,
) -> RepoIssue:
    return RepoIssue(
        number=number,
        title=title,
        body=body,
        labels=labels or [],
        state="open",
        created_at="2025-01-15T10:00:00",
        comments_count=comments_count,
        url=f"https://github.com/owner/repo/issues/{number}",
    )


SAMPLE_FILE_TREE = [
    {"path": "src", "type": "tree", "size": 0},
    {"path": "src/main.py", "type": "blob", "size": 500},
    {"path": "src/utils.py", "type": "blob", "size": 300},
    {"path": "tests", "type": "tree", "size": 0},
    {"path": "tests/test_main.py", "type": "blob", "size": 400},
    {"path": "README.md", "type": "blob", "size": 1000},
    {"path": "docs", "type": "tree", "size": 0},
    {"path": "docs/guide.md", "type": "blob", "size": 800},
]


# ---------------------------------------------------------------------------
# Tests: _format_top_level_tree
# ---------------------------------------------------------------------------

class TestFormatTree:
    def test_shows_top_level(self):
        result = _format_top_level_tree(SAMPLE_FILE_TREE, max_depth=1)
        assert "src" in result or "README.md" in result

    def test_respects_max_depth(self):
        result = _format_top_level_tree(SAMPLE_FILE_TREE, max_depth=1)
        assert "src/main.py" not in result

    def test_includes_files_at_depth(self):
        result = _format_top_level_tree(SAMPLE_FILE_TREE, max_depth=2)
        assert "src/main.py" in result

    def test_empty_tree(self):
        result = _format_top_level_tree([])
        assert result == ""


# ---------------------------------------------------------------------------
# Tests: _issue_to_dict
# ---------------------------------------------------------------------------

class TestIssueToDict:
    def test_basic_conversion(self):
        issue = _make_issue(number=42, title="Add feature X")
        d = _issue_to_dict(issue)
        assert d["number"] == 42
        assert d["title"] == "Add feature X"
        assert d["state"] == "open"

    def test_preserves_labels(self):
        issue = _make_issue(labels=["bug", "good first issue"])
        d = _issue_to_dict(issue)
        assert "bug" in d["labels"]
        assert "good first issue" in d["labels"]


# ---------------------------------------------------------------------------
# Tests: _fallback_roadmap
# ---------------------------------------------------------------------------

class TestFallbackRoadmap:
    def test_generates_sections_by_difficulty(self):
        analyses = [
            {"number": 1, "title": "Easy bug", "url": "#1", "labels": [],
             "difficulty": "easy", "summary": "Fix it", "related_files": ["a.py"], "suggestion": "Do this"},
            {"number": 2, "title": "Hard refactor", "url": "#2", "labels": [],
             "difficulty": "hard", "summary": "Refactor it", "related_files": [], "suggestion": "Good luck"},
        ]
        roadmap = _fallback_roadmap(analyses)
        assert "🟢" in roadmap
        assert "🔴" in roadmap
        assert "Easy bug" in roadmap
        assert "Hard refactor" in roadmap

    def test_skips_empty_sections(self):
        analyses = [
            {"number": 1, "title": "Medium task", "url": "#1", "labels": [],
             "difficulty": "medium", "summary": "Do it", "related_files": [], "suggestion": "Start here"},
        ]
        roadmap = _fallback_roadmap(analyses)
        assert "🟡" in roadmap
        assert "🟢" not in roadmap
        assert "🔴" not in roadmap

    def test_empty_analyses(self):
        roadmap = _fallback_roadmap([])
        assert "贡献路线图" in roadmap

    def test_shows_related_files(self):
        analyses = [
            {"number": 1, "title": "Bug", "url": "#1", "labels": [],
             "difficulty": "easy", "summary": "Fix", "related_files": ["src/main.py", "src/utils.py"],
             "suggestion": "Check main.py"},
        ]
        roadmap = _fallback_roadmap(analyses)
        assert "src/main.py" in roadmap
        assert "src/utils.py" in roadmap


# ---------------------------------------------------------------------------
# Tests: BEGINNER_LABELS
# ---------------------------------------------------------------------------

class TestBeginnerLabels:
    def test_contains_standard_labels(self):
        assert "good first issue" in BEGINNER_LABELS
        assert "help wanted" in BEGINNER_LABELS

    def test_has_multiple_labels(self):
        assert len(BEGINNER_LABELS) >= 5


# ---------------------------------------------------------------------------
# Tests: _analyze_single_issue (mocked LLM)
# ---------------------------------------------------------------------------

class TestAnalyzeSingleIssue:
    @patch("reposense.graph.nodes.contributor.invoke_with_fallback")
    def test_parses_valid_response(self, mock_invoke):
        from reposense.graph.nodes.contributor import _analyze_single_issue

        mock_invoke.return_value = json.dumps({
            "difficulty": "easy",
            "summary": "Add type hints to utils.py",
            "related_files": ["src/utils.py"],
            "suggestion": "Open utils.py and add type annotations to all functions.",
        })

        issue = _make_issue(number=10, title="Add type hints")
        result = _analyze_single_issue(issue, "src/\n  src/utils.py", "owner/repo", "")

        assert result is not None
        assert result["number"] == 10
        assert result["difficulty"] == "easy"
        assert "src/utils.py" in result["related_files"]

    @patch("reposense.graph.nodes.contributor.invoke_with_fallback")
    def test_handles_markdown_fenced_json(self, mock_invoke):
        from reposense.graph.nodes.contributor import _analyze_single_issue

        mock_invoke.return_value = '```json\n{"difficulty": "medium", "summary": "test", "related_files": [], "suggestion": "do it"}\n```'

        issue = _make_issue()
        result = _analyze_single_issue(issue, "", "owner/repo", "")

        assert result is not None
        assert result["difficulty"] == "medium"

    @patch("reposense.graph.nodes.contributor.invoke_with_fallback")
    def test_handles_llm_failure(self, mock_invoke):
        from reposense.graph.nodes.contributor import _analyze_single_issue

        mock_invoke.side_effect = RuntimeError("LLM down")

        issue = _make_issue()
        result = _analyze_single_issue(issue, "", "owner/repo", "")

        assert result is None

    @patch("reposense.graph.nodes.contributor.invoke_with_fallback")
    def test_handles_invalid_json(self, mock_invoke):
        from reposense.graph.nodes.contributor import _analyze_single_issue

        mock_invoke.return_value = "This is not JSON at all, just a plain text response."

        issue = _make_issue()
        result = _analyze_single_issue(issue, "", "owner/repo", "")

        assert result is None
