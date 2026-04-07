"""GitHub API client with rate-limiting, retries, and caching."""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from github import Github, Auth, GithubException, RateLimitExceededException

logger = logging.getLogger(__name__)

BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a",
    ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac",
    ".pyc", ".pyo", ".class", ".jar",
    ".lock",
})

MAX_FILE_SIZE = 1024 * 1024  # 1 MB


@dataclass
class RepoMeta:
    """Essential metadata about a GitHub repository."""
    full_name: str
    description: str
    language: str
    languages: dict[str, int]
    stars: int
    forks: int
    open_issues: int
    default_branch: str
    created_at: str
    updated_at: str
    topics: list[str]
    license: str | None
    contributors_count: int


@dataclass
class FileNode:
    """A node in the repository file tree."""
    path: str
    type: str  # "blob" (file) or "tree" (directory)
    size: int


@dataclass
class RepoIssue:
    """A simplified GitHub issue."""
    number: int
    title: str
    body: str
    labels: list[str]
    state: str
    created_at: str
    comments_count: int
    url: str


class GitHubClient:
    """High-level GitHub API wrapper with retry, rate-limit handling, and caching."""

    def __init__(self, token: str, *, max_retries: int = 3, timeout: int = 15) -> None:
        self._gh = Github(auth=Auth.Token(token), timeout=timeout)
        self._max_retries = max_retries
        self._file_cache: dict[str, str] = {}
        self._api_calls = 0

    @property
    def api_calls(self) -> int:
        return self._api_calls

    @property
    def rate_limit_remaining(self) -> int:
        return self._gh.get_rate_limit().resources.core.remaining

    def _retry(self, fn, *args, **kwargs) -> Any:
        """Execute *fn* with exponential-backoff retry on rate-limit or transient errors."""
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                self._api_calls += 1
                return fn(*args, **kwargs)
            except RateLimitExceededException as exc:
                last_exc = exc
                reset_time = self._gh.get_rate_limit().resources.core.reset.timestamp()
                wait = max(reset_time - time.time(), 1) + 1
                logger.warning("Rate limited. Waiting %.0fs (attempt %d/%d)", wait, attempt + 1, self._max_retries)
                time.sleep(wait)
            except GithubException as exc:
                last_exc = exc
                if exc.status in (500, 502, 503):
                    wait = 2 ** attempt
                    logger.warning("GitHub %d error. Retrying in %ds (attempt %d/%d)", exc.status, wait, attempt + 1, self._max_retries)
                    time.sleep(wait)
                else:
                    raise
        raise last_exc  # type: ignore[misc]

    def get_repo_meta(self, repo_name: str) -> RepoMeta:
        """Fetch essential repository metadata.

        Args:
            repo_name: owner/repo format, e.g. "langchain-ai/langgraph"
        """
        repo = self._retry(self._gh.get_repo, repo_name)

        languages = self._retry(repo.get_languages)

        try:
            contributors_count = self._retry(repo.get_contributors).totalCount
        except GithubException:
            contributors_count = -1

        return RepoMeta(
            full_name=repo.full_name,
            description=repo.description or "",
            language=repo.language or "Unknown",
            languages=languages,
            stars=repo.stargazers_count,
            forks=repo.forks_count,
            open_issues=repo.open_issues_count,
            default_branch=repo.default_branch,
            created_at=repo.created_at.isoformat(),
            updated_at=repo.updated_at.isoformat(),
            topics=repo.get_topics(),
            license=repo.license.name if repo.license else None,
            contributors_count=contributors_count,
        )

    def get_file_tree(self, repo_name: str, *, branch: str | None = None) -> list[FileNode]:
        """Fetch the full file tree using the Git Tree API (recursive, single call).

        Falls back to recursive directory listing if the tree is truncated.
        """
        repo = self._retry(self._gh.get_repo, repo_name)
        sha = branch or repo.default_branch

        tree = self._retry(repo.get_git_tree, sha, recursive=True)

        if tree.truncated:
            logger.warning("Tree truncated for %s — falling back to recursive listing", repo_name)
            return self._get_file_tree_recursive(repo)

        return [
            FileNode(path=item.path, type=item.type, size=item.size or 0)
            for item in tree.tree
        ]

    def _get_file_tree_recursive(self, repo, path: str = "") -> list[FileNode]:
        """Fallback: walk directory tree via Contents API for truncated repos."""
        nodes: list[FileNode] = []
        contents = self._retry(repo.get_contents, path)
        if not isinstance(contents, list):
            contents = [contents]
        for item in contents:
            nodes.append(FileNode(path=item.path, type="tree" if item.type == "dir" else "blob", size=item.size))
            if item.type == "dir":
                nodes.extend(self._get_file_tree_recursive(repo, item.path))
        return nodes

    def get_file_content(self, repo_name: str, file_path: str) -> str | None:
        """Fetch a single file's text content. Returns None for binary / oversized files.

        Results are cached in memory to avoid duplicate API calls.
        """
        cache_key = f"{repo_name}:{file_path}"
        if cache_key in self._file_cache:
            return self._file_cache[cache_key]

        ext = "." + file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        if ext in BINARY_EXTENSIONS:
            logger.debug("Skipping binary file: %s", file_path)
            return None

        repo = self._retry(self._gh.get_repo, repo_name)
        content_file = self._retry(repo.get_contents, file_path)

        if isinstance(content_file, list):
            logger.debug("Path is a directory, not a file: %s", file_path)
            return None

        if content_file.size > MAX_FILE_SIZE:
            logger.debug("Skipping oversized file (%d bytes): %s", content_file.size, file_path)
            return None

        if content_file.encoding == "base64" and content_file.content:
            try:
                text = base64.b64decode(content_file.content).decode("utf-8")
            except (UnicodeDecodeError, ValueError):
                logger.debug("Cannot decode file (likely binary): %s", file_path)
                return None
        elif content_file.content:
            text = content_file.content
        else:
            return None

        self._file_cache[cache_key] = text
        return text

    def get_issues(
        self,
        repo_name: str,
        *,
        state: str = "open",
        labels: list[str] | None = None,
        limit: int = 30,
    ) -> list[RepoIssue]:
        """Fetch issues, optionally filtered by labels.

        Args:
            labels: e.g. ["good first issue", "help wanted"]
            limit: max number of issues to return
        """
        repo = self._retry(self._gh.get_repo, repo_name)

        kwargs: dict[str, Any] = {"state": state, "sort": "updated", "direction": "desc"}
        if labels:
            kwargs["labels"] = [repo.get_label(l) for l in labels]

        paginated = self._retry(repo.get_issues, **kwargs)

        issues: list[RepoIssue] = []
        for issue in paginated:
            if issue.pull_request is not None:
                continue
            issues.append(RepoIssue(
                number=issue.number,
                title=issue.title,
                body=issue.body or "",
                labels=[l.name for l in issue.labels],
                state=issue.state,
                created_at=issue.created_at.isoformat(),
                comments_count=issue.comments,
                url=issue.html_url,
            ))
            if len(issues) >= limit:
                break
        return issues

    def download_repo_files(
        self,
        repo_name: str,
        file_tree: list[FileNode] | list[dict] | None = None,
        *,
        max_files: int = 200,
    ) -> dict[str, str]:
        """Download source files via the GitHub zipball API (much faster than per-file).

        Falls back to per-file download if zipball fails.
        Returns a dict of {file_path: content}.
        """
        import io
        import zipfile
        import requests

        from reposense.rag.indexer import _is_indexable

        if file_tree:
            indexable: set[str] = set()
            for node in file_tree:
                path = node.path if isinstance(node, FileNode) else node["path"]
                ntype = node.type if isinstance(node, FileNode) else node["type"]
                if ntype == "blob" and _is_indexable(path):
                    indexable.add(path)
        else:
            indexable = None

        repo = self._retry(self._gh.get_repo, repo_name)
        archive_url = repo.get_archive_link("zipball")

        contents: dict[str, str] = {}
        try:
            logger.info("Downloading zipball for %s ...", repo_name)
            resp = requests.get(archive_url, timeout=60, stream=True)
            resp.raise_for_status()

            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                prefix = ""
                for name in zf.namelist():
                    if "/" in name and not prefix:
                        prefix = name.split("/")[0] + "/"

                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    rel_path = info.filename
                    if prefix and rel_path.startswith(prefix):
                        rel_path = rel_path[len(prefix):]
                    if not rel_path:
                        continue

                    if indexable is not None and rel_path not in indexable:
                        continue
                    if not _is_indexable(rel_path):
                        continue
                    if info.file_size > MAX_FILE_SIZE:
                        continue
                    if len(contents) >= max_files:
                        break

                    try:
                        text = zf.read(info.filename).decode("utf-8")
                        contents[rel_path] = text
                    except (UnicodeDecodeError, KeyError):
                        continue

            logger.info("Zipball extracted %d files for %s", len(contents), repo_name)
        except Exception as exc:
            logger.warning("Zipball download failed for %s: %s. Falling back to per-file.", repo_name, exc)
            if file_tree:
                paths = []
                for node in file_tree:
                    path = node.path if isinstance(node, FileNode) else node["path"]
                    ntype = node.type if isinstance(node, FileNode) else node["type"]
                    if ntype == "blob" and _is_indexable(path):
                        paths.append(path)
                for p in paths[:max_files]:
                    c = self.get_file_content(repo_name, p)
                    if c is not None:
                        contents[p] = c

        return contents

    def close(self) -> None:
        self._gh.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
