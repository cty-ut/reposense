"""Centralized configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Config:
    github_token: str = field(default_factory=lambda: os.getenv("GITHUB_TOKEN", ""))
    google_api_key: str = field(default_factory=lambda: os.getenv("GOOGLE_API_KEY", ""))

    gemini_model: str = "gemini-2.5-flash-preview-04-17"

    max_react_loops: int = 15
    max_tokens_per_analysis: int = 50_000
    max_api_calls: int = 100

    def validate(self) -> list[str]:
        """Return a list of missing-but-required config keys."""
        missing: list[str] = []
        if not self.github_token:
            missing.append("GITHUB_TOKEN")
        if not self.google_api_key:
            missing.append("GOOGLE_API_KEY")
        return missing


settings = Config()
