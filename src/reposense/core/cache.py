"""Small on-disk JSON cache for expensive results (model calls, GitHub lookups)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable

CACHE_ROOT = Path(os.path.expanduser("~")) / ".cache" / "reposense" / "analysis"


def content_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()[:16]


class JsonCache:
    def __init__(self, repo: str, root: Path = CACHE_ROOT):
        self.dir = root / repo.replace("/", "__")

    def _path(self, namespace: str, key: str) -> Path:
        safe = hashlib.sha1(key.encode()).hexdigest()[:20]
        return self.dir / namespace / f"{safe}.json"

    def get(self, namespace: str, key: str) -> Any | None:
        p = self._path(namespace, key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def set(self, namespace: str, key: str, value: Any) -> None:
        p = self._path(namespace, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)

    def get_or_compute(self, namespace: str, key: str, compute: Callable[[], Any], refresh: bool = False) -> Any:
        if not refresh:
            hit = self.get(namespace, key)
            if hit is not None:
                return hit
        value = compute()
        self.set(namespace, key, value)
        return value
