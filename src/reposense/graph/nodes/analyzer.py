"""Analyzer node — uses LLM to understand repository structure and code."""

from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import SystemMessage, HumanMessage

from reposense.graph.state import RepoSenseState
from reposense.llm import invoke_with_fallback

logger = logging.getLogger(__name__)

JSON_PARSE_RETRIES = 2


def _extract_json(text: str) -> dict | None:
    """Robustly extract a JSON object from LLM output."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{", text)
    if match:
        depth, start = 0, match.start()
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    return None


def _invoke_json(system: str, user: str, retry_label: str) -> dict | None:
    """Invoke LLM (with fallback) expecting JSON, retry on parse failure."""
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    for attempt in range(JSON_PARSE_RETRIES):
        try:
            content = invoke_with_fallback(messages)
        except RuntimeError as exc:
            logger.error("%s: LLM invocation failed: %s", retry_label, exc)
            return None
        result = _extract_json(content)
        if result is not None:
            return result
        logger.warning(
            "%s: JSON parse failed (attempt %d/%d). Raw: %s",
            retry_label, attempt + 1, JSON_PARSE_RETRIES, content[:300],
        )
        messages = [
            SystemMessage(content=system),
            HumanMessage(content=user + "\n\nREMINDER: You MUST respond with valid JSON only. No markdown, no commentary."),
        ]
    return None


# ---------------------------------------------------------------------------
# Level 1: Directory-level analysis
# ---------------------------------------------------------------------------

_LEVEL1_SYSTEM = """\
You are a senior software engineer analyzing an open-source repository.
Given the repository metadata and full file tree, you must:
1. Describe the role/purpose of each top-level directory (1 sentence each).
2. Identify the project type (web app / CLI tool / library / framework / etc.).
3. List the 3-8 most important directories that contain core logic (not docs, tests, CI, etc.).
4. Identify likely entry point files (main.py, index.ts, app.py, etc.).

IMPORTANT: Every file path you mention MUST exist in the provided file tree. Do NOT invent paths.

Respond in this EXACT JSON format. All values must be simple strings (no newlines inside strings). No markdown fences.
{
  "directory_roles": {"dir_name": "one sentence role description", ...},
  "project_type": "string",
  "core_directories": ["dir1", "dir2"],
  "entry_points": ["path/to/entry1.py"]
}"""


def _format_tree_for_prompt(file_tree: list[dict]) -> str:
    """Build a compact text representation of the file tree."""
    lines: list[str] = []
    for node in file_tree:
        prefix = "📁 " if node["type"] == "tree" else "   "
        size_info = f" ({node['size']} bytes)" if node["type"] == "blob" and node["size"] > 0 else ""
        lines.append(f"{prefix}{node['path']}{size_info}")
    return "\n".join(lines)


def level1_analyzer_node(state: RepoSenseState) -> dict:
    """Level 1: Analyze directory structure, identify project type and core dirs."""
    meta = state["repo_meta"]
    file_tree = state["file_tree"]

    tree_text = _format_tree_for_prompt(file_tree)

    user_msg = f"""\
## Repository: {meta['full_name']}
**Description:** {meta.get('description', 'N/A')}
**Primary language:** {meta.get('language', 'Unknown')}
**Languages:** {meta.get('languages', {})}
**Stars:** {meta.get('stars', 0)} | **Forks:** {meta.get('forks', 0)}

## File Tree
{tree_text}

Analyze this repository now. Remember: only reference paths that exist in the tree above."""

    result = _invoke_json(_LEVEL1_SYSTEM, user_msg, "Level 1")
    if result is None:
        return {"error": "Level 1 analysis failed: could not parse JSON from LLM after retries"}

    # Normalize directory_roles: accept both dict and string
    roles = result.get("directory_roles", {})
    if isinstance(roles, dict):
        roles_text = "\n".join(f"- **{d}**: {r}" for d, r in roles.items())
    else:
        roles_text = str(roles)

    return {
        "directory_roles": roles_text,
        "project_type": result.get("project_type", "unknown"),
        "core_directories": result.get("core_directories", []),
        "entry_points": result.get("entry_points", []),
    }


# ---------------------------------------------------------------------------
# Level 2: File-level summaries for core dirs
# ---------------------------------------------------------------------------

_LEVEL2_SYSTEM = """\
You are a senior software engineer. Given file previews (first ~50 lines) from the core directories of a repository, provide a one-line summary for each file describing what it does.

IMPORTANT: Only summarize the files provided. Do NOT invent files.

Also identify the 5-10 most important files that should be read in full to understand the project's core logic. Prefer source code over configs/tests.

Respond in this EXACT JSON format. All values must be short strings (no newlines). No markdown fences.
{
  "file_summaries": {"path/to/file.py": "one-line summary", ...},
  "key_files": ["path/to/important1.py", "path/to/important2.py"]
}"""


def level2_analyzer_node(state: RepoSenseState) -> dict:
    """Level 2: Summarize individual files and identify key files for deep read."""
    file_contents = state.get("file_contents") or {}
    meta = state["repo_meta"]

    if not file_contents:
        return {"file_summaries": {}, "key_files": []}

    previews = "\n\n".join(
        f"### {path}\n```\n{content}\n```"
        for path, content in sorted(file_contents.items())
    )

    user_msg = f"""\
## Repository: {meta['full_name']}
**Type:** {state.get('project_type', 'unknown')}
**Core directories:** {state.get('core_directories', [])}

## File Previews
{previews}

Summarize each file and pick the key files for deep reading."""

    result = _invoke_json(_LEVEL2_SYSTEM, user_msg, "Level 2")
    if result is None:
        logger.error("Level 2 analysis failed after retries")
        return {"file_summaries": {}, "key_files": []}

    return {
        "file_summaries": result.get("file_summaries", {}),
        "key_files": result.get("key_files", []),
    }


# ---------------------------------------------------------------------------
# Level 3: Deep analysis of key files
# ---------------------------------------------------------------------------

_LEVEL3_SYSTEM = """\
You are a senior software engineer doing a deep code review. Given the full source code of key files in a project, provide a detailed analysis for each file:
- What is the file's purpose?
- What are the key classes/functions and what do they do?
- How does this file relate to other parts of the project?

IMPORTANT: Every reference must match the actual code provided. Do NOT invent function names or class names.

Respond in this EXACT JSON format. Use short paragraphs as values (separate points with semicolons, not newlines). No markdown fences.
{
  "key_file_analyses": {
    "path/to/file.py": "detailed analysis as a single-line string; use semicolons to separate points",
    ...
  }
}"""


def level3_analyzer_node(state: RepoSenseState) -> dict:
    """Level 3: Deep-read key files and produce detailed analyses."""
    file_contents = state.get("file_contents") or {}
    key_files = state.get("key_files") or []
    meta = state["repo_meta"]

    key_contents = {
        path: file_contents[path]
        for path in key_files
        if path in file_contents
    }

    if not key_contents:
        return {"key_file_analyses": {}}

    code_blocks = "\n\n".join(
        f"### {path}\n```\n{content}\n```"
        for path, content in sorted(key_contents.items())
    )

    user_msg = f"""\
## Repository: {meta['full_name']}
**Type:** {state.get('project_type', 'unknown')}
**File summaries:** {json.dumps(state.get('file_summaries', {}), indent=2)}

## Key Files (full content)
{code_blocks}

Provide detailed analysis for each file."""

    result = _invoke_json(_LEVEL3_SYSTEM, user_msg, "Level 3")
    if result is None:
        logger.error("Level 3 analysis failed after retries")
        return {"key_file_analyses": {}}

    return {"key_file_analyses": result.get("key_file_analyses", {})}
