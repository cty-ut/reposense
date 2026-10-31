"""Smoke-test a bundled backend the way the extension uses it: JSON-RPC over stdin/stdout.

    python packaging/smoke_backend.py vscode-extension/bin/reposense-backend.exe [repo path]

Opens the given git repository (default: this one), then asks for the import graph and the history of
one file. No API keys or network are needed. Exits non-zero on any error or after 120 seconds.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path


def main() -> None:
    exe = Path(sys.argv[1]).resolve()
    repo = Path(sys.argv[2] if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent)
    proc = subprocess.Popen([str(exe)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                            encoding="utf-8", cwd=repo)
    timer = threading.Timer(120, proc.kill)
    timer.daemon = True
    timer.start()
    calls = [("config.check", {}), ("repo.open", {"path": str(repo)}), ("repo.dependencies", {}),
             ("file.history", {"path": "README.md", "limit": 3})]
    for i, (method, params) in enumerate(calls, 1):
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n")
        proc.stdin.flush()
        while True:
            msg = json.loads(proc.stdout.readline())
            if msg.get("id") == i:
                break  # skip progress notifications
        if "error" in msg:
            proc.kill()
            sys.exit(f"{method} failed: {msg['error']}")
        result = msg["result"]
        summary = {k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in result.items()}
        print(f"ok  {method}: {summary}")
    proc.stdin.close()
    proc.wait(timeout=10)
    print("smoke test passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
