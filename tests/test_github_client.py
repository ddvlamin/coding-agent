"""Unit tests for GitHub REST and GraphQL client operations."""

import httpx
import pytest

from src.github.auth import GitHubAppAuth
from src.github.client import GitHubClient


@pytest.fixture
def mock_auth(rsa_key_pem):
    auth = GitHubAppAuth(app_id="12345", private_key_pem=rsa_key_pem, installation_id="67890")
    auth._token_cache["67890"] = ("ghs_mock", 9999999999.0)
    return auth


def test_get_open_pull_requests(mock_auth, monkeypatch):
    client = GitHubClient(mock_auth)

    def mock_get(url, headers=None, params=None):
        return httpx.Response(
            status_code=200,
            json=[
                {
                    "number": 42,
                    "title": "Fix something",
                    "body": "Fixes bug",
                    "user": {"login": "octocat"},
                    "head": {"ref": "fix-branch", "sha": "abc1234"},
                    "base": {"ref": "main"},
                    "labels": [{"name": "agent-fix"}],
                    "mergeable": True,
                    "mergeable_state": "clean",
                }
            ],
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.Client, "get", lambda self, url, headers=None, params=None: mock_get(url, headers, params))

    prs = client.get_open_pull_requests("org", "repo")
    assert len(prs) == 1
    assert prs[0].number == 42
    assert prs[0].author == "octocat"
    assert "agent-fix" in prs[0].labels


def test_check_ci_status_passed(mock_auth, monkeypatch):
    client = GitHubClient(mock_auth)

    def mock_get(url, headers=None, params=None):
        if "check-runs" in url:
            return httpx.Response(
                status_code=200,
                json={"check_runs": [{"name": "tests", "status": "completed", "conclusion": "success"}]},
                request=httpx.Request("GET", url),
            )
        return httpx.Response(
            status_code=200,
            json={"state": "success"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.Client, "get", lambda self, url, headers=None, params=None: mock_get(url, headers, params))

    passed, summary = client.check_ci_status("org", "repo", "sha123")
    assert passed is True
    assert "All checks passed" in summary


def test_check_ci_status_failed(mock_auth, monkeypatch):
    client = GitHubClient(mock_auth)

    def mock_get(url, headers=None, params=None):
        if "check-runs" in url:
            return httpx.Response(
                status_code=200,
                json={"check_runs": [{"name": "linter", "status": "completed", "conclusion": "failure"}]},
                request=httpx.Request("GET", url),
            )
        return httpx.Response(status_code=200, json={"state": "success"}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", lambda self, url, headers=None, params=None: mock_get(url, headers, params))

    passed, summary = client.check_ci_status("org", "repo", "sha123")
    assert passed is False
    assert "Failed checks: linter (failure)" in summary


def test_get_unresolved_review_threads(mock_auth, monkeypatch):
    client = GitHubClient(mock_auth)

    def mock_graphql(query, variables=None, owner_repo=None):
        return {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [
                            {
                                "id": "PRRT_123",
                                "isResolved": False,
                                "path": "src/app.py",
                                "line": 15,
                                "comments": {
                                    "nodes": [
                                        {"id": "C_1", "author": {"login": "reviewer1"}, "body": "Please add docstring"}
                                    ]
                                },
                            },
                            {
                                "id": "PRRT_456",
                                "isResolved": True,
                                "path": "src/utils.py",
                                "line": 20,
                                "comments": {"nodes": []},
                            },
                        ]
                    }
                }
            }
        }

    monkeypatch.setattr(mock_auth, "execute_graphql", mock_graphql)

    threads = client.get_unresolved_review_threads("org", "repo", 101)
    assert len(threads) == 1
    assert threads[0].id == "PRRT_123"
    assert threads[0].path == "src/app.py"
    assert threads[0].comments[0].body == "Please add docstring"


def test_get_ready_issues(mock_auth, monkeypatch):
    client = GitHubClient(mock_auth)

    def mock_get(url, headers=None, params=None):
        return httpx.Response(
            status_code=200,
            json=[
                {
                    "number": 1,
                    "title": "Bug in login",
                    "body": "User cannot log in",
                    "user": {"login": "alice"},
                    "labels": [{"name": "ready"}],
                    "state": "open",
                },
                {
                    "number": 2,
                    "title": "A pull request",
                    "body": "PR",
                    "user": {"login": "bob"},
                    "labels": [{"name": "ready"}],
                    "pull_request": {},
                    "state": "open",
                },
                {
                    "number": 3,
                    "title": "Feature request",
                    "body": "Add dark mode",
                    "user": {"login": "charlie"},
                    "labels": [{"name": "ready to implement"}],
                    "state": "open",
                },
            ],
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.Client, "get", lambda self, url, headers=None, params=None: mock_get(url, headers, params))

    issues = client.get_ready_issues("org", "repo", limit=2)
    assert len(issues) == 2
    assert issues[0].number == 1
    assert issues[1].number == 3
