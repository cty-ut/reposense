"""Live end-to-end test for the Q&A system.

Run manually:  .venv/bin/python tests/test_qa_live.py
"""

from __future__ import annotations

import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from reposense.graph.qa_graph import QAEngine
from reposense.graph.nodes.verifier import verify_answer
from reposense.tools.github_api import GitHubClient
from reposense.config import settings
from dataclasses import asdict

TEST_REPO = "pallets/click"

TEST_QUESTIONS = [
    ("What is the Context class and what does it do?", "semantic"),
    ("Where is make_parser defined?", "keyword"),
    ("How does Click handle shell completion?", "semantic"),
    ("What files are in the src/click directory?", "exploration"),
    ("How does the testing module CliRunner work?", "semantic"),
]


def main():
    if not settings.github_token:
        print("⚠️  GITHUB_TOKEN not set — skipping.")
        return
    if not settings.google_api_key and not settings.poe_api_key:
        print("⚠️  No LLM API key set — skipping.")
        return

    engine = QAEngine()

    # Build index
    print(f"📦 Building index for {TEST_REPO}...")
    t0 = time.time()
    with GitHubClient(token=settings.github_token) as client:
        meta = client.get_repo_meta(TEST_REPO)
        tree_nodes = client.get_file_tree(TEST_REPO)
        file_tree = [{"path": n.path, "type": n.type, "size": n.size} for n in tree_nodes]

    stats = engine.build_index(TEST_REPO, file_tree)
    t_index = time.time() - t0
    print(f"   Index built in {t_index:.1f}s: {stats}")
    print()

    meta_dict = asdict(meta)
    thread_id = "test-session"
    total_iters = 0
    total_refs = 0
    valid_refs = 0

    for i, (question, qtype) in enumerate(TEST_QUESTIONS, 1):
        print(f"{'=' * 60}")
        print(f"Q{i} [{qtype}]: {question}")
        print(f"{'=' * 60}")

        t0 = time.time()
        result = engine.ask(question, thread_id=thread_id, repo_meta=meta_dict)
        elapsed = time.time() - t0

        answer = result["answer"]
        iters = result["iterations"]
        total_iters += iters

        verification = verify_answer(answer, file_tree)
        total_refs += verification["total_refs"]
        valid_refs += verification["valid_refs"]

        # Print truncated answer
        lines = answer.split("\n")
        preview = "\n".join(lines[:15])
        if len(lines) > 15:
            preview += f"\n... ({len(lines) - 15} more lines)"
        print(f"\n{preview}")
        print(f"\n[{elapsed:.1f}s | {iters} tool calls | refs: {verification['valid_refs']}/{verification['total_refs']} valid | hallucination: {verification['hallucination_rate']:.0%}]")
        if verification["invalid_refs"]:
            print(f"  ⚠️ Invalid refs: {verification['invalid_refs']}")
        print()

    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Questions: {len(TEST_QUESTIONS)}")
    print(f"Total tool calls: {total_iters}")
    print(f"Total file refs: {total_refs}")
    print(f"Valid file refs: {valid_refs}")
    hallucination_rate = (total_refs - valid_refs) / total_refs * 100 if total_refs > 0 else 0
    print(f"Overall hallucination rate: {hallucination_rate:.1f}%")

    # Test multi-turn memory
    print()
    print("─── Multi-turn memory test ───")
    result2 = engine.ask(
        "In the previous question about CliRunner, which file was it defined in?",
        thread_id=thread_id,
        repo_meta=meta_dict,
    )
    print(f"Follow-up answer preview: {result2['answer'][:200]}")
    print(f"[{result2['iterations']} tool calls]")

    history = engine.get_conversation_history(thread_id)
    print(f"\nConversation history: {len(history)} messages")


if __name__ == "__main__":
    main()
