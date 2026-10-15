"""GitHub REST and GraphQL client operations."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any
import httpx

from src.github.auth import GitHubAppAuth, GitHubAuthError

logger = logging.getLogger(__name__)


@dataclass
class CommentInfo:
    id: str
    author: str
    body: str
    created_at: str = ""


@dataclass
class ReviewThreadInfo:
    id: str
    path: str
    line: int | None
    is_resolved: bool
    comments: list[CommentInfo] = field(default_factory=list)


@dataclass
class PullRequestInfo:
    number: int
    title: str
    body: str
    author: str
    head_ref: str
    base_ref: str
    head_sha: str
    labels: list[str] = field(default_factory=list)
    mergeable: bool | None = None
    mergeable_state: str | None = None
    review_decision: str | None = None  # "APPROVED", "CHANGES_REQUESTED", etc.


@dataclass
class IssueInfo:
    number: int
    title: str
    body: str
    author: str
    labels: list[str] = field(default_factory=list)
    state: str = "open"
    created_at: str = ""


class GitHubClient:
    """High-level client for GitHub REST and GraphQL APIs used by the agent."""

    def __init__(self, auth: GitHubAppAuth, base_url: str = "https://api.github.com") -> None:
        self.auth = auth
        self.base_url = base_url.rstrip("/")

    def _http_get(self, endpoint: str, owner_repo: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = self.auth.get_auth_headers(owner_repo)
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, headers=headers, params=params)
            if resp.status_code != 200:
                raise GitHubAuthError(f"GET {endpoint} failed ({resp.status_code}): {resp.text}")
            return resp.json()

    def _http_post(self, endpoint: str, owner_repo: str, data: dict[str, Any]) -> Any:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = self.auth.get_auth_headers(owner_repo)
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, headers=headers, json=data)
            if resp.status_code not in (200, 201):
                raise GitHubAuthError(f"POST {endpoint} failed ({resp.status_code}): {resp.text}")
            return resp.json()

    def _http_put(self, endpoint: str, owner_repo: str, data: dict[str, Any]) -> Any:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = self.auth.get_auth_headers(owner_repo)
        with httpx.Client(timeout=30.0) as client:
            resp = client.put(url, headers=headers, json=data)
            if resp.status_code not in (200, 201):
                raise GitHubAuthError(f"PUT {endpoint} failed ({resp.status_code}): {resp.text}")
            return resp.json()

    def _http_delete(self, endpoint: str, owner_repo: str) -> None:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = self.auth.get_auth_headers(owner_repo)
        with httpx.Client(timeout=30.0) as client:
            resp = client.delete(url, headers=headers)
            if resp.status_code not in (200, 204, 404):
                raise GitHubAuthError(f"DELETE {endpoint} failed ({resp.status_code}): {resp.text}")

    # ==========================================
    # Pull Request Methods
    # ==========================================

    def get_open_pull_requests(self, owner: str, repo: str) -> list[PullRequestInfo]:
        """Fetch all open pull requests for a repository."""
        owner_repo = f"{owner}/{repo}"
        data = self._http_get(f"/repos/{owner}/{repo}/pulls", owner_repo, params={"state": "open", "per_page": 50})
        prs = []
        for item in data:
            prs.append(
                PullRequestInfo(
                    number=item["number"],
                    title=item["title"],
                    body=item.get("body") or "",
                    author=item["user"]["login"] if item.get("user") else "",
                    head_ref=item["head"]["ref"],
                    base_ref=item["base"]["ref"],
                    head_sha=item["head"]["sha"],
                    labels=[lb["name"] for lb in item.get("labels", [])],
                    mergeable=item.get("mergeable"),
                    mergeable_state=item.get("mergeable_state"),
                )
            )
        return prs

    def get_pull_request_details(self, owner: str, repo: str, pr_number: int) -> PullRequestInfo:
        """Fetch full details of a specific pull request including mergeable state."""
        owner_repo = f"{owner}/{repo}"
        item = self._http_get(f"/repos/{owner}/{repo}/pulls/{pr_number}", owner_repo)
        return PullRequestInfo(
            number=item["number"],
            title=item["title"],
            body=item.get("body") or "",
            author=item["user"]["login"] if item.get("user") else "",
            head_ref=item["head"]["ref"],
            base_ref=item["base"]["ref"],
            head_sha=item["head"]["sha"],
            labels=[lb["name"] for lb in item.get("labels", [])],
            mergeable=item.get("mergeable"),
            mergeable_state=item.get("mergeable_state"),
        )

    def get_unresolved_review_threads(self, owner: str, repo: str, pr_number: int) -> list[ReviewThreadInfo]:
        """Fetch unresolved review comment threads using GraphQL."""
        owner_repo = f"{owner}/{repo}"
        query = """
        query GetPRReviewThreads($owner: String!, $name: String!, $pr: Int!) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $pr) {
              reviewDecision
              reviewThreads(first: 50) {
                nodes {
                  id
                  isResolved
                  path
                  line
                  originalLine
                  comments(first: 20) {
                    nodes {
                      id
                      body
                      author { login }
                      createdAt
                    }
                  }
                }
              }
            }
          }
        }
        """
        data = self.auth.execute_graphql(query, {"owner": owner, "name": repo, "pr": pr_number}, owner_repo)
        pr_data = data.get("repository", {}).get("pullRequest") or {}
        threads_nodes = pr_data.get("reviewThreads", {}).get("nodes", [])

        unresolved: list[ReviewThreadInfo] = []
        for node in threads_nodes:
            if not node.get("isResolved"):
                comments = []
                for c in node.get("comments", {}).get("nodes", []):
                    comments.append(
                        CommentInfo(
                            id=c.get("id", ""),
                            author=c.get("author", {}).get("login", "") if c.get("author") else "",
                            body=c.get("body", ""),
                            created_at=c.get("createdAt", ""),
                        )
                    )
                unresolved.append(
                    ReviewThreadInfo(
                        id=node["id"],
                        path=node.get("path") or "",
                        line=node.get("line") or node.get("originalLine"),
                        is_resolved=False,
                        comments=comments,
                    )
                )
        return unresolved

    def resolve_review_thread(self, thread_id: str, owner_repo: str | None = None) -> bool:
        """Resolve a review thread via GraphQL mutation."""
        mutation = """
        mutation ResolveThread($threadId: ID!) {
          resolveReviewThread(input: { threadId: $threadId }) {
            thread {
              id
              isResolved
            }
          }
        }
        """
        res = self.auth.execute_graphql(mutation, {"threadId": thread_id}, owner_repo)
        return bool(res.get("resolveReviewThread", {}).get("thread", {}).get("isResolved"))

    def check_ci_status(self, owner: str, repo: str, ref: str) -> tuple[bool, str]:
        """Check CI check runs and commit status for a ref."""
        owner_repo = f"{owner}/{repo}"
        # 1. Check Runs
        check_runs_data = self._http_get(f"/repos/{owner}/{repo}/commits/{ref}/check-runs", owner_repo)
        check_runs = check_runs_data.get("check_runs", [])

        # 2. Combined Commit Status
        status_data = self._http_get(f"/repos/{owner}/{repo}/commits/{ref}/status", owner_repo)
        combined_state = status_data.get("state", "success")  # pending, success, failure, error

        if combined_state in ("failure", "error"):
            return False, f"Combined commit status: {combined_state}"

        if not check_runs and combined_state == "success":
            return True, "No check runs, status is clean"

        failed_checks = []
        pending_checks = []
        for cr in check_runs:
            status = cr.get("status")  # queued, in_progress, completed
            conclusion = cr.get("conclusion")  # success, failure, neutral, cancelled, timed_out, action_required
            name = cr.get("name", "unknown")
            if status != "completed":
                pending_checks.append(name)
            elif conclusion not in ("success", "neutral", "skipped"):
                failed_checks.append(f"{name} ({conclusion})")

        if failed_checks:
            return False, f"Failed checks: {', '.join(failed_checks)}"
        if pending_checks:
            return False, f"Pending checks: {', '.join(pending_checks)}"
        if combined_state == "pending":
            return False, "Commit status is pending"

        return True, "All checks passed"

    def merge_pull_request(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_title: str | None = None,
        commit_message: str | None = None,
        merge_method: str = "squash",
    ) -> bool:
        """Merge pull request via REST API."""
        owner_repo = f"{owner}/{repo}"
        payload: dict[str, Any] = {"merge_method": merge_method}
        if commit_title:
            payload["commit_title"] = commit_title
        if commit_message:
            payload["commit_message"] = commit_message

        res = self._http_put(f"/repos/{owner}/{repo}/pulls/{pr_number}/merge", owner_repo, payload)
        return bool(res.get("merged"))

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
        labels: list[str] | None = None,
    ) -> PullRequestInfo:
        """Create a new pull request and assign labels."""
        owner_repo = f"{owner}/{repo}"
        payload = {
            "title": title,
            "body": body,
            "head": head,
            "base": base,
        }
        res = self._http_post(f"/repos/{owner}/{repo}/pulls", owner_repo, payload)
        pr_number = res["number"]

        if labels:
            try:
                self._http_post(f"/repos/{owner}/{repo}/issues/{pr_number}/labels", owner_repo, {"labels": labels})
            except Exception as e:
                logger.warning("Could not set labels on PR #%d: %s", pr_number, e)

        return PullRequestInfo(
            number=pr_number,
            title=res["title"],
            body=res.get("body") or "",
            author=res["user"]["login"] if res.get("user") else "",
            head_ref=head,
            base_ref=base,
            head_sha=res["head"]["sha"],
            labels=labels or [],
        )

    def add_pr_comment(self, owner: str, repo: str, pr_number: int, body: str) -> None:
        """Add general comment to a PR."""
        owner_repo = f"{owner}/{repo}"
        self._http_post(f"/repos/{owner}/{repo}/issues/{pr_number}/comments", owner_repo, {"body": body})

    # ==========================================
    # Issue Methods
    # ==========================================

    def get_ready_issues(self, owner: str, repo: str, limit: int = 2) -> list[IssueInfo]:
        """Fetch open issues labeled 'ready' or 'ready to implement'."""
        owner_repo = f"{owner}/{repo}"
        # We query issues with state=open
        data = self._http_get(
            f"/repos/{owner}/{repo}/issues",
            owner_repo,
            params={"state": "open", "per_page": 50, "sort": "created", "direction": "asc"},
        )
        ready_issues: list[IssueInfo] = []
        for item in data:
            # Skip pull requests returned in issues API
            if "pull_request" in item:
                continue
            labels = [lb["name"].lower() for lb in item.get("labels", [])]
            if "ready" in labels or "ready to implement" in labels:
                ready_issues.append(
                    IssueInfo(
                        number=item["number"],
                        title=item["title"],
                        body=item.get("body") or "",
                        author=item["user"]["login"] if item.get("user") else "",
                        labels=[lb["name"] for lb in item.get("labels", [])],
                        state=item.get("state", "open"),
                        created_at=item.get("created_at", ""),
                    )
                )
                if len(ready_issues) >= limit:
                    break
        return ready_issues

    def update_issue_labels(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> None:
        """Add and/or remove labels on an issue."""
        owner_repo = f"{owner}/{repo}"
        if remove_labels:
            for rl in remove_labels:
                try:
                    self._http_delete(f"/repos/{owner}/{repo}/issues/{issue_number}/labels/{rl}", owner_repo)
                except Exception as e:
                    logger.debug("Label '%s' not present to remove: %s", rl, e)

        if add_labels:
            self._http_post(f"/repos/{owner}/{repo}/issues/{issue_number}/labels", owner_repo, {"labels": add_labels})

    def assign_issue(self, owner: str, repo: str, issue_number: int, assignee: str) -> None:
        """Assign issue to a user/bot."""
        owner_repo = f"{owner}/{repo}"
        try:
            self._http_post(f"/repos/{owner}/{repo}/issues/{issue_number}/assignees", owner_repo, {"assignees": [assignee]})
        except Exception as e:
            logger.warning("Could not assign issue #%d to '%s': %s", issue_number, assignee, e)

    def add_issue_comment(self, owner: str, repo: str, issue_number: int, body: str) -> None:
        """Add comment to an issue."""
        owner_repo = f"{owner}/{repo}"
        self._http_post(f"/repos/{owner}/{repo}/issues/{issue_number}/comments", owner_repo, {"body": body})

    def create_issue(self, owner: str, repo: str, title: str, body: str, labels: list[str]) -> IssueInfo:
        """Create a new issue with labels."""
        owner_repo = f"{owner}/{repo}"
        payload = {"title": title, "body": body, "labels": labels}
        res = self._http_post(f"/repos/{owner}/{repo}/issues", owner_repo, payload)
        return IssueInfo(
            number=res["number"],
            title=res["title"],
            body=res.get("body") or "",
            author=res["user"]["login"] if res.get("user") else "",
            labels=labels,
            state="open",
            created_at=res.get("created_at", ""),
        )

    def get_recent_issues(self, owner: str, repo: str, days: int = 90) -> list[IssueInfo]:
        """Fetch all issues created/updated in the last N days (for semantic deduplication)."""
        owner_repo = f"{owner}/{repo}"
        since_date = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        data = self._http_get(
            f"/repos/{owner}/{repo}/issues",
            owner_repo,
            params={"state": "all", "since": since_date, "per_page": 100},
        )
        issues: list[IssueInfo] = []
        for item in data:
            if "pull_request" in item:
                continue
            issues.append(
                IssueInfo(
                    number=item["number"],
                    title=item["title"],
                    body=item.get("body") or "",
                    author=item["user"]["login"] if item.get("user") else "",
                    labels=[lb["name"] for lb in item.get("labels", [])],
                    state=item.get("state", "open"),
                    created_at=item.get("created_at", ""),
                )
            )
        return issues
