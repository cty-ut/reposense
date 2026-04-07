"""Live comparison test: vector-only vs BM25-only vs hybrid retrieval.

Run manually:  .venv/bin/python tests/test_hybrid_live.py
"""

from __future__ import annotations

import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from reposense.rag.indexer import CodeIndexer, _is_indexable
from reposense.rag.retriever import CodeRetriever
from reposense.rag.bm25 import BM25Searcher
from reposense.rag.hybrid import HybridRetriever
from reposense.tools.code_parser import chunk_code
from reposense.tools.github_api import GitHubClient
from reposense.config import settings

TEST_REPO = "pallets/click"

# Mix of semantic questions AND exact symbol/keyword questions
TEST_QUERIES = [
    # Semantic questions (vector search excels)
    ("Where is the main entry point?", "semantic"),
    ("How does error handling work?", "semantic"),
    ("How does shell completion work?", "semantic"),
    ("What is the Context class?", "semantic"),
    ("How does option parsing work?", "semantic"),
    # Exact symbol/keyword questions (BM25 excels)
    ("make_parser", "keyword"),
    ("CliRunner", "keyword"),
    ("BadParameter", "keyword"),
    ("BaseCommand", "keyword"),
    ("shell_complete", "keyword"),
]


def _hit(results, label: str) -> bool:
    """Check if any result seems relevant (has content)."""
    return len(results) > 0


def main():
    token = settings.github_token
    if not token:
        print("⚠️  GITHUB_TOKEN not set — skipping live test.")
        return

    # Fetch files
    print(f"📦 Fetching files from {TEST_REPO}...")
    with GitHubClient(token=token) as client:
        tree_nodes = client.get_file_tree(TEST_REPO)
        file_tree = [{"path": n.path, "type": n.type, "size": n.size} for n in tree_nodes]
        indexable = [n["path"] for n in file_tree if n["type"] == "blob" and _is_indexable(n["path"])][:100]

        file_contents: dict[str, str] = {}
        for path in indexable:
            content = client.get_file_content(TEST_REPO, path)
            if content is not None:
                file_contents[path] = content

    print(f"   Fetched {len(file_contents)} files")

    # Build vector index
    print("🔢 Building vector index...")
    indexer = CodeIndexer()
    t0 = time.time()
    stats = indexer.build_index(TEST_REPO, file_contents)
    t_vector = time.time() - t0
    print(f"   {stats['total_chunks']} chunks in {t_vector:.1f}s")

    # Build BM25 index
    print("📝 Building BM25 index...")
    hybrid = HybridRetriever(indexer)
    t0 = time.time()
    bm25_count = hybrid.build_bm25_index(file_contents)
    t_bm25 = time.time() - t0
    print(f"   {bm25_count} chunks in {t_bm25:.1f}s")

    # Also build standalone components for comparison
    vector_retriever = CodeRetriever(indexer)
    bm25_searcher = BM25Searcher()
    all_chunks = []
    for path, source in file_contents.items():
        if _is_indexable(path):
            all_chunks.extend(chunk_code(source, path))
    bm25_searcher.build(all_chunks)

    # Run tests
    print()
    print(f"{'Query':<45} {'Type':<10} {'Vector':<10} {'BM25':<10} {'Hybrid':<10}")
    print("=" * 85)

    vector_hits = 0
    bm25_hits = 0
    hybrid_hits = 0
    total = len(TEST_QUERIES)

    for query, qtype in TEST_QUERIES:
        v_results = vector_retriever.search(TEST_REPO, query, top_k=5)
        b_results = bm25_searcher.search(query, top_k=5)
        h_results = hybrid.search(TEST_REPO, query, top_k=5)

        v_hit = _hit(v_results, "vector")
        b_hit = _hit(b_results, "bm25")
        h_hit = _hit(h_results, "hybrid")

        if v_hit:
            vector_hits += 1
        if b_hit:
            bm25_hits += 1
        if h_hit:
            hybrid_hits += 1

        v_sym = "✅" if v_hit else "❌"
        b_sym = "✅" if b_hit else "❌"
        h_sym = "✅" if h_hit else "❌"

        q_display = query[:43]
        print(f"{q_display:<45} {qtype:<10} {v_sym:<10} {b_sym:<10} {h_sym:<10}")

    print("=" * 85)
    print(f"{'Hit rate':<45} {'':10} {vector_hits}/{total:<8} {bm25_hits}/{total:<8} {hybrid_hits}/{total}")
    print()

    # Show detailed results for a keyword query to demonstrate BM25 advantage
    print("─── Detailed comparison: 'make_parser' ───")
    print()

    print("  Vector results:")
    v_results = vector_retriever.search(TEST_REPO, "make_parser", top_k=3)
    if v_results:
        for r in v_results:
            print(f"    → {r.file_path}:{r.start_line} ({r.symbol_name}) [dist={r.distance}]")
    else:
        print("    → (no results)")

    print()
    print("  BM25 results:")
    b_results = bm25_searcher.search("make_parser", top_k=3)
    if b_results:
        for r in b_results:
            print(f"    → {r.chunk.file_path}:{r.chunk.start_line} ({r.chunk.symbol_name}) [score={r.score}]")
    else:
        print("    → (no results)")

    print()
    print("  Hybrid results:")
    h_results = hybrid.search(TEST_REPO, "make_parser", top_k=3)
    if h_results:
        for r in h_results:
            print(f"    → {r.file_path}:{r.start_line} ({r.symbol_name}) [rrf={r.rrf_score}, src={r.source}]")
    else:
        print("    → (no results)")

    # Cleanup
    indexer.delete_index(TEST_REPO)
    print()
    print("✅ Done.")


if __name__ == "__main__":
    main()
