"""Sprint 0: Verify GitHub API works — fetch basic repo info."""

from __future__ import annotations

import os
from github import Github, Auth


def test_github_api():
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        print("⚠️  GITHUB_TOKEN not set, skipping live test.")
        print("   Copy .env.example to .env and fill in your token.")
        return

    g = Github(auth=Auth.Token(token))

    repo = g.get_repo("langchain-ai/langgraph")

    print(f"✅ GitHub API works.")
    print(f"   Repo:  {repo.full_name}")
    print(f"   Stars: {repo.stargazers_count}")
    print(f"   Lang:  {repo.language}")
    print(f"   Desc:  {repo.description[:80] if repo.description else 'N/A'}")

    rate_limit = g.get_rate_limit()
    rate = getattr(rate_limit, "core", None) or rate_limit.rate
    print(f"   API rate limit: {rate.remaining}/{rate.limit}")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    test_github_api()
