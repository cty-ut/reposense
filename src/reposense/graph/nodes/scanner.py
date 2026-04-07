"""Scanner node — fetches repo metadata, file tree, and file contents from GitHub."""

from __future__ import annotations

import logging
from dataclasses import asdict

from reposense.config import settings
from reposense.graph.state import RepoSenseState
from reposense.tools.github_api import GitHubClient

logger = logging.getLogger(__name__)

SIGNATURE_LINES = 50


def scanner_node(state: RepoSenseState) -> dict:
    """Fetch repo metadata and file tree; read key files for later analysis."""
    repo_name = state["repo_name"]

    with GitHubClient(token=settings.github_token) as client:
        meta = client.get_repo_meta(repo_name)
        tree_nodes = client.get_file_tree(repo_name)

        file_tree = [
            {"path": n.path, "type": n.type, "size": n.size}
            for n in tree_nodes
        ]

        logger.info(
            "Scanned %s: %d nodes (%d files)",
            repo_name,
            len(file_tree),
            sum(1 for n in file_tree if n["type"] == "blob"),
        )

    meta_dict = asdict(meta)
    # Remove the 'url' key that PyGithub sometimes injects into languages
    meta_dict.get("languages", {}).pop("url", None)

    return {
        "repo_meta": meta_dict,
        "file_tree": file_tree,
    }


def _pick_signature_files(file_tree: list[dict]) -> list[str]:
    """Select files worth reading a preview of (entry points, configs, inits)."""
    PRIORITY_NAMES = {
        "main.py", "app.py", "index.ts", "index.js", "server.py",
        "cli.py", "setup.py", "manage.py", "mod.rs", "lib.rs", "main.rs",
        "main.go", "cmd/main.go",
    }
    CONFIG_NAMES = {
        "pyproject.toml", "package.json", "go.mod", "Cargo.toml",
        "setup.cfg", "setup.py", "pom.xml", "build.gradle",
    }
    ENTRY_PATTERNS = {"__init__.py", "__main__.py"}

    picked: list[str] = []
    for node in file_tree:
        if node["type"] != "blob":
            continue
        name = node["path"].rsplit("/", 1)[-1]
        if name in PRIORITY_NAMES or name in CONFIG_NAMES or name in ENTRY_PATTERNS:
            picked.append(node["path"])

    # Also pick README variants
    for node in file_tree:
        if node["type"] == "blob" and node["path"].rsplit("/", 1)[-1].upper().startswith("README"):
            if node["path"].count("/") == 0:  # top-level only
                picked.append(node["path"])

    return list(dict.fromkeys(picked))  # dedupe, preserve order


def file_reader_node(state: RepoSenseState) -> dict:
    """Read signature files (entry points, configs, READMEs) for the analyzer."""
    repo_name = state["repo_name"]
    file_tree = state["file_tree"]
    existing_contents = dict(state.get("file_contents") or {})

    targets = _pick_signature_files(file_tree)
    logger.info("Reading %d signature files for %s", len(targets), repo_name)

    with GitHubClient(token=settings.github_token) as client:
        for path in targets:
            if path in existing_contents:
                continue
            content = client.get_file_content(repo_name, path)
            if content is not None:
                # Only keep the first N lines as a preview to save tokens
                lines = content.split("\n")
                if len(lines) > SIGNATURE_LINES:
                    preview = "\n".join(lines[:SIGNATURE_LINES]) + f"\n... ({len(lines) - SIGNATURE_LINES} more lines)"
                else:
                    preview = content
                existing_contents[path] = preview

    return {"file_contents": existing_contents}


def deep_reader_node(state: RepoSenseState) -> dict:
    """Read full content of key files identified by the analyzer."""
    repo_name = state["repo_name"]
    key_files = state.get("key_files") or []
    existing_contents = dict(state.get("file_contents") or {})

    with GitHubClient(token=settings.github_token) as client:
        for path in key_files:
            if path in existing_contents and "more lines)" not in existing_contents[path]:
                continue  # already have full content
            content = client.get_file_content(repo_name, path)
            if content is not None:
                existing_contents[path] = content

    return {"file_contents": existing_contents}
