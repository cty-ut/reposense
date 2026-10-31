"""Non-ASCII file names come back from git as real paths (issue #7)."""

import subprocess

from reposense.analysis import structure
from reposense.analysis.file_history import file_history
from reposense.core.files import tracked_files
from reposense.core.git import Git

NAME = "docs/使用指南.md"


def test_non_ascii_paths_are_usable(ws):
    (ws.root / "docs").mkdir()
    (ws.root / NAME).write_text("# 使用指南\n\n第一步\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(ws.root), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(ws.root), "commit", "-q", "-m", "添加中文文档"], check=True, capture_output=True)

    assert NAME in tracked_files(ws)
    assert (ws.root / NAME).is_file()
    assert Git(ws.root).churn().get(NAME) == 1
    assert {f["path"]: f["lines"] for f in structure.collect_files(ws)}[NAME] == 3
    history = file_history(ws, NAME, gh=None, limit=5)
    assert [c["subject"] for c in history["commits"]] == ["添加中文文档"]
    assert history["commits"][0]["additions"] == 3
