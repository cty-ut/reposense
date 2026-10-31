"""Centralized configuration loaded from environment variables.

A `.env` file is read only when the command line asks for it (`load_env_file`). The JSON-RPC server
never reads one: it runs inside repositories the user has just cloned, and a `.env` there must not be
able to change where requests (and the API key) are sent.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


class Config:
    def __init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        """Re-read the environment (after `load_env_file`)."""
        self.github_token = os.getenv("GITHUB_TOKEN", "")
        self.deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.deepseek_model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self.deepseek_base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    def validate(self) -> list[str]:
        """Return a list of missing-but-required config keys."""
        missing: list[str] = []
        if not self.github_token:
            missing.append("GITHUB_TOKEN")
        if not self.deepseek_api_key:
            missing.append("DEEPSEEK_API_KEY")
        return missing


settings = Config()


def load_env_file(path: Path | None = None) -> None:
    """Command-line convenience: read `.env` from the current directory. Real environment variables win."""
    load_dotenv(path or Path.cwd() / ".env", override=False)
    settings.reload()
