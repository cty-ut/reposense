"""Sprint 0: Verify Gemini API works — simplest possible call."""

from __future__ import annotations

import os
from langchain_google_genai import ChatGoogleGenerativeAI


def test_gemini_api():
    api_key = os.getenv("GOOGLE_API_KEY", "")
    if not api_key:
        print("⚠️  GOOGLE_API_KEY not set, skipping live test.")
        print("   Copy .env.example to .env and fill in your key.")
        return

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash-preview-04-17",
        google_api_key=api_key,
        temperature=0,
        max_output_tokens=100,
    )

    response = llm.invoke("Say 'hello reposense' and nothing else.")

    print("✅ Gemini API works.")
    print(f"   Model response: {response.content}")
    print(f"   Usage metadata: {response.usage_metadata}")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    test_gemini_api()
