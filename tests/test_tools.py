import pytest

from reposense.agent import code_tools, history_tools


def test_glob_and_ls(ws):
    assert code_tools.glob(ws, "**/*.py")["files"] == ["src/pkg/core.py", "tests/test_core.py"]
    assert code_tools.ls(ws, ".")["entries"] == ["src/", "tests/"]
    assert code_tools.ls(ws, "src/pkg")["entries"] == ["core.py"]
    assert code_tools.ls(ws, "nope")["error"] == "not_found"


def test_grep_returns_file_and_line(ws):
    res = code_tools.grep(ws, r"nargs != 1")
    assert res["matches"] == [{"file": "src/pkg/core.py", "line": 5, "text": "if self.nargs != 1:"}]
    assert code_tools.grep(ws, "metavar", file_glob="*.py", path="tests")["total"] == 1


def test_read_line_range(ws):
    res = code_tools.read(ws, "src/pkg/core.py", 5, 6)
    assert (res["start"], res["end"], res["total_lines"]) == (5, 6, 7)
    assert "5  " in res["content"] and 'metavar += "..."' in res["content"]
    assert code_tools.read(ws, "src/pkg/core.py", 99)["error"] == "out_of_range"
    assert code_tools.read(ws, "missing.py")["error"] == "not_found"


def test_paths_cannot_escape_workspace(ws):
    with pytest.raises(ValueError):
        code_tools.read(ws, "../../etc/passwd")


def test_git_log_extracts_pr_numbers(ws):
    commits = history_tools.git_log(ws, "src/pkg/core.py")["commits"]
    assert [c["pr"] for c in commits] == [42, None]
    assert commits[0]["author"] == "bob"


def test_git_blame_groups_lines_by_commit(ws):
    blocks = history_tools.git_blame(ws, "src/pkg/core.py", 1, 7)["blocks"]
    by_pr = {(b["start"], b["end"]): b["pr"] for b in blocks}
    assert by_pr[(5, 6)] == 42
    assert by_pr[(1, 4)] is None
