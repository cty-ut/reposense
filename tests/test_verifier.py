"""Tests for the hallucination verification layer."""

from __future__ import annotations

from reposense.graph.nodes.verifier import (
    extract_file_references,
    verify_answer,
    annotate_answer,
)

SAMPLE_TREE = [
    {"path": "src/click/core.py", "type": "blob", "size": 89000},
    {"path": "src/click/types.py", "type": "blob", "size": 35000},
    {"path": "src/click/utils.py", "type": "blob", "size": 12000},
    {"path": "src/click/__init__.py", "type": "blob", "size": 3000},
    {"path": "tests/test_basic.py", "type": "blob", "size": 5000},
    {"path": "README.md", "type": "blob", "size": 8000},
    {"path": "src", "type": "tree", "size": 0},
    {"path": "src/click", "type": "tree", "size": 0},
]


class TestExtractFileReferences:

    def test_backtick_path(self):
        text = "The main logic is in `src/click/core.py` which handles commands."
        refs = extract_file_references(text)
        assert "src/click/core.py" in refs

    def test_multiple_paths(self):
        text = "See `src/click/core.py` and `src/click/types.py` for details."
        refs = extract_file_references(text)
        assert "src/click/core.py" in refs
        assert "src/click/types.py" in refs

    def test_ignores_urls(self):
        text = "Check https://github.com/pallets/click for more info."
        refs = extract_file_references(text)
        assert len(refs) == 0

    def test_ignores_non_code_extensions(self):
        text = "The image is in assets/logo.png"
        refs = extract_file_references(text)
        assert len(refs) == 0


class TestVerifyAnswer:

    def test_all_valid(self):
        answer = "The code is in `src/click/core.py` and `src/click/types.py`."
        result = verify_answer(answer, SAMPLE_TREE)
        assert result["total_refs"] == 2
        assert result["valid_refs"] == 2
        assert result["invalid_refs"] == []
        assert result["hallucination_rate"] == 0.0

    def test_hallucinated_path(self):
        answer = "Look at `src/click/commands.py` for the implementation."
        result = verify_answer(answer, SAMPLE_TREE)
        assert result["total_refs"] == 1
        assert result["valid_refs"] == 0
        assert "src/click/commands.py" in result["invalid_refs"]
        assert result["hallucination_rate"] == 1.0

    def test_mixed_valid_and_invalid(self):
        answer = "See `src/click/core.py` and `src/click/fake.py`."
        result = verify_answer(answer, SAMPLE_TREE)
        assert result["total_refs"] == 2
        assert result["valid_refs"] == 1
        assert result["hallucination_rate"] == 0.5

    def test_no_references(self):
        answer = "This project is a CLI framework written in Python."
        result = verify_answer(answer, SAMPLE_TREE)
        assert result["total_refs"] == 0
        assert result["hallucination_rate"] == 0.0


class TestAnnotateAnswer:

    def test_clean_answer_unchanged(self):
        answer = "The code is in `src/click/core.py`."
        verification = {"invalid_refs": [], "total_refs": 1, "valid_refs": 1, "hallucination_rate": 0.0, "warnings": []}
        result = annotate_answer(answer, verification)
        assert result == answer

    def test_adds_warning_for_invalid_refs(self):
        answer = "See `src/click/fake.py` for details."
        verification = {
            "invalid_refs": ["src/click/fake.py"],
            "total_refs": 1,
            "valid_refs": 0,
            "hallucination_rate": 1.0,
            "warnings": [],
        }
        result = annotate_answer(answer, verification)
        assert "校验提示" in result
        assert "src/click/fake.py" in result
