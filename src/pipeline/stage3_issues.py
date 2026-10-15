"""Phase 3: Issue Implementation Worker."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.config import RepoConfig
from src.github.client import GitHubClient, IssueInfo
from src.harnesses.base import AgentHarness
from src.sandbox.container import DockerSandbox
from src.sandbox.git_workspace import GitWorkspace

logger = logging.getLogger(__name__)


@dataclass
class Stage3Result:
    issues_evaluated: int = 0
    prs_created: list[tuple[int, int]] = field(default_factory=list)  # (issue_number, pr_number)
    blocked_issues: list[tuple[int, str]] = field(default_factory=list)  # (issue_number, reason)
    errors: list[str] = field(default_factory=list)


def slugify(text: str) -> str:
    """Create URL/branch-safe slug."""
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)[:30]


class Stage3IssueImplementer:
    """Implements issues marked 'ready' or 'ready to implement' and opens review PRs."""

    def __init__(
        self,
        gh_client: GitHubClient,
        git_workspace: GitWorkspace,
        sandbox: DockerSandbox,
        harness: AgentHarness,
        dry_run: bool = False,
    ) -> None:
        self.gh = gh_client
        self.git = git_workspace
        self.sandbox = sandbox
        self.harness = harness
        self.dry_run = dry_run

    def run(self, repo_config: RepoConfig) -> Stage3Result:
        owner = repo_config.owner
        repo = repo_config.repo_name
        default_branch = repo_config.default_branch
        max_issues = repo_config.max_issues_per_run

        logger.info("Running Phase 3 (Issue Implementer) for %s/%s (max %d issues)", owner, repo, max_issues)
        result = Stage3Result()

        try:
            ready_issues = self.gh.get_ready_issues(owner, repo, limit=max_issues)
        except Exception as e:
            msg = f"Failed to fetch ready issues for {owner}/{repo}: {e}"
            logger.error(msg)
            result.errors.append(msg)
            return result

        result.issues_evaluated = len(ready_issues)
        if not ready_issues:
            logger.info("No 'ready' issues found for %s/%s", owner, repo)
            return result

        bot_username = self.gh.auth.app_id

        for issue in ready_issues:
            logger.info("Picked issue #%d: '%s'", issue.number, issue.title)
            branch_slug = slugify(issue.title)
            branch_name = f"agent/issue-{issue.number}-{branch_slug}"

            if self.dry_run:
                logger.info("[DRY RUN] Would implement issue #%d and branch %s", issue.number, branch_name)
                result.prs_created.append((issue.number, 9999))
                continue

            # 1. Swap labels: remove 'ready'/'ready to implement', add 'in-progress'
            try:
                self.gh.update_issue_labels(
                    owner,
                    repo,
                    issue.number,
                    add_labels=["in-progress"],
                    remove_labels=["ready", "ready to implement"],
                )
                self.gh.assign_issue(owner, repo, issue.number, str(bot_username))
            except Exception as e:
                logger.warning("Could not update initial issue labels/assignee for #%d: %s", issue.number, e)

            # 2. Checkout fresh branch from default_branch
            try:
                repo_dir = self.git.prepare_repo(owner, repo)
                self.git.checkout_branch(repo_dir, branch_name, create=True, base_branch=default_branch)

                # 3. Build prompt and execute harness
                prompt = self._build_prompt(issue)
                harness_res = self.sandbox.execute_harness(
                    self.harness,
                    repo_dir,
                    prompt,
                    test_command=repo_config.test_command,
                )

                # 4. Check results
                if not harness_res.success:
                    diag = f"Issue implementation or tests failed: {harness_res.error}\n\n```\n{harness_res.output[-1500:]}\n```"
                    logger.warning("Issue #%d failed: %s", issue.number, harness_res.error)
                    self.gh.add_issue_comment(
                        owner,
                        repo,
                        issue.number,
                        f"🤖 **Automated Maintenance Agent Attempt Failed**\n\n{diag}",
                    )
                    self.gh.update_issue_labels(
                        owner,
                        repo,
                        issue.number,
                        add_labels=["agent-blocked"],
                        remove_labels=["in-progress"],
                    )
                    result.blocked_issues.append((issue.number, harness_res.error or "Failed test/build"))
                    continue

                # 5. Commit and Push
                committed = self.git.commit_all(
                    repo_dir,
                    f"feat: implement issue #{issue.number} - {issue.title}\n\nCloses #{issue.number}\nAutomated implementation by Maintenance Agent",
                )
                if not committed:
                    logger.warning("No code changes made for issue #%d", issue.number)
                    self.gh.add_issue_comment(
                        owner,
                        repo,
                        issue.number,
                        "🤖 **Automated Maintenance Agent**: Finished analysis but no changes were required or generated.",
                    )
                    self.gh.update_issue_labels(
                        owner,
                        repo,
                        issue.number,
                        add_labels=["agent-blocked"],
                        remove_labels=["in-progress"],
                    )
                    result.blocked_issues.append((issue.number, "No changes generated"))
                    continue

                self.git.push(repo_dir, branch_name)

                # 6. Create Pull Request
                pr_title = f"feat: implement #{issue.number} - {issue.title}"
                pr_body = (
                    f"## Autonomous Maintenance Agent Implementation\n\n"
                    f"Closes #{issue.number}\n\n"
                    f"### Summary of Changes\n```\n{harness_res.diff_summary}\n```\n\n"
                    f"### Test Results\n{'✅ All tests passed' if harness_res.test_passed else '⚠️ No tests configured'}\n\n"
                    f"Automated PR submitted for review."
                )
                pr = self.gh.create_pull_request(
                    owner=owner,
                    repo=repo,
                    title=pr_title,
                    body=pr_body,
                    head=branch_name,
                    base=default_branch,
                    labels=["ready-for-review", "agent-managed"],
                )

                # 7. Update Issue status
                self.gh.update_issue_labels(
                    owner,
                    repo,
                    issue.number,
                    add_labels=["ready-for-review"],
                    remove_labels=["in-progress"],
                )
                self.gh.add_issue_comment(
                    owner,
                    repo,
                    issue.number,
                    f"🤖 **Automated Maintenance Agent**: Implementation complete. PR created: #{pr.number} ({pr.title}).",
                )

                result.prs_created.append((issue.number, pr.number))
                logger.info("Successfully created PR #%d for issue #%d", pr.number, issue.number)

            except Exception as e:
                err = f"Error implementing issue #{issue.number} in {owner}/{repo}: {e}"
                logger.exception(err)
                result.errors.append(err)
                try:
                    self.gh.update_issue_labels(
                        owner,
                        repo,
                        issue.number,
                        add_labels=["agent-blocked"],
                        remove_labels=["in-progress"],
                    )
                except Exception:
                    pass

        return result

    def _build_prompt(self, issue: IssueInfo) -> str:
        return (
            f"Please implement the following GitHub issue:\n\n"
            f"Issue #{issue.number}: {issue.title}\n"
            f"Author: {issue.author}\n\n"
            f"Description:\n{issue.body}\n\n"
            "Implementation Guidelines:\n"
            "1. Implement the feature, fix, or enhancement described above.\n"
            "2. Follow the repository's existing architecture and coding conventions.\n"
            "3. Add or update unit tests covering the new functionality.\n"
            "4. Keep code changes surgical and clean.\n"
        )
