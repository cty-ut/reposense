"""Build a ChromaDB vector index from code chunks."""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from reposense.config import settings
from reposense.tools.code_parser import CodeChunk, chunk_code
from reposense.tools.github_api import GitHubClient

logger = logging.getLogger(__name__)

BATCH_SIZE = 50
COLLECTION_PREFIX = "reposense_"

CODE_EXTENSIONS = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".c", ".cpp", ".h", ".hpp",
    ".rb", ".php", ".swift", ".scala", ".sh", ".bash",
    ".yaml", ".yml", ".toml", ".json", ".md", ".txt",
    ".css", ".scss", ".html",
})


def _collection_name(repo_name: str) -> str:
    safe = repo_name.replace("/", "_").replace("-", "_").lower()
    name = f"{COLLECTION_PREFIX}{safe}"
    if len(name) > 63:
        name = name[:55] + "_" + hashlib.md5(safe.encode()).hexdigest()[:7]
    return name


def _is_indexable(path: str) -> bool:
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return ext in CODE_EXTENSIONS


class CodeIndexer:
    """Builds and manages a ChromaDB index for a repository's code."""

    def __init__(self, persist_directory: str | None = None) -> None:
        if persist_directory:
            self._client = chromadb.PersistentClient(path=persist_directory)
        else:
            self._client = chromadb.Client()
        self._collections: dict[str, Any] = {}

    def _get_or_create_collection(self, repo_name: str):
        name = _collection_name(repo_name)
        if name not in self._collections:
            self._collections[name] = self._client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collections[name]

    def build_index(
        self,
        repo_name: str,
        file_contents: dict[str, str],
    ) -> dict[str, Any]:
        """Chunk all files and add to ChromaDB.

        Args:
            repo_name: owner/repo
            file_contents: {file_path: source_code}

        Returns:
            stats dict with chunk counts, timing, etc.
        """
        t0 = time.time()
        collection = self._get_or_create_collection(repo_name)

        all_chunks: list[CodeChunk] = []
        for path, source in file_contents.items():
            if not _is_indexable(path):
                continue
            chunks = chunk_code(source, path)
            all_chunks.extend(chunks)

        logger.info("Chunked %d files → %d chunks for %s", len(file_contents), len(all_chunks), repo_name)

        if not all_chunks:
            return {"total_chunks": 0, "elapsed_s": time.time() - t0}

        # Upsert in batches
        for i in range(0, len(all_chunks), BATCH_SIZE):
            batch = all_chunks[i : i + BATCH_SIZE]
            ids = [
                hashlib.sha256(f"{c.file_path}:{c.start_line}:{c.end_line}".encode()).hexdigest()[:16]
                for c in batch
            ]
            documents = [c.content for c in batch]
            metadatas = [
                {
                    "file_path": c.file_path,
                    "start_line": c.start_line,
                    "end_line": c.end_line,
                    "symbol_name": c.symbol_name,
                    "symbol_type": c.symbol_type,
                    "language": c.language,
                }
                for c in batch
            ]
            collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

        elapsed = time.time() - t0
        logger.info("Indexed %d chunks in %.1fs for %s", len(all_chunks), elapsed, repo_name)

        return {
            "total_chunks": len(all_chunks),
            "total_files": len(file_contents),
            "elapsed_s": round(elapsed, 2),
        }

    def build_index_from_github(
        self,
        repo_name: str,
        *,
        file_tree: list[dict] | None = None,
        max_files: int = 200,
    ) -> dict[str, Any]:
        """Fetch code from GitHub and build the index end-to-end.

        Uses zipball download for speed; falls back to per-file API on failure.
        """
        with GitHubClient(token=settings.github_token) as client:
            if file_tree is None:
                tree_nodes = client.get_file_tree(repo_name)
                file_tree = [{"path": n.path, "type": n.type, "size": n.size} for n in tree_nodes]

            file_contents = client.download_repo_files(
                repo_name, file_tree, max_files=max_files,
            )

        return self.build_index(repo_name, file_contents)

    def delete_index(self, repo_name: str) -> None:
        name = _collection_name(repo_name)
        try:
            self._client.delete_collection(name)
            self._collections.pop(name, None)
        except Exception:
            pass

    @property
    def chroma_client(self):
        return self._client
