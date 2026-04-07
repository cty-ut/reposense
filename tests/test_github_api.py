"""Unit tests for GitHubClient — uses mocks so no real API calls are needed."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from reposense.tools.github_api import (
    GitHubClient,
    RepoMeta,
    FileNode,
    RepoIssue,
    BINARY_EXTENSIONS,
    MAX_FILE_SIZE,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    with patch("reposense.tools.github_api.Github") as MockGithub:
        mock_gh = MockGithub.return_value
        mock_gh.get_rate_limit.return_value = SimpleNamespace(
            core=SimpleNamespace(remaining=4999, limit=5000, reset=SimpleNamespace(timestamp=lambda: 0))
        )
        c = GitHubClient(token="fake-token")
        c._gh = mock_gh
        yield c, mock_gh


def _make_mock_repo(**overrides):
    """Build a mock repo object with sensible defaults."""
    defaults = dict(
        full_name="owner/repo",
        description="A test repo",
        language="Python",
        stargazers_count=100,
        forks_count=10,
        open_issues_count=5,
        default_branch="main",
        created_at=SimpleNamespace(isoformat=lambda: "2024-01-01T00:00:00"),
        updated_at=SimpleNamespace(isoformat=lambda: "2025-01-01T00:00:00"),
        license=SimpleNamespace(name="MIT"),
    )
    defaults.update(overrides)
    repo = MagicMock(**defaults)
    repo.get_languages.return_value = {"Python": 50000, "JavaScript": 10000}
    repo.get_topics.return_value = ["ai", "agent"]
    repo.get_contributors.return_value = MagicMock(totalCount=12)
    return repo


# ---------------------------------------------------------------------------
# get_repo_meta
# ---------------------------------------------------------------------------

class TestGetRepoMeta:

    def test_returns_repo_meta(self, client):
        c, mock_gh = client
        mock_repo = _make_mock_repo()
        mock_gh.get_repo.return_value = mock_repo

        meta = c.get_repo_meta("owner/repo")

        assert isinstance(meta, RepoMeta)
        assert meta.full_name == "owner/repo"
        assert meta.stars == 100
        assert meta.language == "Python"
        assert meta.languages == {"Python": 50000, "JavaScript": 10000}
        assert meta.contributors_count == 12
        assert meta.license == "MIT"
        assert meta.topics == ["ai", "agent"]

    def test_handles_missing_description(self, client):
        c, mock_gh = client
        mock_repo = _make_mock_repo(description=None)
        mock_gh.get_repo.return_value = mock_repo

        meta = c.get_repo_meta("owner/repo")
        assert meta.description == ""

    def test_handles_no_license(self, client):
        c, mock_gh = client
        mock_repo = _make_mock_repo(license=None)
        mock_gh.get_repo.return_value = mock_repo

        meta = c.get_repo_meta("owner/repo")
        assert meta.license is None


# ---------------------------------------------------------------------------
# get_file_tree
# ---------------------------------------------------------------------------

class TestGetFileTree:

    def test_returns_file_nodes(self, client):
        c, mock_gh = client
        mock_repo = _make_mock_repo()
        mock_gh.get_repo.return_value = mock_repo

        tree_item1 = SimpleNamespace(path="src/main.py", type="blob", size=1024)
        tree_item2 = SimpleNamespace(path="src/", type="tree", size=None)
        mock_tree = SimpleNamespace(tree=[tree_item1, tree_item2], truncated=False)
        mock_repo.get_git_tree.return_value = mock_tree

        result = c.get_file_tree("owner/repo")

        assert len(result) == 2
        assert isinstance(result[0], FileNode)
        assert result[0].path == "src/main.py"
        assert result[0].type == "blob"
        assert result[0].size == 1024
        assert result[1].size == 0  # None → 0

    def test_uses_custom_branch(self, client):
        c, mock_gh = client
        mock_repo = _make_mock_repo()
        mock_gh.get_repo.return_value = mock_repo
        mock_repo.get_git_tree.return_value = SimpleNamespace(tree=[], truncated=False)

        c.get_file_tree("owner/repo", branch="develop")
        mock_repo.get_git_tree.assert_called_with("develop", recursive=True)


# ---------------------------------------------------------------------------
# get_file_content
# ---------------------------------------------------------------------------

class TestGetFileContent:

    def test_returns_decoded_content(self, client):
        import base64
        c, mock_gh = client
        mock_repo = _make_mock_repo()
        mock_gh.get_repo.return_value = mock_repo

        raw = base64.b64encode(b"print('hello')").decode()
        content_file = SimpleNamespace(
            size=100, encoding="base64", content=raw
        )
        mock_repo.get_contents.return_value = content_file

        text = c.get_file_content("owner/repo", "main.py")
        assert text == "print('hello')"

    def test_caches_result(self, client):
        import base64
        c, mock_gh = client
        mock_repo = _make_mock_repo()
        mock_gh.get_repo.return_value = mock_repo

        raw = base64.b64encode(b"cached").decode()
        content_file = SimpleNamespace(size=10, encoding="base64", content=raw)
        mock_repo.get_contents.return_value = content_file

        result1 = c.get_file_content("owner/repo", "file.py")
        result2 = c.get_file_content("owner/repo", "file.py")

        assert result1 == result2 == "cached"
        assert mock_gh.get_repo.call_count == 1  # second call hits cache

    def test_skips_binary_files(self, client):
        c, _ = client
        assert c.get_file_content("owner/repo", "image.png") is None
        assert c.get_file_content("owner/repo", "font.woff2") is None

    def test_skips_oversized_files(self, client):
        c, mock_gh = client
        mock_repo = _make_mock_repo()
        mock_gh.get_repo.return_value = mock_repo

        content_file = SimpleNamespace(size=MAX_FILE_SIZE + 1, encoding="base64", content="x")
        mock_repo.get_contents.return_value = content_file

        assert c.get_file_content("owner/repo", "big.py") is None

    def test_skips_directory(self, client):
        c, mock_gh = client
        mock_repo = _make_mock_repo()
        mock_gh.get_repo.return_value = mock_repo
        mock_repo.get_contents.return_value = [MagicMock(), MagicMock()]

        assert c.get_file_content("owner/repo", "src/") is None


# ---------------------------------------------------------------------------
# get_issues
# ---------------------------------------------------------------------------

class TestGetIssues:

    def _make_mock_issue(self, *, number=1, title="Bug", is_pr=False, labels=None):
        issue = MagicMock()
        issue.number = number
        issue.title = title
        issue.body = "Fix this"
        issue.labels = [SimpleNamespace(name=l) for l in (labels or [])]
        issue.state = "open"
        issue.created_at = SimpleNamespace(isoformat=lambda: "2025-01-01T00:00:00")
        issue.comments = 3
        issue.html_url = f"https://github.com/owner/repo/issues/{number}"
        issue.pull_request = MagicMock() if is_pr else None
        return issue

    def test_returns_issues_excluding_prs(self, client):
        c, mock_gh = client
        mock_repo = _make_mock_repo()
        mock_gh.get_repo.return_value = mock_repo

        mock_repo.get_issues.return_value = [
            self._make_mock_issue(number=1, title="Bug"),
            self._make_mock_issue(number=2, title="PR", is_pr=True),
            self._make_mock_issue(number=3, title="Feature"),
        ]

        issues = c.get_issues("owner/repo")

        assert len(issues) == 2
        assert issues[0].number == 1
        assert issues[1].number == 3

    def test_respects_limit(self, client):
        c, mock_gh = client
        mock_repo = _make_mock_repo()
        mock_gh.get_repo.return_value = mock_repo

        mock_repo.get_issues.return_value = [
            self._make_mock_issue(number=i) for i in range(10)
        ]

        issues = c.get_issues("owner/repo", limit=3)
        assert len(issues) == 3

    def test_issue_data_shape(self, client):
        c, mock_gh = client
        mock_repo = _make_mock_repo()
        mock_gh.get_repo.return_value = mock_repo
        mock_repo.get_issues.return_value = [
            self._make_mock_issue(number=42, title="Add feature", labels=["good first issue"]),
        ]

        issues = c.get_issues("owner/repo")
        issue = issues[0]

        assert isinstance(issue, RepoIssue)
        assert issue.number == 42
        assert issue.title == "Add feature"
        assert "good first issue" in issue.labels


# ---------------------------------------------------------------------------
# Retry / rate-limit
# ---------------------------------------------------------------------------

class TestRetry:

    def test_api_call_counter(self, client):
        c, mock_gh = client
        mock_repo = _make_mock_repo()
        mock_gh.get_repo.return_value = mock_repo
        mock_repo.get_git_tree.return_value = SimpleNamespace(tree=[], truncated=False)

        assert c.api_calls == 0
        c.get_file_tree("owner/repo")
        assert c.api_calls >= 1

    def test_context_manager(self):
        with patch("reposense.tools.github_api.Github"):
            with GitHubClient(token="fake") as c:
                assert c is not None


# ---------------------------------------------------------------------------
# Binary extensions coverage
# ---------------------------------------------------------------------------

class TestBinaryExtensions:

    def test_common_image_formats(self):
        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            assert ext in BINARY_EXTENSIONS

    def test_common_archive_formats(self):
        for ext in (".zip", ".tar", ".gz"):
            assert ext in BINARY_EXTENSIONS
