"""Centralized configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Config:
    github_token: str = field(default_factory=lambda: os.getenv("GITHUB_TOKEN", ""))
    deepseek_api_key: str = field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", ""))
    google_api_key: str = field(default_factory=lambda: os.getenv("GOOGLE_API_KEY", ""))
    poe_api_key: str = field(default_factory=lambda: os.getenv("POE_API_KEY", ""))

    deepseek_model: str = field(default_factory=lambda: os.getenv("DEEPSEEK_MODEL", "deepseek-chat"))
    deepseek_base_url: str = field(default_factory=lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    gemini_model: str = "gemini-2.5-flash"
    poe_model: str = field(default_factory=lambda: os.getenv("POE_MODEL", "Claude-Sonnet-4"))

    max_react_loops: int = 15
    max_tokens_per_analysis: int = 50_000
    max_api_calls: int = 100

    @property
    def llm_keys(self) -> list[str]:
        """Configured LLM providers in priority order."""
        keys = [("DeepSeek", self.deepseek_api_key), ("Gemini", self.google_api_key), ("Poe", self.poe_api_key)]
        return [name for name, key in keys if key]

    @property
    def has_fallback_llm(self) -> bool:
        return len(self.llm_keys) > 1

    def validate(self) -> list[str]:
        """Return a list of missing-but-required config keys."""
        missing: list[str] = []
        if not self.github_token:
            missing.append("GITHUB_TOKEN")
        if not self.llm_keys:
            missing.append("DEEPSEEK_API_KEY, GOOGLE_API_KEY or POE_API_KEY (need at least one)")
        return missing


settings = Config()
