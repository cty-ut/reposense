"""Model access (DeepSeek, OpenAI-compatible). One place for client construction and JSON-mode calls."""

from __future__ import annotations

import json

from openai import OpenAI

from reposense.config import settings


def make_client() -> OpenAI:
    return OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url, timeout=120, max_retries=2)


class LLM:
    def __init__(self, client: OpenAI | None = None, model: str | None = None):
        self.client = client or make_client()
        self.model = model or settings.deepseek_model

    def json(self, system: str, user: str, *, max_tokens: int = 4000) -> dict:
        """Ask for a single JSON object. The prompt must describe the expected keys."""
        resp = self.client.chat.completions.create(
            model=self.model, temperature=0, max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        text = resp.choices[0].message.content or "{}"
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Model returned invalid JSON: {e}") from e
