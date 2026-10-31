"""Batched commit -> PR lookup (issue #8), against a fake GitHub API."""

import json

import httpx

from reposense.analysis.file_history import file_history
from reposense.core.cache import JsonCache
from reposense.core.github import GitHubClient

A, B = "a" * 40, "b" * 40


def client(tmp_path, handler):
    gh = GitHubClient("acme/pkg", token="t", cache=JsonCache("acme/pkg", root=tmp_path / "c"))
    gh.http = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    return gh


def test_one_graphql_request_for_many_commits_then_cache(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert A in body["query"] and B in body["query"]
        return httpx.Response(200, json={"data": {"repository": {
            "c0": {"associatedPullRequests": {"nodes": [{"number": 7, "mergedAt": None},
                                                        {"number": 9, "mergedAt": "2026-01-01T00:00:00Z"}]}},
            "c1": {"associatedPullRequests": {"nodes": []}},
        }}})

    gh = client(tmp_path, handler)
    assert gh.prs_for_commits([A, B]) == {A: 9, B: None}  # the merged PR wins, like pr_for_commit
    assert len(requests) == 1 and requests[0].url.path == "/graphql"
    assert gh.prs_for_commits([A, B]) == {A: 9, B: None}
    assert len(requests) == 1  # second time from the cache
    assert gh.pr_for_commit(A) == 9  # shares the cache with the REST lookup


def test_falls_back_to_rest_when_graphql_fails(tmp_path):
    def handler(request):
        if request.url.path == "/graphql":
            return httpx.Response(401, json={"message": "Bad credentials"})
        sha = request.url.path.split("/")[-2]
        return httpx.Response(200, json=[{"number": 5, "merged_at": "x"}] if sha == A else [])

    assert client(tmp_path, handler).prs_for_commits([A, B]) == {A: 5, B: None}


def test_history_looks_up_only_commits_without_a_pr_in_the_subject(ws):
    class RecordingGitHub:
        asked = None

        def prs_for_commits(self, shas):
            self.asked = shas
            return {sha: 3 for sha in shas}

    gh = RecordingGitHub()
    h = file_history(ws, "src/pkg/core.py", gh, limit=5)
    by_subject = {c["subject"]: c["pr"]["number"] for c in h["commits"]}
    assert by_subject == {"Add ellipsis for nargs (#42)": 42, "initial": 3}
    assert len(gh.asked) == 1  # only "initial" needed GitHub
