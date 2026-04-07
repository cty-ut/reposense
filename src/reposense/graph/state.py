"""RepoSenseState — the single state object that flows through the entire graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypedDict

from langchain_core.messages import BaseMessage


class RepoSenseState(TypedDict, total=False):
    # ── User input ──────────────────────────────────────────────
    repo_url: str                          # e.g. "https://github.com/owner/repo"
    repo_name: str                         # e.g. "owner/repo" (parsed from URL)
    user_query: str                        # free-form question from the user
    messages: list[BaseMessage]            # conversation history

    # ── Intent routing ──────────────────────────────────────────
    intent: str                            # "scan" | "ask" | "contribute"

    # ── Repository data (populated by scanner) ──────────────────
    repo_meta: dict                        # from GitHubClient.get_repo_meta()
    file_tree: list[dict]                  # [{path, type, size}, ...]
    file_contents: dict[str, str]          # path → content (cached reads)

    # ── Analysis results (populated by analyzer) ────────────────
    directory_roles: str                   # LLM output: role of each top-level dir
    core_directories: list[str]            # dirs the LLM flagged as important
    file_summaries: dict[str, str]         # path → one-line summary
    key_files: list[str]                   # files the LLM flagged for deep read
    key_file_analyses: dict[str, str]      # path → detailed analysis
    project_type: str                      # "web app" / "CLI tool" / "library" / ...
    entry_points: list[str]               # identified entry files

    # ── Q&A (populated by ReAct agent) ─────────────────────────
    qa_answer: str                         # final answer from the agent
    qa_references: list[dict]              # [{file_path, start_line, end_line, snippet}, ...]
    qa_verified: dict                      # verification result from the checker
    react_iterations: int                  # how many ReAct loops were used
    rag_indexed: bool                      # whether the RAG index has been built

    # ── Contribution guidance ────────────────────────────────────
    issues: list[dict]                     # raw issues from GitHub
    issue_analyses: list[dict]             # [{number, title, difficulty, related_files, suggestion}, ...]
    contribution_report: str               # final contribution roadmap (Markdown)

    # ── Output ──────────────────────────────────────────────────
    report: str                            # final Markdown report

    # ── Control ─────────────────────────────────────────────────
    error: str                             # error message if something went wrong
