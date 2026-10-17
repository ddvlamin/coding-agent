"""Phase 2: PR Auto-Merge & Conflict Resolver."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from src.config import RepoConfig
from src.github.client import GitHubClient, PullRequestInfo
from src.harnesses.base import AgentHarness
from src.sandbox.container import BaseSandbox, DockerSandbox
from src.sandbox.git_workspace import GitWorkspace

logger = logging.getLogger(__name__)


@dataclass
class Stage2Result:
    prs_evaluated: int = 0
    prs_merged: list[int] = field(default_factory=list)
    conflicts_resolved: list[int] = field(default_factory=list)
    blocked_prs: list[tuple[int, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class Stage2PRMerger:
    """Merges approved pull requests and resolves merge conflicts autonomously."""

    def __init__(
        self,
        gh_client: GitHubClient,
        git_workspace: GitWorkspace,
        sandbox: BaseSandbox,
        harness: AgentHarness,
        dry_run: bool = False,
    ) -> None:
        self.gh = gh_client
        self.git = git_workspace
        self.sandbox = sandbox
        self.harness = harness
        self.dry_run = dry_run

    def run(self, repo_config: RepoConfig) -> Stage2Result:
        owner = repo_config.owner
        repo = repo_config.repo_name
        default_branch = repo_config.default_branch
        logger.info("Running Phase 2 (PR Auto-Merge & Conflicts) for %s/%s", owner, repo)
        result = Stage2Result()

        try:
            prs = self.gh.get_open_pull_requests(owner, repo)
        except Exception as e:
            msg = f"Failed to fetch open PRs for {owner}/{repo}: {e}"
            logger.error(msg)
            result.errors.append(msg)
            return result

        for pr in prs:
            result.prs_evaluated += 1
            try:
                # 1. Fetch detailed PR state and review decision
                pr_detail = self.gh.get_pull_request_details(owner, repo, pr.number)

                # Check review decision via GraphQL
                threads = self.gh.get_unresolved_review_threads(owner, repo, pr.number)
                if threads:
                    logger.info("PR #%d has %d unresolved review thread(s). Skipping merge.", pr.number, len(threads))
                    continue

                # Query review decision via GraphQL
                review_decision = self._get_review_decision(owner, repo, pr.number)
                if review_decision != "APPROVED":
                    logger.info("PR #%d review decision is '%s' (not APPROVED). Skipping merge.", pr.number, review_decision)
                    continue

                logger.info("PR #%d is APPROVED with 0 unresolved review threads.", pr.number)

                # 2. Check for merge conflicts
                has_conflict = pr_detail.mergeable is False or pr_detail.mergeable_state == "dirty"
                if has_conflict:
                    logger.info("PR #%d has merge conflicts. Attempting automated resolution...", pr.number)
                    if self.dry_run:
                        logger.info("[DRY RUN] Would resolve conflicts on PR #%d", pr.number)
                        result.conflicts_resolved.append(pr.number)
                    else:
                        resolved = self._resolve_conflicts(repo_config, pr_detail)
                        if resolved:
                            result.conflicts_resolved.append(pr.number)
                            # Refresh details
                            pr_detail = self.gh.get_pull_request_details(owner, repo, pr.number)
                        else:
                            msg = f"PR #{pr.number} conflict resolution failed"
                            result.blocked_prs.append((pr.number, msg))
                            continue

                # 3. Check CI Status
                ci_passed, ci_summary = self.gh.check_ci_status(owner, repo, pr_detail.head_sha)
                if not ci_passed:
                    logger.warning("PR #%d CI checks not green: %s", pr.number, ci_summary)
                    result.blocked_prs.append((pr.number, f"CI not green: {ci_summary}"))
                    continue

                # 4. Execute Merge
                if self.dry_run:
                    logger.info("[DRY RUN] Would squash-merge approved PR #%d", pr.number)
                    result.prs_merged.append(pr.number)
                    continue

                merged = self.gh.merge_pull_request(
                    owner,
                    repo,
                    pr.number,
                    commit_title=f"{pr.title} (#{pr.number})",
                    merge_method="squash",
                )
                if merged:
                    logger.info("Successfully merged PR #%d into %s", pr.number, default_branch)
                    result.prs_merged.append(pr.number)
                    self.gh.add_pr_comment(
                        owner,
                        repo,
                        pr.number,
                        "🤖 **Automated Maintenance Agent**: Pull request successfully merged (all approvals and checks verified).",
                    )
                else:
                    msg = f"Failed to merge PR #{pr.number} via GitHub API"
                    logger.error(msg)
                    result.blocked_prs.append((pr.number, msg))

            except Exception as e:
                err = f"Error evaluating PR #{pr.number} in {owner}/{repo}: {e}"
                logger.exception(err)
                result.errors.append(err)

        return result

    def _get_review_decision(self, owner: str, repo: str, pr_number: int) -> str:
        """Fetch PR reviewDecision (APPROVED, CHANGES_REQUESTED, REVIEW_REQUIRED)."""
        query = """
        query GetPRDecision($owner: String!, $name: String!, $pr: Int!) {
          repository(owner: $owner, name: $name) {
            pullRequest(number: $pr) {
              reviewDecision
            }
          }
        }
        """
        try:
            data = self.gh.auth.execute_graphql(query, {"owner": owner, "name": repo, "pr": pr_number}, f"{owner}/{repo}")
            return data.get("repository", {}).get("pullRequest", {}).get("reviewDecision") or "NONE"
        except Exception as e:
            logger.warning("Could not query reviewDecision for PR #%d: %s", pr_number, e)
            return "UNKNOWN"

    def _resolve_conflicts(self, repo_config: RepoConfig, pr: PullRequestInfo) -> bool:
        """Merge base branch into PR branch and prompt harness to resolve conflict markers."""
        owner = repo_config.owner
        repo = repo_config.repo_name
        default_branch = repo_config.default_branch

        repo_dir = self.git.prepare_repo(owner, repo)
        self.git.checkout_branch(repo_dir, pr.head_ref)

        # Attempt git merge origin/main
        merge_cmd = ["git", "merge", f"origin/{default_branch}", "--no-commit"]
        res = subprocess.run(merge_cmd, cwd=repo_dir, capture_output=True, text=True, check=False)

        if res.returncode == 0:
            # Merged cleanly without conflict markers
            self.git.commit_all(repo_dir, f"Merge remote-tracking branch 'origin/{default_branch}' into {pr.head_ref}")
            self.git.push(repo_dir, pr.head_ref)
            return True

        # Has conflict markers
        prompt = (
            f"Pull Request #{pr.number} has git merge conflicts against `{default_branch}`.\n\n"
            "Conflict resolution guidelines:\n"
            "1. Inspect all files containing `<<<<<<<`, `=======`, `>>>>>>>` conflict markers.\n"
            "2. Resolve the conflicts cleanly, keeping both necessary changes and removing conflict markers.\n"
            "3. Ensure the project builds and all tests pass.\n"
        )
        harness_res = self.sandbox.execute_harness(
            self.harness,
            repo_dir,
            prompt,
            test_command=repo_config.test_command,
        )

        if not harness_res.success:
            logger.error("Conflict resolution failed for PR #%d: %s", pr.number, harness_res.error)
            # Abort merge
            subprocess.run(["git", "merge", "--abort"], cwd=repo_dir, capture_output=True, check=False)
            return False

        committed = self.git.commit_all(
            repo_dir,
            f"chore: resolve merge conflicts with {default_branch}\n\nAutomated conflict resolution by Maintenance Agent",
        )
        self.git.push(repo_dir, pr.head_ref)
        logger.info("Pushed conflict-resolved commit for PR #%d", pr.number)
        return True
