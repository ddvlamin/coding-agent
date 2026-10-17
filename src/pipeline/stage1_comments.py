"""Phase 1: PR Review Comment Resolver."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.config import RepoConfig
from src.github.client import GitHubClient, LABEL_AGENT_FIX, PullRequestInfo, ReviewThreadInfo
from src.harnesses.base import AgentHarness
from src.sandbox.container import BaseSandbox, DockerSandbox
from src.sandbox.git_workspace import GitWorkspace

logger = logging.getLogger(__name__)


@dataclass
class Stage1Result:
    prs_evaluated: int = 0
    prs_fixed: list[int] = field(default_factory=list)
    resolved_threads: int = 0
    errors: list[str] = field(default_factory=list)


class Stage1CommentsResolver:
    """Resolves unresolved code review comments on agent-managed PRs."""

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

    def run(self, repo_config: RepoConfig) -> Stage1Result:
        owner = repo_config.owner
        repo = repo_config.repo_name
        logger.info("Running Phase 1 (PR Comments Resolver) for %s/%s", owner, repo)
        result = Stage1Result()

        try:
            prs = self.gh.get_open_pull_requests(owner, repo)
        except Exception as e:
            msg = f"Failed to fetch open PRs for {owner}/{repo}: {e}"
            logger.error(msg)
            result.errors.append(msg)
            return result

        bot_username = str(self.gh.bot_username).lower()
        bot_slug = bot_username.removesuffix("[bot]")

        for pr in prs:
            # Check eligibility: bot author OR labeled 'agent fix'
            author_lower = pr.author.lower()
            is_agent_pr = (
                author_lower in (bot_username, bot_slug, f"{bot_slug}[bot]")
                or LABEL_AGENT_FIX in [lb.lower() for lb in pr.labels]
            )
            if not is_agent_pr:
                continue

            result.prs_evaluated += 1
            try:
                threads = self.gh.get_unresolved_review_threads(owner, repo, pr.number)
                if not threads:
                    continue

                logger.info(
                    "Found %d unresolved review thread(s) for PR #%d (%s)",
                    len(threads),
                    pr.number,
                    pr.title,
                )

                if self.dry_run:
                    logger.info("[DRY RUN] Would resolve %d threads on PR #%d", len(threads), pr.number)
                    result.prs_fixed.append(pr.number)
                    result.resolved_threads += len(threads)
                    continue

                # Prepare workspace and checkout PR branch
                repo_dir = self.git.prepare_repo(owner, repo)
                self.git.checkout_branch(repo_dir, pr.head_ref)

                # Build prompt with thread comments
                prompt = self._build_prompt(pr, threads)

                # Execute harness inside sandbox
                harness_res = self.sandbox.execute_harness(
                    self.harness,
                    repo_dir,
                    prompt,
                    test_command=repo_config.test_command,
                )

                if not harness_res.success:
                    err = f"PR #{pr.number}: Agent harness failed or tests did not pass: {harness_res.error}"
                    logger.error(err)
                    result.errors.append(err)
                    self.gh.add_pr_comment(
                        owner,
                        repo,
                        pr.number,
                        f"🤖 **Automated Maintenance Agent Review Fix Attempt Failed**\n\n```\n{harness_res.output[-1000:]}\n```",
                    )
                    continue

                # Commit changes
                committed = self.git.commit_all(
                    repo_dir,
                    f"fix: address review comments on PR #{pr.number}\n\nAutomated fix by Maintenance Agent",
                )
                if committed:
                    self.git.push(repo_dir, pr.head_ref)
                else:
                    logger.info("No code changes needed for PR #%d", pr.number)

                # Resolve GraphQL threads
                resolved_count = 0
                for thread in threads:
                    try:
                        if self.gh.resolve_review_thread(thread.id, f"{owner}/{repo}"):
                            resolved_count += 1
                    except Exception as te:
                        logger.warning("Could not resolve thread %s: %s", thread.id, te)

                # Post confirmation comment
                changes_block = harness_res.diff_summary if committed else "No code changes required (remarks evaluated as non-actionable or already satisfied)."
                self.gh.add_pr_comment(
                    owner,
                    repo,
                    pr.number,
                    f"🤖 **Automated Maintenance Agent**: Evaluated {len(threads)} review comment thread(s) ({resolved_count} resolved).\n\n"
                    f"**Changes:**\n```\n{changes_block}\n```\n"
                    f"**Tests:** {'✅ Passing' if harness_res.test_passed else '⚠️ No test run'}",
                )

                result.prs_fixed.append(pr.number)
                result.resolved_threads += resolved_count
                logger.info("Successfully processed review comments on PR #%d", pr.number)

            except Exception as e:
                err = f"Error processing PR #{pr.number} in {owner}/{repo}: {e}"
                logger.exception(err)
                result.errors.append(err)

        return result

    def _build_prompt(self, pr: PullRequestInfo, threads: list[ReviewThreadInfo]) -> str:
        prompt = (
            f"Evaluate and address the following code review comments on Pull Request #{pr.number}: '{pr.title}'.\n\n"
            f"PR Description:\n{pr.body}\n\n"
            "Unresolved Review Feedback:\n"
        )
        for i, thread in enumerate(threads, 1):
            prompt += f"\n--- Thread {i} (File: {thread.path}, Line: {thread.line}) ---\n"
            for c in thread.comments:
                prompt += f"  [{c.author}]: {c.body}\n"

        prompt += (
            "\nTask:\n"
            "1. Read the referenced files and surrounding context to understand each reviewer remark.\n"
            "2. Critically evaluate each remark for technical validity, accuracy, and value:\n"
            "   - Verify whether the concern actually applies to the current code.\n"
            "   - Check if the suggested change would introduce bugs, regressions, or unnecessary complexity.\n"
            "   - Do not blindly implement suggestions that are incorrect, outdated, or counterproductive.\n"
            "3. Only proceed with surgical code edits for remarks that are valid and valuable. "
            "If a remark is not valid or does not warrant code changes, leave the code untouched and state the technical reason.\n"
            "4. Do not modify unrelated files or code outside the scope of valid review remarks.\n"
        )
        return prompt
