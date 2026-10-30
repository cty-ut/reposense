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
    deepseek_model: str = field(default_factory=lambda: os.getenv("DEEPSEEK_MODEL", "deepseek-chat"))
    deepseek_base_url: str = field(default_factory=lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))

    def validate(self) -> list[str]:
        """Return a list of missing-but-required config keys."""
        missing: list[str] = []
        if not self.github_token:
            missing.append("GITHUB_TOKEN")
        if not self.deepseek_api_key:
            missing.append("DEEPSEEK_API_KEY")
        return missing


settings = Config()
