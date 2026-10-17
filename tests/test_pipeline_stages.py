"""Unit tests for the four maintenance pipeline stages."""

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from src.config import RepoConfig, SandboxConfig
from src.github.client import CommentInfo, IssueInfo, PullRequestInfo, ReviewThreadInfo
from src.harnesses.base import AgentHarness, HarnessResult
from src.pipeline.stage1_comments import Stage1CommentsResolver
from src.pipeline.stage2_merger import Stage2PRMerger
from src.pipeline.stage3_issues import Stage3IssueImplementer
from src.pipeline.stage4_ideation import ProposalItem, Stage4IdeationEngine
from src.sandbox.container import DockerSandbox


class DummyHarness(AgentHarness):
    def __init__(self, success: bool = True, diff: str = "src/a.py | 1 +", output: str = "Done"):
        self.success = success
        self.diff = diff
        self.out = output

    def execute(
        self,
        workspace_dir: Path,
        prompt: str,
        test_command: str | None = None,
        timeout: int = 1800,
        sandbox: DockerSandbox | None = None,
    ) -> HarnessResult:
        return HarnessResult(
            success=self.success,
            status="completed" if self.success else "failed",
            diff_summary=self.diff,
            test_passed=self.success,
            output=self.out,
            error=None if self.success else "Harness failed",
        )


def test_stage1_comments_resolver_dry_run(tmp_path):
    gh = MagicMock()
    git = MagicMock()
    sandbox = DockerSandbox(SandboxConfig(use_docker=False))
    harness = DummyHarness()

    gh.bot_username = "bot[app]"
    gh.get_open_pull_requests.return_value = [
        PullRequestInfo(
            number=10,
            title="Update parser",
            body="PR body",
            author="bot[app]",
            head_ref="agent/fix",
            base_ref="main",
            head_sha="sha1",
        )
    ]
    gh.get_unresolved_review_threads.return_value = [
        ReviewThreadInfo(id="T_1", path="a.py", line=1, is_resolved=False, comments=[CommentInfo(id="C1", author="alice", body="Fix typo")])
    ]

    resolver = Stage1CommentsResolver(gh, git, sandbox, harness, dry_run=True)
    res = resolver.run(RepoConfig(name="org/repo"))

    assert res.prs_evaluated == 1
    assert 10 in res.prs_fixed
    assert res.resolved_threads == 1
    git.prepare_repo.assert_not_called()


def test_stage2_merger_approved_and_merged(tmp_path):
    gh = MagicMock()
    git = MagicMock()
    sandbox = DockerSandbox(SandboxConfig(use_docker=False))
    harness = DummyHarness()

    gh.get_open_pull_requests.return_value = [
        PullRequestInfo(
            number=20,
            title="Add feature",
            body="Description",
            author="dev",
            head_ref="feature",
            base_ref="main",
            head_sha="sha2",
            mergeable=True,
            mergeable_state="clean",
        )
    ]
    gh.get_pull_request_details.return_value = gh.get_open_pull_requests.return_value[0]
    gh.get_unresolved_review_threads.return_value = []
    gh.check_ci_status.return_value = (True, "All checks passed")
    gh.merge_pull_request.return_value = True

    merger = Stage2PRMerger(gh, git, sandbox, harness, dry_run=False)
    merger._get_review_decision = MagicMock(return_value="APPROVED")

    res = merger.run(RepoConfig(name="org/repo"))
    assert res.prs_evaluated == 1
    assert 20 in res.prs_merged
    gh.merge_pull_request.assert_called_once()


def test_stage3_issue_implementer(tmp_path):
    gh = MagicMock()
    git = MagicMock()
    git.prepare_repo.return_value = tmp_path
    git.commit_all.return_value = True

    sandbox = DockerSandbox(SandboxConfig(use_docker=False))
    harness = DummyHarness(success=True, diff="app.py | 10 ++++++++++")

    gh.bot_username = "agent-bot"
    gh.get_ready_issues.return_value = [
        IssueInfo(
            number=5,
            title="Implement webhook handler",
            body="Need webhook support",
            author="product",
            labels=["ready"],
        )
    ]
    gh.create_pull_request.return_value = PullRequestInfo(
        number=55,
        title="feat: implement #5",
        body="...",
        author="agent-bot",
        head_ref="agent/issue-5-implement-webhook-handler",
        base_ref="main",
        head_sha="sha55",
    )

    implementer = Stage3IssueImplementer(gh, git, sandbox, harness, dry_run=False)
    res = implementer.run(RepoConfig(name="org/repo", max_issues_per_run=1))

    assert res.issues_evaluated == 1
    assert (5, 55) in res.prs_created
    gh.create_pull_request.assert_called_once()
    gh.update_issue_labels.assert_any_call(
        "org", "repo", 5, add_labels=["ready for review"], remove_labels=["in progress"]
    )


def test_stage4_ideation_engine(tmp_path):
    gh = MagicMock()
    git = MagicMock()
    git.prepare_repo.return_value = tmp_path

    # Create dummy README
    readme = tmp_path / "README.md"
    readme.write_text("# Test Repo\nA test repository", encoding="utf-8")

    sandbox = DockerSandbox(SandboxConfig(use_docker=False))
    harness = DummyHarness(
        success=True,
        output='```json\n[\n  {\n    "title": "Add retry mechanism for network timeouts",\n    "category": "feature",\n    "description": "Network calls should have exponential backoff."\n  }\n]\n```',
    )

    gh.get_recent_issues.return_value = [
        IssueInfo(number=1, title="Fix login UI", body="", author="dev")
    ]
    gh.create_issue.return_value = IssueInfo(
        number=99,
        title="[Proposal] Add retry mechanism for network timeouts",
        body="",
        author="agent-bot",
    )

    engine = Stage4IdeationEngine(gh, git, sandbox, harness, dry_run=False)
    res = engine.run(RepoConfig(name="org/repo", ideation_count=1))

    assert res.ideas_generated == 1
    assert 99 in res.proposals_created
    gh.create_issue.assert_called_once()
