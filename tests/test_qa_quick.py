"""Quick Q&A test with minimal API calls.

Run manually:  .venv/bin/python tests/test_qa_quick.py
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


def main():
    if not settings.github_token:
        print("⚠️  GITHUB_TOKEN not set — skipping.")
        return
    if not settings.llm_keys:
        print("⚠️  No LLM API key set — skipping.")
        return

    engine = QAEngine()

    print(f"📦 Building index for {TEST_REPO}...")
    with GitHubClient(token=settings.github_token) as client:
        meta = client.get_repo_meta(TEST_REPO)
        tree_nodes = client.get_file_tree(TEST_REPO)
        file_tree = [{"path": n.path, "type": n.type, "size": n.size} for n in tree_nodes]

    stats = engine.build_index(TEST_REPO, file_tree)
    print(f"   {stats}")
    print()

    meta_dict = asdict(meta)
    thread_id = "quick-test"

    # Test 1: semantic question
    print("Q1: What is the Context class?")
    r1 = engine.ask("What is the Context class and what does it do?", thread_id=thread_id, repo_meta=meta_dict)
    v1 = verify_answer(r1["answer"], file_tree)
    print(f"   Answer: {r1['answer'][:200]}...")
    print(f"   [{r1['iterations']} tool calls, refs: {v1['valid_refs']}/{v1['total_refs']}, hallucination: {v1['hallucination_rate']:.0%}]")
    print()

    # Test 2: follow-up (multi-turn memory)
    print("Q2 (follow-up): Which file is it defined in?")
    r2 = engine.ask("Which file is the Context class defined in? Give the exact path.", thread_id=thread_id, repo_meta=meta_dict)
    v2 = verify_answer(r2["answer"], file_tree)
    print(f"   Answer: {r2['answer'][:200]}...")
    print(f"   [{r2['iterations']} tool calls, refs: {v2['valid_refs']}/{v2['total_refs']}, hallucination: {v2['hallucination_rate']:.0%}]")
    print()

    # Verify multi-turn memory
    history = engine.get_conversation_history(thread_id)
    print(f"Conversation history: {len(history)} messages")
    
    total_refs = v1["total_refs"] + v2["total_refs"]
    total_valid = v1["valid_refs"] + v2["valid_refs"]
    overall_hall = (total_refs - total_valid) / total_refs * 100 if total_refs > 0 else 0
    print(f"\nOverall: {total_valid}/{total_refs} valid refs, {overall_hall:.1f}% hallucination rate")
    print("✅ Done.")


if __name__ == "__main__":
    main()
