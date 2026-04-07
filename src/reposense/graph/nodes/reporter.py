"""Reporter node — assembles the final Markdown panorama report."""

from __future__ import annotations

import json
import logging

from langchain_core.messages import SystemMessage, HumanMessage

from reposense.graph.state import RepoSenseState
from reposense.llm import invoke_with_fallback

logger = logging.getLogger(__name__)

_REPORT_SYSTEM = """\
You are a technical writer producing a "Project Panorama Report" for a developer who wants to quickly understand an open-source repository.

Given structured analysis data, generate a Markdown report with these sections:

## 项目概览
- Name, one-sentence summary, languages, scale (files/lines), stars, license

## 技术栈
- Core dependencies, test framework, build tools (inferred from config files)

## 目录结构解读
- Tree-style layout of important directories with role annotations

## 入口与核心流程
- Entry point files, core execution flow, suggested reading order (numbered)

## 架构亮点与设计模式
- Notable design patterns, architectural decisions observed in the code

Rules:
- Write in Chinese (中文).
- Every file path you mention MUST come from the analysis data provided — do NOT invent paths.
- Be concise but informative. Target ~400-600 words.
- Use Markdown formatting (headers, lists, code blocks) for readability.
- Output ONLY the report, no extra commentary."""


def reporter_node(state: RepoSenseState) -> dict:
    """Generate the final panorama report from all analysis results."""
    meta = state.get("repo_meta", {})
    file_tree = state.get("file_tree", [])

    file_count = sum(1 for n in file_tree if n.get("type") == "blob")
    dir_count = sum(1 for n in file_tree if n.get("type") == "tree")

    analysis_data = f"""\
## Repository Metadata
- Full name: {meta.get('full_name', 'N/A')}
- Description: {meta.get('description', 'N/A')}
- Primary language: {meta.get('language', 'Unknown')}
- Languages: {meta.get('languages', {})}
- Stars: {meta.get('stars', 0)} | Forks: {meta.get('forks', 0)} | Contributors: {meta.get('contributors_count', 'N/A')}
- License: {meta.get('license', 'N/A')}
- Topics: {meta.get('topics', [])}
- Scale: {file_count} files, {dir_count} directories

## Project Type
{state.get('project_type', 'unknown')}

## Directory Roles
{state.get('directory_roles', 'N/A')}

## Core Directories
{state.get('core_directories', [])}

## Entry Points
{state.get('entry_points', [])}

## File Summaries
{json.dumps(state.get('file_summaries', {}), indent=2, ensure_ascii=False)}

## Key File Analyses
{json.dumps(state.get('key_file_analyses', {}), indent=2, ensure_ascii=False)}"""

    try:
        content = invoke_with_fallback(
            [SystemMessage(content=_REPORT_SYSTEM), HumanMessage(content=analysis_data)],
            temperature=0.3,
        )
    except RuntimeError as exc:
        logger.error("Report generation failed: %s", exc)
        return {"report": f"Report generation failed: {exc}"}

    return {"report": content}
