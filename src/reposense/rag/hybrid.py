"""Hybrid retrieval: vector (ChromaDB) + BM25, fused with Reciprocal Rank Fusion."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from reposense.rag.indexer import CodeIndexer, _collection_name, _is_indexable
from reposense.rag.bm25 import BM25Searcher
from reposense.rag.retriever import CodeRetriever, RetrievalResult
from reposense.tools.code_parser import CodeChunk, chunk_code

logger = logging.getLogger(__name__)

RRF_K = 60  # standard RRF constant


@dataclass
class HybridResult:
    """A retrieval result from hybrid search with fused score."""
    content: str
    file_path: str
    start_line: int
    end_line: int
    symbol_name: str
    symbol_type: str
    language: str
    rrf_score: float
    source: str  # "vector", "bm25", or "both"

    def format_for_llm(self) -> str:
        header = f"### {self.file_path} (lines {self.start_line}-{self.end_line}) — {self.symbol_name}"
        return f"{header}\n```{self.language}\n{self.content}\n```"


def _chunk_key(file_path: str, start_line: int, end_line: int) -> str:
    return f"{file_path}:{start_line}:{end_line}"


class HybridRetriever:
    """Combines vector semantic search and BM25 keyword search with RRF reranking."""

    def __init__(self, indexer: CodeIndexer) -> None:
        self._vector_retriever = CodeRetriever(indexer)
        self._bm25 = BM25Searcher()
        self._indexer = indexer

    def build_bm25_index(self, file_contents: dict[str, str]) -> int:
        """Build BM25 index from file contents (should be called after vector index is built)."""
        all_chunks: list[CodeChunk] = []
        for path, source in file_contents.items():
            if not _is_indexable(path):
                continue
            all_chunks.extend(chunk_code(source, path))

        self._bm25.build(all_chunks)
        return len(all_chunks)

    @property
    def bm25_ready(self) -> bool:
        return self._bm25.is_built

    def search(
        self,
        repo_name: str,
        query: str,
        *,
        top_k: int = 8,
        vector_weight: float = 1.0,
        bm25_weight: float = 1.0,
    ) -> list[HybridResult]:
        """Hybrid search with RRF fusion.

        Args:
            repo_name: owner/repo
            query: natural language or keyword query
            top_k: max final results
            vector_weight: RRF weight for vector results
            bm25_weight: RRF weight for BM25 results
        """
        vector_k = top_k * 2
        bm25_k = top_k * 2

        vector_results = self._vector_retriever.search(repo_name, query, top_k=vector_k)
        bm25_results = self._bm25.search(query, top_k=bm25_k) if self._bm25.is_built else []

        rrf_scores: dict[str, float] = {}
        result_map: dict[str, dict] = {}
        source_map: dict[str, set[str]] = {}

        for rank, r in enumerate(vector_results):
            key = _chunk_key(r.file_path, r.start_line, r.end_line)
            rrf_scores[key] = rrf_scores.get(key, 0) + vector_weight / (RRF_K + rank + 1)
            source_map.setdefault(key, set()).add("vector")
            if key not in result_map:
                result_map[key] = {
                    "content": r.content,
                    "file_path": r.file_path,
                    "start_line": r.start_line,
                    "end_line": r.end_line,
                    "symbol_name": r.symbol_name,
                    "symbol_type": r.symbol_type,
                    "language": r.language,
                }

        for rank, br in enumerate(bm25_results):
            c = br.chunk
            key = _chunk_key(c.file_path, c.start_line, c.end_line)
            rrf_scores[key] = rrf_scores.get(key, 0) + bm25_weight / (RRF_K + rank + 1)
            source_map.setdefault(key, set()).add("bm25")
            if key not in result_map:
                result_map[key] = {
                    "content": c.content,
                    "file_path": c.file_path,
                    "start_line": c.start_line,
                    "end_line": c.end_line,
                    "symbol_name": c.symbol_name,
                    "symbol_type": c.symbol_type,
                    "language": c.language,
                }

        sorted_keys = sorted(rrf_scores.keys(), key=lambda k: rrf_scores[k], reverse=True)

        results: list[HybridResult] = []
        for key in sorted_keys[:top_k]:
            data = result_map[key]
            sources = source_map[key]
            source = "both" if len(sources) > 1 else sources.pop()
            results.append(HybridResult(
                content=data["content"],
                file_path=data["file_path"],
                start_line=data["start_line"],
                end_line=data["end_line"],
                symbol_name=data["symbol_name"],
                symbol_type=data["symbol_type"],
                language=data["language"],
                rrf_score=round(rrf_scores[key], 6),
                source=source,
            ))

        logger.info(
            "Hybrid search '%s' → %d results (vector=%d, bm25=%d) for %s",
            query[:50], len(results), len(vector_results), len(bm25_results), repo_name,
        )
        return results

    def format_context(self, results: list[HybridResult]) -> str:
        if not results:
            return "(No relevant code found)"
        return "\n\n".join(r.format_for_llm() for r in results)
