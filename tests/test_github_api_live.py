"""Live integration tests for GitHubClient — requires GITHUB_TOKEN in .env.

Run manually:  .venv/bin/python tests/test_github_api_live.py
"""

from __future__ import annotations

import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from reposense.tools.github_api import GitHubClient

TEST_REPOS = [
    ("pallets/click", "small CLI library"),
    ("encode/httpx", "medium HTTP client"),
    ("langchain-ai/langgraph", "large AI framework"),
]


def main():
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        print("⚠️  GITHUB_TOKEN not set — skipping live test.")
        return

    with GitHubClient(token=token) as client:
        print(f"Rate limit remaining: {client.rate_limit_remaining}\n")

        for repo_name, desc in TEST_REPOS:
            print(f"{'=' * 60}")
            print(f"Testing: {repo_name} ({desc})")
            print(f"{'=' * 60}")

            t0 = time.time()
            meta = client.get_repo_meta(repo_name)
            t_meta = time.time() - t0
            print(f"\n📋 Metadata ({t_meta:.1f}s):")
            print(f"   Name:         {meta.full_name}")
            print(f"   Description:  {meta.description[:80]}")
            print(f"   Language:     {meta.language}")
            print(f"   Languages:    {meta.languages}")
            print(f"   Stars:        {meta.stars}")
            print(f"   Forks:        {meta.forks}")
            print(f"   Contributors: {meta.contributors_count}")
            print(f"   License:      {meta.license}")
            print(f"   Topics:       {meta.topics}")

            t0 = time.time()
            tree = client.get_file_tree(repo_name)
            t_tree = time.time() - t0
            files = [n for n in tree if n.type == "blob"]
            dirs = [n for n in tree if n.type == "tree"]
            print(f"\n📂 File tree ({t_tree:.1f}s):")
            print(f"   Total nodes: {len(tree)}  ({len(files)} files, {len(dirs)} dirs)")
            print(f"   First 10 files:")
            for f in files[:10]:
                print(f"     {f.path} ({f.size} bytes)")

            if files:
                target = next((f.path for f in files if f.path.endswith(".py")), files[0].path)
                t0 = time.time()
                content = client.get_file_content(repo_name, target)
                t_content = time.time() - t0
                if content:
                    lines = content.count("\n") + 1
                    print(f"\n📄 File content: {target} ({t_content:.1f}s)")
                    print(f"   Lines: {lines}")
                    print(f"   Preview: {content[:120]}...")

            t0 = time.time()
            issues = client.get_issues(repo_name, limit=5)
            t_issues = time.time() - t0
            print(f"\n🐛 Issues ({t_issues:.1f}s): {len(issues)} fetched")
            for iss in issues[:3]:
                labels = ", ".join(iss.labels) if iss.labels else "none"
                print(f"   #{iss.number}: {iss.title[:60]} [{labels}]")

            print()

        print(f"Total API calls: {client.api_calls}")
        print(f"Rate limit remaining: {client.rate_limit_remaining}")


if __name__ == "__main__":
    main()
