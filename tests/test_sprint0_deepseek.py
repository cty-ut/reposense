"""Sprint 0: Verify DeepSeek API works — simplest possible call."""

from __future__ import annotations

import os
from langchain_openai import ChatOpenAI


def test_deepseek_api():
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("⚠️  DEEPSEEK_API_KEY not set, skipping live test.")
        print("   Copy .env.example to .env and fill in your key.")
        return

    llm = ChatOpenAI(
        model="deepseek-chat",
        api_key=api_key,
        base_url="https://api.deepseek.com",
        temperature=0,
        max_tokens=100,
    )

    response = llm.invoke("Say 'hello reposense' and nothing else.")
    content = response.content.strip().lower()

    print(f"✅ DeepSeek API works.")
    print(f"   Model response: {response.content}")
    print(f"   Token usage:    {response.response_metadata.get('token_usage', 'N/A')}")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    test_deepseek_api()
