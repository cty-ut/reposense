"""Contributor node — analyzes issues and generates contribution guidance."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict

from langchain_core.messages import SystemMessage, HumanMessage

from reposense.config import settings
from reposense.graph.state import RepoSenseState
from reposense.llm import invoke_with_fallback
from reposense.tools.github_api import GitHubClient, RepoIssue

logger = logging.getLogger(__name__)

BEGINNER_LABELS = [
    "good first issue",
    "help wanted",
    "beginner",
    "easy",
    "starter",
    "first-timers-only",
    "up-for-grabs",
    "contributions welcome",
]

MAX_ISSUES = 20
MAX_ISSUES_TO_ANALYZE = 10


def _fetch_issues(repo_name: str) -> list[RepoIssue]:
    """Fetch open issues, prioritizing beginner-friendly ones."""
    with GitHubClient(token=settings.github_token) as client:
        beginner_issues: list[RepoIssue] = []
        for label in BEGINNER_LABELS:
            try:
                found = client.get_issues(repo_name, labels=[label], limit=10)
                for issue in found:
                    if issue.number not in {i.number for i in beginner_issues}:
                        beginner_issues.append(issue)
            except Exception:
                continue

        if len(beginner_issues) >= MAX_ISSUES:
            return beginner_issues[:MAX_ISSUES]

        remaining = MAX_ISSUES - len(beginner_issues)
        all_issues = client.get_issues(repo_name, limit=remaining + len(beginner_issues))
        seen = {i.number for i in beginner_issues}
        for issue in all_issues:
            if issue.number not in seen:
                beginner_issues.append(issue)
                if len(beginner_issues) >= MAX_ISSUES:
                    break

        return beginner_issues


def _issue_to_dict(issue: RepoIssue) -> dict:
    return asdict(issue)


# ---------------------------------------------------------------------------
# LLM prompts for issue analysis
# ---------------------------------------------------------------------------

_ISSUE_ANALYSIS_SYSTEM = """\
You are a senior open-source maintainer helping a newcomer understand issues in a repository.

Given an issue and the repository's directory structure, analyze the issue and provide:
1. A difficulty rating: "easy", "medium", or "hard"
2. A brief explanation of what the issue is asking (1-2 sentences)
3. Which files or directories are most likely relevant
4. A concrete suggestion for how to start working on it (2-3 sentences)

CRITICAL RULES:
- The "related_files" field MUST ONLY contain paths that appear EXACTLY in the provided file tree.
- Do NOT guess or invent file paths. If you are unsure which file is relevant, use a directory path from the tree.
- If no file in the tree seems relevant, set "related_files" to an empty list [].
- If the issue is unclear or too vague, say so and rate it as "hard".
- Be practical and specific in your suggestions.

Respond in this EXACT JSON format (no markdown fences):
{
  "difficulty": "easy" | "medium" | "hard",
  "summary": "what this issue is asking for",
  "related_files": ["exact/path/from/file_tree.py"],
  "suggestion": "how to start working on this issue"
}"""

_ROADMAP_SYSTEM = """\
You are a technical writer creating a "Contribution Roadmap" for a developer who wants to contribute to an open-source project.

Given the analyzed issues with their difficulty ratings, related files, and suggestions, generate a well-structured Markdown contribution guide.

Structure the guide as follows:

## 贡献路线图

Brief intro about the project and how to get started contributing.

### 🟢 入门级（Easy）
For each easy issue, show:
- Issue title and number (with link)
- What it asks for
- Related files
- How to start
- Estimated time

### 🟡 进阶级（Medium）
Same format for medium issues.

### 🔴 挑战级（Hard）
Same format for hard issues.

## 开始贡献的步骤
1. Fork the repository
2. Set up development environment
3. Pick an issue from above
4. ...

CRITICAL RULES:
- Write in Chinese (中文).
- ONLY reference file paths that appear in the "related_files" field of the analysis data. Do NOT invent, guess, or extrapolate any file paths.
- If a file path is not listed in "related_files", do NOT mention it.
- Be encouraging and practical.
- If there are no issues in a difficulty level, skip that section.
- Output ONLY the Markdown report."""


def _analyze_single_issue(
    issue: RepoIssue,
    file_tree_text: str,
    repo_name: str,
    search_results: str,
) -> dict | None:
    """Use LLM to analyze a single issue."""
    body_preview = (issue.body or "")[:1500]
    labels_str = ", ".join(issue.labels) if issue.labels else "none"

    user_msg = f"""\
## Repository: {repo_name}

## Issue #{issue.number}: {issue.title}
**Labels:** {labels_str}
**Comments:** {issue.comments_count}
**Created:** {issue.created_at}

**Description:**
{body_preview}

## Code Search Results (potentially related code)
{search_results}

## Repository File Tree (top-level structure)
{file_tree_text}

Analyze this issue now."""

    messages = [
        SystemMessage(content=_ISSUE_ANALYSIS_SYSTEM),
        HumanMessage(content=user_msg),
    ]
    try:
        content = invoke_with_fallback(messages)
    except RuntimeError as exc:
        logger.error("Issue analysis LLM call failed for #%d: %s", issue.number, exc)
        return None

    content = content.strip()
    if content.startswith("```"):
        import re
        content = re.sub(r"^```\w*\n?", "", content)
        content = re.sub(r"\n?```\s*$", "", content)
        content = content.strip()

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        import re
        match = re.search(r"\{", content)
        if match:
            depth, start = 0, match.start()
            for i in range(start, len(content)):
                if content[i] == "{":
                    depth += 1
                elif content[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            result = json.loads(content[start : i + 1])
                            break
                        except json.JSONDecodeError:
                            pass
            else:
                logger.warning("Could not parse issue analysis JSON for #%d", issue.number)
                return None
        else:
            logger.warning("No JSON found in issue analysis for #%d", issue.number)
            return None

    return {
        "number": issue.number,
        "title": issue.title,
        "url": issue.url,
        "labels": issue.labels,
        "difficulty": result.get("difficulty", "medium"),
        "summary": result.get("summary", ""),
        "related_files": result.get("related_files", []),
        "suggestion": result.get("suggestion", ""),
    }


def _validate_file_refs(analysis: dict, file_tree: list[dict]) -> dict:
    """Remove file paths from related_files that don't exist in the repo.

    Only keeps paths that correspond to actual files (blobs), not directories.
    If the LLM gives a bare filename, resolves it to the full path.
    """
    blob_paths = {n["path"] for n in file_tree if n.get("type") == "blob"}
    raw_files = analysis.get("related_files", [])
    validated = []
    for f in raw_files:
        f = f.strip().rstrip("/")
        if f in blob_paths:
            validated.append(f)
        elif any(p.endswith("/" + f) or p == f for p in blob_paths):
            match = next((p for p in blob_paths if p.endswith("/" + f) or p == f), None)
            if match:
                validated.append(match)
        else:
            logger.debug("Filtered hallucinated path: %s (issue #%d)", f, analysis.get("number", 0))
    analysis["related_files"] = validated
    return analysis


def _format_top_level_tree(file_tree: list[dict], max_depth: int = 2) -> str:
    """Build a compact tree showing only top-level structure."""
    lines: list[str] = []
    for node in file_tree:
        depth = node["path"].count("/")
        if depth < max_depth:
            prefix = "📁 " if node["type"] == "tree" else "   "
            lines.append(f"{prefix}{node['path']}")
    return "\n".join(lines[:200])


def _search_related_code(
    issue: RepoIssue,
    hybrid_retriever,
    repo_name: str,
) -> str:
    """Use hybrid search to find code related to an issue."""
    query = f"{issue.title} {(issue.body or '')[:300]}"
    try:
        results = hybrid_retriever.search(repo_name, query, top_k=5)
        if not results:
            return "(no related code found)"
        lines = []
        for r in results:
            lines.append(f"- **{r.file_path}** (L{r.start_line}-{r.end_line}, {r.symbol_name}): {r.content[:150]}...")
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("Search failed for issue #%d: %s", issue.number, exc)
        return "(search unavailable)"


def _sanitize_roadmap_paths(roadmap: str, file_tree: list[dict]) -> str:
    """Remove or flag file paths in roadmap markdown that don't exist in the repo."""
    import re as _re
    valid_paths = {n["path"] for n in file_tree}

    path_re = _re.compile(r"`([^`]+\.[a-zA-Z]{1,5})`")

    def _replace(m: _re.Match) -> str:
        p = m.group(1).strip("/")
        if p in valid_paths:
            return m.group(0)
        if any(vp.endswith("/" + p) or vp == p for vp in valid_paths):
            return m.group(0)
        if any(vp.startswith(p + "/") for vp in valid_paths):
            return m.group(0)
        logger.debug("Removing hallucinated path from roadmap: %s", p)
        return ""

    return path_re.sub(_replace, roadmap)


def _generate_roadmap(
    repo_name: str,
    repo_meta: dict,
    issue_analyses: list[dict],
    file_tree: list[dict] | None = None,
) -> str:
    """Use LLM to generate the final contribution roadmap."""
    analyses_json = json.dumps(issue_analyses, indent=2, ensure_ascii=False)

    user_msg = f"""\
## Repository: {repo_name}
**Description:** {repo_meta.get('description', 'N/A')}
**Language:** {repo_meta.get('language', 'Unknown')}
**Stars:** {repo_meta.get('stars', 0)}

## Analyzed Issues
{analyses_json}

Generate the contribution roadmap now."""

    try:
        content = invoke_with_fallback(
            [SystemMessage(content=_ROADMAP_SYSTEM), HumanMessage(content=user_msg)],
            temperature=0.3,
        )
        if file_tree:
            content = _sanitize_roadmap_paths(content, file_tree)
        return content
    except RuntimeError as exc:
        logger.error("Roadmap generation failed: %s", exc)
        return _fallback_roadmap(issue_analyses)


def _fallback_roadmap(issue_analyses: list[dict]) -> str:
    """Generate a simple roadmap without LLM if it fails."""
    easy = [a for a in issue_analyses if a["difficulty"] == "easy"]
    medium = [a for a in issue_analyses if a["difficulty"] == "medium"]
    hard = [a for a in issue_analyses if a["difficulty"] == "hard"]

    sections = ["# 贡献路线图\n"]

    for label, emoji, items in [
        ("入门级", "🟢", easy),
        ("进阶级", "🟡", medium),
        ("挑战级", "🔴", hard),
    ]:
        if not items:
            continue
        sections.append(f"\n## {emoji} {label}\n")
        for a in items:
            sections.append(f"### Issue #{a['number']}: {a['title']}")
            sections.append(f"- **链接:** {a['url']}")
            sections.append(f"- **概述:** {a['summary']}")
            if a["related_files"]:
                sections.append(f"- **相关文件:** {', '.join(a['related_files'])}")
            sections.append(f"- **建议:** {a['suggestion']}\n")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Graph node entry points
# ---------------------------------------------------------------------------

def issue_fetcher_node(state: RepoSenseState) -> dict:
    """Fetch and store issues from the repository."""
    repo_name = state.get("repo_name", "")
    if not repo_name:
        return {"error": "No repo_name in state"}

    logger.info("Fetching issues for %s", repo_name)
    issues = _fetch_issues(repo_name)
    logger.info("Fetched %d issues for %s", len(issues), repo_name)

    return {"issues": [_issue_to_dict(i) for i in issues]}


def issue_analyzer_node(state: RepoSenseState) -> dict:
    """Analyze each issue: difficulty, related code, suggestions."""
    repo_name = state.get("repo_name", "")
    issues_data = state.get("issues", [])
    file_tree = state.get("file_tree", [])

    if not issues_data:
        return {"issue_analyses": [], "error": "No issues found in this repository."}

    tree_text = _format_top_level_tree(file_tree)
    issues_to_analyze = issues_data[:MAX_ISSUES_TO_ANALYZE]

    analyses: list[dict] = []
    for issue_dict in issues_to_analyze:
        issue = RepoIssue(**issue_dict)

        search_results = "(search not available — index not built)"

        analysis = _analyze_single_issue(issue, tree_text, repo_name, search_results)
        if analysis:
            analysis = _validate_file_refs(analysis, file_tree)
            analyses.append(analysis)
            logger.info(
                "Analyzed issue #%d: difficulty=%s, %d related files",
                issue.number, analysis["difficulty"], len(analysis["related_files"]),
            )

    return {"issue_analyses": analyses}


def issue_analyzer_with_search_node(state: RepoSenseState, hybrid_retriever) -> dict:
    """Analyze issues with code search support (used when index is available)."""
    repo_name = state.get("repo_name", "")
    issues_data = state.get("issues", [])
    file_tree = state.get("file_tree", [])

    if not issues_data:
        return {"issue_analyses": [], "error": "No issues found in this repository."}

    tree_text = _format_top_level_tree(file_tree)
    issues_to_analyze = issues_data[:MAX_ISSUES_TO_ANALYZE]

    analyses: list[dict] = []
    for issue_dict in issues_to_analyze:
        issue = RepoIssue(**issue_dict)
        search_results = _search_related_code(issue, hybrid_retriever, repo_name)
        analysis = _analyze_single_issue(issue, tree_text, repo_name, search_results)
        if analysis:
            analysis = _validate_file_refs(analysis, file_tree)
            analyses.append(analysis)
            logger.info(
                "Analyzed issue #%d: difficulty=%s, %d related files",
                issue.number, analysis["difficulty"], len(analysis["related_files"]),
            )

    return {"issue_analyses": analyses}


def roadmap_node(state: RepoSenseState) -> dict:
    """Generate the final contribution roadmap report."""
    repo_name = state.get("repo_name", "")
    repo_meta = state.get("repo_meta", {})
    issue_analyses = state.get("issue_analyses", [])
    file_tree = state.get("file_tree", [])

    if not issue_analyses:
        return {"contribution_report": "该仓库当前没有找到可分析的 Issue。"}

    report = _generate_roadmap(repo_name, repo_meta, issue_analyses, file_tree)
    return {"contribution_report": report}
