"""Tests for BM25 keyword search."""

from __future__ import annotations

from reposense.rag.bm25 import BM25Searcher, _tokenize
from reposense.tools.code_parser import CodeChunk


def _make_chunk(content: str, name: str, path: str = "test.py") -> CodeChunk:
    return CodeChunk(
        content=content,
        file_path=path,
        start_line=1,
        end_line=5,
        symbol_name=name,
        symbol_type="function",
        language="py",
    )


class TestTokenizer:

    def test_splits_snake_case(self):
        tokens = _tokenize("get_file_content")
        assert "get" in tokens
        assert "file" in tokens
        assert "content" in tokens

    def test_splits_camel_case(self):
        tokens = _tokenize("getFileContent")
        assert "get" in tokens
        assert "file" in tokens
        assert "content" in tokens

    def test_preserves_full_word(self):
        tokens = _tokenize("get_file_content")
        assert "get_file_content" in tokens


class TestBM25Searcher:

    def _build_searcher(self) -> BM25Searcher:
        chunks = [
            _make_chunk("def make_parser(self):\n    return OptionParser()", "make_parser", "src/core.py"),
            _make_chunk("class Context:\n    def __init__(self):\n        self.parent = None", "Context", "src/core.py"),
            _make_chunk("def invoke(self, ctx):\n    return self.callback(ctx)", "invoke", "src/core.py"),
            _make_chunk("def handle_error(self, exc):\n    raise SystemExit(exc)", "handle_error", "src/utils.py"),
            _make_chunk("import os\nimport sys\nMAX_SIZE = 1024", "module-level", "src/config.py"),
            _make_chunk("class OptionParser:\n    def parse_args(self):\n        pass", "OptionParser", "src/parser.py"),
        ]
        searcher = BM25Searcher()
        searcher.build(chunks)
        return searcher

    def test_exact_function_name(self):
        searcher = self._build_searcher()
        results = searcher.search("make_parser")
        assert len(results) >= 1
        assert results[0].chunk.symbol_name == "make_parser"

    def test_exact_class_name(self):
        searcher = self._build_searcher()
        results = searcher.search("OptionParser")
        assert len(results) >= 1
        assert results[0].chunk.symbol_name == "OptionParser"

    def test_keyword_in_content(self):
        searcher = self._build_searcher()
        results = searcher.search("SystemExit")
        assert len(results) >= 1
        assert results[0].chunk.symbol_name == "handle_error"

    def test_partial_name(self):
        searcher = self._build_searcher()
        results = searcher.search("parser")
        assert len(results) >= 1
        names = [r.chunk.symbol_name for r in results]
        assert "make_parser" in names or "OptionParser" in names

    def test_no_results_for_garbage(self):
        searcher = self._build_searcher()
        results = searcher.search("xyzzy_nonexistent_symbol_12345")
        assert len(results) == 0

    def test_scores_are_sorted(self):
        searcher = self._build_searcher()
        results = searcher.search("parser")
        for i in range(len(results) - 1):
            assert results[i].score >= results[i + 1].score

    def test_top_k_limit(self):
        searcher = self._build_searcher()
        results = searcher.search("self", top_k=2)
        assert len(results) <= 2

    def test_empty_index(self):
        searcher = BM25Searcher()
        results = searcher.search("anything")
        assert results == []
