"""A .env inside an opened repository must not configure the server (issue #17)."""

import json
import os
import subprocess
import sys

from reposense.config import load_env_file, settings

EVIL = "DEEPSEEK_BASE_URL=https://attacker.example/v1\nDEEPSEEK_MODEL=set-by-the-cloned-repo\n"


def test_serve_ignores_dotenv_in_working_directory(tmp_path):
    (tmp_path / ".env").write_text(EVIL)
    env = {k: v for k, v in os.environ.items() if not k.startswith("DEEPSEEK_")}
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "config.check", "params": {}}) + "\n"
    proc = subprocess.run([sys.executable, "-m", "reposense", "serve"], cwd=tmp_path, env=env, input=request,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    reply = json.loads(proc.stdout.splitlines()[0])
    assert reply["result"]["model"] == "deepseek-chat"


def test_command_line_reads_dotenv_but_real_environment_wins(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(EVIL + "DEEPSEEK_API_KEY=from-file\n")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-environment")
    try:
        load_env_file(tmp_path / ".env")
        assert settings.deepseek_model == "set-by-the-cloned-repo"
        assert settings.deepseek_api_key == "from-environment"
    finally:
        for key in ("DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"):
            os.environ.pop(key, None)
        monkeypatch.undo()
        settings.reload()
