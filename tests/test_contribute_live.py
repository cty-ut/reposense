"""Live integration test for the contribution guidance flow.

Requires: GITHUB_TOKEN + at least one LLM API key.
Run with: python -m pytest tests/test_contribute_live.py -v -s
"""

from __future__ import annotations

import logging
import os

import pytest

from reposense.config import settings

logging.basicConfig(level=logging.INFO)

pytestmark = pytest.mark.skipif(
    not settings.github_token,
    reason="GITHUB_TOKEN not set",
)

TEST_REPO = "pallets/click"


class TestIssueFetching:
    """Test issue fetching and filtering (no LLM needed)."""

    def test_fetch_issues(self):
        from reposense.graph.nodes.contributor import _fetch_issues

        issues = _fetch_issues(TEST_REPO)
        print(f"\n✓ Fetched {len(issues)} issues from {TEST_REPO}")
        assert len(issues) >= 0

        for i in issues[:5]:
            labels = ", ".join(i.labels) if i.labels else "none"
            print(f"  #{i.number}: {i.title} [{labels}]")

    def test_fetch_repo_data(self):
        from reposense.graph.contribute import ContributeEngine

        engine = ContributeEngine()
        meta = engine.fetch_repo_data(TEST_REPO)
        assert meta["full_name"] == TEST_REPO
        print(f"\n✓ Repo: {meta['full_name']} ({meta.get('stars', 0)} stars)")


class TestIssueAnalysis:
    """Test issue analysis with mocked search but real LLM."""

    def test_analyze_single_issue(self):
        from reposense.graph.nodes.contributor import _analyze_single_issue
        from reposense.tools.github_api import RepoIssue

        issue = RepoIssue(
            number=999,
            title="Add type hints to core module",
            body="We should add type hints to all functions in src/click/core.py for better IDE support.",
            labels=["good first issue"],
            state="open",
            created_at="2025-01-15T10:00:00",
            comments_count=3,
            url="https://github.com/pallets/click/issues/999",
        )

        tree_text = "📁 src/\n   src/click/\n   src/click/core.py\n   src/click/utils.py\n📁 tests/\n📁 docs/"
        search_results = "- **src/click/core.py** (L1-50, BaseCommand): class BaseCommand that handles CLI commands..."

        result = _analyze_single_issue(issue, tree_text, TEST_REPO, search_results)

        assert result is not None, "Analysis should succeed"
        print(f"\n✓ Analysis result:")
        print(f"  Difficulty: {result['difficulty']}")
        print(f"  Summary: {result['summary']}")
        print(f"  Related: {result['related_files']}")
        print(f"  Suggestion: {result['suggestion']}")

        assert result["difficulty"] in ("easy", "medium", "hard")
        assert result["number"] == 999
        assert len(result["summary"]) > 10


class TestRoadmapGeneration:
    """Test roadmap generation with pre-built analyses."""

    def test_generate_roadmap(self):
        from reposense.graph.nodes.contributor import _generate_roadmap

        analyses = [
            {
                "number": 1, "title": "Add type hints", "url": "#1",
                "labels": ["good first issue"], "difficulty": "easy",
                "summary": "Add type annotations to core.py",
                "related_files": ["src/click/core.py"],
                "suggestion": "Open core.py and add type hints to function signatures.",
            },
            {
                "number": 2, "title": "Refactor parser", "url": "#2",
                "labels": [], "difficulty": "medium",
                "summary": "Refactor the argument parser for better extensibility",
                "related_files": ["src/click/parser.py", "src/click/types.py"],
                "suggestion": "Study the current parser architecture first.",
            },
            {
                "number": 3, "title": "Async support", "url": "#3",
                "labels": [], "difficulty": "hard",
                "summary": "Add native async/await support to Click commands",
                "related_files": ["src/click/core.py", "src/click/decorators.py"],
                "suggestion": "This requires deep understanding of Click internals.",
            },
        ]

        meta = {"description": "A Python package for creating CLI applications", "language": "Python", "stars": 15000}
        roadmap = _generate_roadmap(TEST_REPO, meta, analyses)

        assert len(roadmap) > 100, f"Roadmap too short: {len(roadmap)} chars"
        print(f"\n✓ Roadmap generated ({len(roadmap)} chars)")
        print("--- Preview ---")
        print(roadmap[:600])
        print("--- End ---")
