"""ContributeEngine — end-to-end contribution guidance with optional Q&A follow-up."""

from __future__ import annotations

import logging
import os
from dataclasses import asdict

from reposense.config import settings
from reposense.graph.nodes.contributor import (
    _fetch_issues,
    _issue_to_dict,
    _analyze_single_issue,
    _validate_file_refs,
    _search_related_code,
    _format_top_level_tree,
    _generate_roadmap,
    MAX_ISSUES_TO_ANALYZE,
    RepoIssue,
)
from reposense.graph.qa_graph import QAEngine
from reposense.tools.github_api import GitHubClient

logger = logging.getLogger(__name__)


class ContributeEngine:
    """Orchestrates the full contribution guidance flow.

    1. Fetch repository data (meta, file tree)
    2. Fetch and filter issues
    3. Build code index (reuse QAEngine)
    4. Analyze each issue with code search
    5. Generate contribution roadmap
    6. Allow follow-up Q&A on specific issues
    """

    def __init__(self) -> None:
        self._qa_engine = QAEngine()
        self._repo_name: str = ""
        self._repo_meta: dict = {}
        self._file_tree: list[dict] = []
        self._issues: list[RepoIssue] = []
        self._issue_analyses: list[dict] = []
        self._roadmap: str = ""

    @property
    def qa_engine(self) -> QAEngine:
        return self._qa_engine

    @property
    def issue_analyses(self) -> list[dict]:
        return self._issue_analyses

    @property
    def roadmap(self) -> str:
        return self._roadmap

    def fetch_repo_data(self, repo_name: str) -> dict:
        """Fetch repo metadata and file tree. Returns meta dict."""
        self._repo_name = repo_name
        with GitHubClient(token=settings.github_token) as client:
            meta = client.get_repo_meta(repo_name)
            tree_nodes = client.get_file_tree(repo_name)
            self._file_tree = [
                {"path": n.path, "type": n.type, "size": n.size}
                for n in tree_nodes
            ]
        self._repo_meta = asdict(meta)
        return self._repo_meta

    def fetch_issues(self) -> list[dict]:
        """Fetch and filter issues from the repository."""
        self._issues = _fetch_issues(self._repo_name)
        logger.info("Fetched %d issues for %s", len(self._issues), self._repo_name)
        return [_issue_to_dict(i) for i in self._issues]

    def build_index(self) -> dict:
        """Build the code search index (vector + BM25) for code-issue association."""
        return self._qa_engine.build_index(self._repo_name, self._file_tree)

    def analyze_issues(self) -> list[dict]:
        """Analyze each issue: difficulty, related code files, suggestions."""
        tree_text = _format_top_level_tree(self._file_tree)
        issues_to_analyze = self._issues[:MAX_ISSUES_TO_ANALYZE]

        analyses: list[dict] = []
        for issue in issues_to_analyze:
            if self._qa_engine.is_indexed:
                search_results = _search_related_code(
                    issue, self._qa_engine._hybrid, self._repo_name,
                )
            else:
                search_results = "(code index not available)"

            analysis = _analyze_single_issue(
                issue, tree_text, self._repo_name, search_results,
            )
            if analysis:
                analysis = _validate_file_refs(analysis, self._file_tree)
                analyses.append(analysis)

        self._issue_analyses = analyses
        return analyses

    def generate_roadmap(self) -> str:
        """Generate the final contribution roadmap from analyzed issues."""
        self._roadmap = _generate_roadmap(
            self._repo_name, self._repo_meta, self._issue_analyses, self._file_tree,
        )
        return self._roadmap

    def ask_about_issue(
        self,
        issue_number: int,
        question: str,
        *,
        thread_id: str = "contribute",
    ) -> dict:
        """Ask a follow-up question about a specific issue using the Q&A engine."""
        issue_info = next(
            (a for a in self._issue_analyses if a["number"] == issue_number),
            None,
        )

        if issue_info:
            context = (
                f"The user is asking about Issue #{issue_number}: {issue_info['title']}.\n"
                f"Summary: {issue_info['summary']}\n"
                f"Difficulty: {issue_info['difficulty']}\n"
                f"Related files: {', '.join(issue_info.get('related_files', []))}\n"
                f"Suggestion: {issue_info['suggestion']}\n\n"
                f"User question: {question}"
            )
        else:
            context = f"The user is asking about Issue #{issue_number}. Question: {question}"

        return self._qa_engine.ask(
            context,
            thread_id=thread_id,
            repo_meta=self._repo_meta,
        )
