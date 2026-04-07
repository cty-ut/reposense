"""Live integration test for RAG: index a real repo + semantic search.

Run manually:  .venv/bin/python tests/test_rag_live.py
"""

from __future__ import annotations

import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from reposense.rag.indexer import CodeIndexer
from reposense.rag.retriever import CodeRetriever

TEST_REPO = "pallets/click"

TEST_QUERIES = [
    "Where is the main entry point?",
    "How are CLI commands defined?",
    "How does error handling work?",
    "Where are parameter types defined?",
    "How does the testing utility work?",
    "What is the Context class?",
    "How does option parsing work?",
    "Where are decorators like @click.command defined?",
    "How does shell completion work?",
    "What exceptions does Click define?",
]


def main():
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        print("⚠️  GITHUB_TOKEN not set — skipping live test.")
        return

    indexer = CodeIndexer()
    retriever = CodeRetriever(indexer)

    # Build index
    print(f"📦 Building index for {TEST_REPO}...")
    t0 = time.time()
    stats = indexer.build_index_from_github(TEST_REPO, max_files=100)
    t_index = time.time() - t0
    print(f"   Indexed {stats['total_chunks']} chunks from {stats['total_files']} files in {t_index:.1f}s")
    print()

    # Run test queries
    hits = 0
    total = len(TEST_QUERIES)
    for i, query in enumerate(TEST_QUERIES, 1):
        results = retriever.search(TEST_REPO, query, top_k=5)
        if results:
            hits += 1
            print(f"✅ Q{i}: {query}")
            for r in results[:3]:
                print(f"   → {r.file_path}:{r.start_line}-{r.end_line} ({r.symbol_name}) [dist={r.distance}]")
        else:
            print(f"❌ Q{i}: {query}")
            print(f"   → No results")
        print()

    hit_rate = hits / total * 100
    print(f"{'=' * 50}")
    print(f"Hit rate: {hits}/{total} = {hit_rate:.0f}%")
    print(f"Index time: {t_index:.1f}s")
    print(f"Total chunks: {stats['total_chunks']}")

    # Cleanup
    indexer.delete_index(TEST_REPO)


if __name__ == "__main__":
    main()
