"""BM25 keyword search over code chunks."""

from __future__ import annotations

import logging
import os
import pickle
import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from reposense.tools.code_parser import CodeChunk

logger = logging.getLogger(__name__)

_SPLIT_RE = re.compile(r"[a-zA-Z][a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z][a-z]*|\d+|[_.]")


def _tokenize(text: str) -> list[str]:
    """Code-aware tokenizer: splits camelCase, snake_case, and punctuation."""
    raw_tokens = text.split()
    tokens: list[str] = []
    for word in raw_tokens:
        tokens.append(word.lower())
        sub = _SPLIT_RE.findall(word)
        if len(sub) > 1:
            tokens.extend(t.lower() for t in sub if len(t) >= 2)
    return tokens


@dataclass
class BM25Result:
    """A code chunk matched by BM25 keyword search."""
    chunk: CodeChunk
    score: float


class BM25Searcher:
    """BM25 keyword index over a list of CodeChunks."""

    def __init__(self) -> None:
        self._index: BM25Okapi | None = None
        self._chunks: list[CodeChunk] = []
        self._corpus: list[list[str]] = []

    @property
    def is_built(self) -> bool:
        return self._index is not None

    def build(self, chunks: list[CodeChunk]) -> None:
        """Build the BM25 index from a list of code chunks."""
        self._chunks = list(chunks)
        self._corpus = []
        for chunk in self._chunks:
            text = f"{chunk.symbol_name} {chunk.file_path} {chunk.content}"
            self._corpus.append(_tokenize(text))

        if self._corpus:
            self._index = BM25Okapi(self._corpus)
            logger.info("BM25 index built with %d chunks", len(self._chunks))
        else:
            self._index = None

    def save(self, path: str) -> None:
        """Persist the BM25 index to disk."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"chunks": self._chunks, "corpus": self._corpus}, f)
        logger.info("BM25 index saved to %s", path)

    def load(self, path: str) -> bool:
        """Load BM25 index from disk. Returns True if successful."""
        if not os.path.exists(path):
            return False
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)
            self._chunks = data["chunks"]
            self._corpus = data["corpus"]
            if self._corpus:
                self._index = BM25Okapi(self._corpus)
            logger.info("BM25 index loaded from %s (%d chunks)", path, len(self._chunks))
            return True
        except Exception as exc:
            logger.warning("Failed to load BM25 index: %s", exc)
            return False

    def search(self, query: str, top_k: int = 8) -> list[BM25Result]:
        """Search for code chunks matching the query keywords."""
        if not self._index or not self._chunks:
            return []

        tokens = _tokenize(query)
        if not tokens:
            return []

        scores = self._index.get_scores(tokens)

        scored = [(i, s) for i, s in enumerate(scores) if s > 0]
        scored.sort(key=lambda x: x[1], reverse=True)

        results: list[BM25Result] = []
        for idx, score in scored[:top_k]:
            results.append(BM25Result(chunk=self._chunks[idx], score=round(float(score), 4)))

        return results
