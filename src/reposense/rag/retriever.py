"""Semantic code retrieval from ChromaDB."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from reposense.rag.indexer import CodeIndexer, _collection_name

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 8
SIMILARITY_THRESHOLD = 1.2  # cosine distance; lower = more similar (0=identical, 2=opposite)


@dataclass
class RetrievalResult:
    """A single code chunk retrieved by semantic search."""
    content: str
    file_path: str
    start_line: int
    end_line: int
    symbol_name: str
    symbol_type: str
    language: str
    distance: float

    def format_for_llm(self) -> str:
        """Format as a string suitable for LLM context."""
        header = f"### {self.file_path} (lines {self.start_line}-{self.end_line}) — {self.symbol_name}"
        return f"{header}\n```{self.language}\n{self.content}\n```"


class CodeRetriever:
    """Retrieve semantically relevant code chunks from a ChromaDB index."""

    def __init__(self, indexer: CodeIndexer) -> None:
        self._indexer = indexer

    def search(
        self,
        repo_name: str,
        query: str,
        *,
        top_k: int = DEFAULT_TOP_K,
        filter_symbol_type: str | None = None,
    ) -> list[RetrievalResult]:
        """Search for code chunks semantically similar to the query.

        Args:
            repo_name: owner/repo
            query: natural language question
            top_k: max results
            filter_symbol_type: optional filter (e.g. "function", "class")

        Returns:
            List of RetrievalResult sorted by relevance (most similar first)
        """
        name = _collection_name(repo_name)
        try:
            collection = self._indexer.chroma_client.get_collection(name)
        except Exception:
            logger.warning("No index found for %s", repo_name)
            return []

        where = None
        if filter_symbol_type:
            where = {"symbol_type": filter_symbol_type}

        try:
            results = collection.query(
                query_texts=[query],
                n_results=top_k,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            logger.error("ChromaDB query failed: %s", exc)
            return []

        if not results["documents"] or not results["documents"][0]:
            return []

        retrieval_results: list[RetrievalResult] = []
        docs = results["documents"][0]
        metas = results["metadatas"][0]
        dists = results["distances"][0]

        for doc, meta, dist in zip(docs, metas, dists):
            if dist > SIMILARITY_THRESHOLD:
                continue
            retrieval_results.append(RetrievalResult(
                content=doc,
                file_path=meta["file_path"],
                start_line=meta["start_line"],
                end_line=meta["end_line"],
                symbol_name=meta["symbol_name"],
                symbol_type=meta["symbol_type"],
                language=meta["language"],
                distance=round(dist, 4),
            ))

        logger.info(
            "Query '%s' → %d results (of %d candidates) for %s",
            query[:50], len(retrieval_results), len(docs), repo_name,
        )
        return retrieval_results

    def format_context(self, results: list[RetrievalResult]) -> str:
        """Format retrieval results as a single context string for the LLM."""
        if not results:
            return "(No relevant code found)"
        sections = [r.format_for_llm() for r in results]
        return "\n\n".join(sections)
