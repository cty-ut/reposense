"""Verifier node — checks LLM answers for hallucinated file paths and code references."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_FILE_PATH_RE = re.compile(
    r"(?:`([^`]+\.[a-zA-Z]{1,5})`)"       # `path/to/file.py`
    r"|(?:^|\s)([\w./\\-]+\.[a-zA-Z]{1,5})"  # bare path/to/file.py
)

_LINE_REF_RE = re.compile(
    r"(?:lines?\s+(\d+)(?:\s*[-–~]\s*(\d+))?)"  # line 42, lines 10-20
    r"|(?:第\s*(\d+)\s*[-–~]\s*(\d+)\s*行)"       # 第10-20行
)


_DENIAL_BEFORE = re.compile(
    r"(?:not\s+found|no\s+such\s+file|not\s+present|"
    r"cannot\s+find|could\s+not\s+find|couldn[''']?t\s+find|"
    r"no\s+file\s+(?:named|called)|"
    r"没有找到|未找到|找不到)",
    re.IGNORECASE,
)

_DENIAL_AFTER = re.compile(
    r"(?:does\s+not\s+exist|doesn[''']?t\s+exist|does\s*n[''']?t\s+exist|"
    r"not\s+found|is\s+not\s+present|was\s+not\s+found|"
    r"不存在)",
    re.IGNORECASE,
)


_CLAUSE_BOUNDARY = re.compile(r"[.!?;]\s+|\bbut\b|\bhowever\b|\bwhile\b|\balthough\b|[，。；]", re.IGNORECASE)


def _is_denied_reference(text: str, match_start: int, path: str) -> bool:
    """Check if a file path is mentioned in a denial context (e.g. 'does not exist').

    Uses clause boundaries (periods, 'but', 'however', etc.) to avoid
    false positives when a denial for one path bleeds into a different clause.
    """
    before_region = text[max(0, match_start - 80):match_start]
    boundary = None
    for m in _CLAUSE_BOUNDARY.finditer(before_region):
        boundary = m.end()
    clause_before = before_region[boundary:] if boundary is not None else before_region
    if _DENIAL_BEFORE.search(clause_before):
        return True

    after_start = match_start + len(path)
    after_region = text[after_start:min(len(text), after_start + 50)]
    boundary_after = _CLAUSE_BOUNDARY.search(after_region)
    clause_after = after_region[:boundary_after.start()] if boundary_after else after_region
    return bool(_DENIAL_AFTER.search(clause_after))


def extract_file_references(text: str) -> list[str]:
    """Extract all file paths mentioned in an LLM answer, excluding denied references."""
    paths: set[str] = set()
    for m in _FILE_PATH_RE.finditer(text):
        path = m.group(1) or m.group(2)
        if path and not path.startswith("http") and "/" in path:
            path = path.strip("`'\",;:()")
            if any(path.endswith(ext) for ext in (
                ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs",
                ".java", ".kt", ".c", ".cpp", ".h", ".hpp",
                ".rb", ".php", ".swift", ".scala", ".sh",
                ".yaml", ".yml", ".toml", ".json", ".md",
                ".css", ".scss", ".html",
            )):
                path_start = m.start(1) if m.group(1) else m.start(2)
                if not _is_denied_reference(text, path_start, path):
                    paths.add(path)
    return sorted(paths)


def verify_answer(
    answer: str,
    file_tree: list[dict],
    file_contents: dict[str, str] | None = None,
) -> dict:
    """Verify file paths and code references in an LLM answer.

    Returns:
        {
            "total_refs": int,
            "valid_refs": int,
            "invalid_refs": list[str],
            "hallucination_rate": float,  # 0.0 = no hallucinations
            "warnings": list[str],
        }
    """
    valid_paths = {n["path"] for n in file_tree if n["type"] == "blob"}

    if not isinstance(answer, str):
        answer = str(answer)
    referenced_paths = extract_file_references(answer)

    if not referenced_paths:
        return {
            "total_refs": 0,
            "valid_refs": 0,
            "invalid_refs": [],
            "hallucination_rate": 0.0,
            "warnings": [],
        }

    valid_refs = 0
    invalid_refs: list[str] = []
    warnings: list[str] = []

    for path in referenced_paths:
        if path in valid_paths:
            valid_refs += 1
        else:
            partial = [p for p in valid_paths if p.endswith("/" + path.split("/")[-1])]
            if partial:
                warnings.append(
                    f"Path '{path}' not found exactly, but similar: {partial[0]}"
                )
                valid_refs += 1
            else:
                invalid_refs.append(path)

    total = len(referenced_paths)
    hallucination_rate = len(invalid_refs) / total if total > 0 else 0.0

    if invalid_refs:
        logger.warning(
            "Hallucinated paths detected (%d/%d): %s",
            len(invalid_refs), total, invalid_refs,
        )

    return {
        "total_refs": total,
        "valid_refs": valid_refs,
        "invalid_refs": invalid_refs,
        "hallucination_rate": round(hallucination_rate, 4),
        "warnings": warnings,
    }


def annotate_answer(answer: str, verification: dict) -> str:
    """Add verification annotations to the answer if issues were found."""
    if not verification["invalid_refs"]:
        return answer

    warning_block = "\n\n---\n⚠️ **校验提示：** 以下引用的文件路径在仓库中未找到，可能不准确：\n"
    for path in verification["invalid_refs"]:
        warning_block += f"- `{path}`\n"

    return answer + warning_block
