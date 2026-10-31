"""Thin wrapper over the local `git` CLI. Pure data access: no GitHub, no model calls."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_PR_IN_SUBJECT = re.compile(r"\(#(\d+)\)\s*$|Merge pull request #(\d+)")
_SEP = "\x1f"
_REC = "\x1e"


def pr_from_subject(subject: str) -> int | None:
    """PR number from a squash-merge '... (#123)' or merge 'Merge pull request #123' subject."""
    m = _PR_IN_SUBJECT.search(subject)
    return int(m.group(1) or m.group(2)) if m else None


def run(root: Path, *args: str, check: bool = True) -> str:
    # stdin=DEVNULL everywhere we spawn: under `reposense serve` stdin is the JSON-RPC pipe, and on
    # Windows a child that inherits a pipe with a pending read blocks at startup until the next request.
    # core.quotepath=off: print non-ASCII paths as UTF-8 instead of "\346\226\207..." in quotes.
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=off", "-C", str(root), *args],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


@dataclass
class Commit:
    sha: str
    author: str
    email: str
    date: str  # YYYY-MM-DD
    subject: str
    additions: int = 0
    deletions: int = 0

    @property
    def pr(self) -> int | None:
        return pr_from_subject(self.subject)


@dataclass
class BlameLine:
    line: int
    sha: str
    author: str
    date: str
    summary: str


class Git:
    def __init__(self, root: Path):
        self.root = root

    def head(self) -> str:
        return run(self.root, "rev-parse", "HEAD").strip()

    def ls_files(self) -> list[str]:
        return [f for f in run(self.root, "ls-files").splitlines() if f]

    def file_log(self, path: str, limit: int = 10) -> list[Commit]:
        """Latest commits touching `path` (following renames), newest first, with line stats."""
        out = run(self.root, "log", f"-n{limit}", "--follow", "--date=short", "--numstat",
                  f"--format={_REC}%H{_SEP}%an{_SEP}%ae{_SEP}%ad{_SEP}%s", "--", path, check=False)
        commits: list[Commit] = []
        for rec in out.split(_REC)[1:]:
            header, *rest = rec.strip("\n").split("\n")
            sha, author, email, date, subject = header.split(_SEP, 4)
            c = Commit(sha, author, email, date, subject)
            for ln in rest:
                parts = ln.split("\t")
                if len(parts) == 3:
                    c.additions += int(parts[0]) if parts[0].isdigit() else 0
                    c.deletions += int(parts[1]) if parts[1].isdigit() else 0
            commits.append(c)
        return commits

    def log(self, path: str = "", limit: int = 10) -> list[Commit]:
        """Latest commits touching a file or directory (no rename following, no stats)."""
        args = ["log", f"-n{limit}", "--date=short", f"--format=%H{_SEP}%an{_SEP}%ae{_SEP}%ad{_SEP}%s"]
        out = run(self.root, *args, *(["--", path] if path else []), check=False)
        return [Commit(*ln.split(_SEP, 4)) for ln in out.splitlines() if ln]

    def blame(self, path: str, start: int | None = None, end: int | None = None) -> list[BlameLine]:
        """Last commit that touched each line (optionally only lines start..end)."""
        args = ["blame", "--line-porcelain"]
        if start and end:
            args.append(f"-L{start},{end}")
        out = run(self.root, *args, "--", path, check=False)
        lines: list[BlameLine] = []
        cur: dict = {}
        for ln in out.splitlines():
            head = ln.split(" ")
            if len(head) >= 3 and len(head[0]) == 40 and head[1].isdigit():
                cur = {"sha": head[0], "line": int(head[2])}
            elif ln.startswith("author "):
                cur["author"] = ln[7:]
            elif ln.startswith("author-time "):
                cur["date"] = datetime.fromtimestamp(int(ln[12:])).strftime("%Y-%m-%d")
            elif ln.startswith("summary "):
                cur["summary"] = ln[8:]
            elif ln.startswith("\t"):
                lines.append(BlameLine(cur["line"], cur["sha"], cur.get("author", ""), cur.get("date", ""),
                                       cur.get("summary", "")))
        return lines

    def file_diff(self, sha: str, path: str, context: int = 3) -> str:
        """Unified diff of `path` introduced by commit `sha`."""
        return run(self.root, "show", f"-U{context}", "--format=", "--follow", sha, "--", path, check=False)

    def commit_body(self, sha: str) -> str:
        return run(self.root, "show", "-s", "--format=%b", sha, check=False).strip()

    def churn(self, since: str = "12 months ago") -> dict[str, int]:
        """How many commits touched each file since `since`."""
        out = run(self.root, "log", f"--since={since}", "--name-only", "--format=", check=False)
        counts: dict[str, int] = {}
        for f in out.splitlines():
            if f:
                counts[f] = counts.get(f, 0) + 1
        return counts


def group_blocks(lines: list[BlameLine]) -> list[dict]:
    """Merge consecutive blame lines from the same commit into {start, end, sha, ...} blocks."""
    blocks: list[dict] = []
    for b in lines:
        if blocks and blocks[-1]["sha"] == b.sha and blocks[-1]["end"] == b.line - 1:
            blocks[-1]["end"] = b.line
        else:
            blocks.append({"start": b.line, "end": b.line, "sha": b.sha, "author": b.author, "date": b.date,
                           "summary": b.summary, "pr": pr_from_subject(b.summary)})
    return blocks
