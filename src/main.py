"""Main orchestrator entrypoint for Autonomous GitHub Maintenance Agent."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from src.config import AgentConfig, RepoConfig, load_config, load_repo_override
from src.github.auth import GitHubAppAuth
from src.github.client import GitHubClient
from src.harnesses import get_harness
from src.pipeline.stage1_comments import Stage1CommentsResolver
from src.pipeline.stage2_merger import Stage2PRMerger
from src.pipeline.stage3_issues import Stage3IssueImplementer
from src.pipeline.stage4_ideation import Stage4IdeationEngine
from src.reporting.email_digest import EmailDigestService, PipelineRunSummary, RepoRunReport
from src.sandbox.container import DockerSandbox
from src.sandbox.git_workspace import GitWorkspace

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("orchestrator")


class Orchestrator:
    """Coordinates execution across all configured repositories and pipeline stages."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.auth = GitHubAppAuth(
            app_id=config.github.app_id,
            private_key_pem=config.github.get_private_key_pem(),
            installation_id=config.github.installation_id,
        )
        self.gh_client = GitHubClient(self.auth, bot_username=config.github.bot_username)
        self.sandbox = DockerSandbox(config.sandbox, deepseek_api_key=config.harness.deepseek_api_key)
        self.git_workspace = GitWorkspace(
            Path(config.workdir),
            self.auth,
            bot_username=config.github.bot_username,
            bot_email=config.github.bot_email,
        )
        self.email_service = EmailDigestService(config.smtp)

    def run(self, target_repos: list[RepoConfig] | None = None, phases: list[int] | None = None) -> PipelineRunSummary:
        start_time = time.time()
        repos = target_repos or self.config.repos

        if not repos:
            logger.warning("No repositories configured or matched to run.")
            return PipelineRunSummary(dry_run=self.config.dry_run)

        logger.info("Starting maintenance run for %d repository/repositories", len(repos))
        summary = PipelineRunSummary(dry_run=self.config.dry_run)

        for repo_cfg in repos:
            repo_report = self._run_for_repo(repo_cfg, phases=phases)
            summary.repo_reports.append(repo_report)

        summary.duration_seconds = time.time() - start_time
        logger.info(
            "Maintenance run finished in %.1fs: %d PRs fixed, %d PRs merged, %d PRs opened, %d proposals filed",
            summary.duration_seconds,
            summary.total_fixed,
            summary.total_merged,
            summary.total_prs_created,
            summary.total_proposals,
        )

        # Dispatch email digest
        try:
            self.email_service.send_digest(summary)
        except Exception as e:
            logger.error("Failed sending email digest: %s", e)

        return summary

    def _run_for_repo(self, base_repo_cfg: RepoConfig, phases: list[int] | None = None) -> RepoRunReport:
        owner = base_repo_cfg.owner
        repo = base_repo_cfg.repo_name
        logger.info("==================================================")
        logger.info("Processing repository: %s/%s", owner, repo)
        logger.info("==================================================")

        report = RepoRunReport(repo_name=f"{owner}/{repo}")

        # Check repository overrides
        repo_dir = Path(self.config.workdir) / f"{owner}_{repo}"
        repo_cfg = load_repo_override(repo_dir, base_repo_cfg)

        harness = get_harness(repo_cfg, self.config)
        active_phases = phases or repo_cfg.enabled_phases

        # Stage 1: PR Review Comments
        if 1 in active_phases:
            try:
                stage1 = Stage1CommentsResolver(
                    gh_client=self.gh_client,
                    git_workspace=self.git_workspace,
                    sandbox=self.sandbox,
                    harness=harness,
                    dry_run=self.config.dry_run,
                )
                report.stage1 = stage1.run(repo_cfg)
            except Exception as e:
                err = f"Phase 1 error in {owner}/{repo}: {e}"
                logger.exception(err)
                report.errors.append(err)

        # Stage 2: PR Merge & Conflicts
        if 2 in active_phases:
            try:
                stage2 = Stage2PRMerger(
                    gh_client=self.gh_client,
                    git_workspace=self.git_workspace,
                    sandbox=self.sandbox,
                    harness=harness,
                    dry_run=self.config.dry_run,
                )
                report.stage2 = stage2.run(repo_cfg)
            except Exception as e:
                err = f"Phase 2 error in {owner}/{repo}: {e}"
                logger.exception(err)
                report.errors.append(err)

        # Stage 3: Pick Ready Issues & Implement
        if 3 in active_phases:
            try:
                stage3 = Stage3IssueImplementer(
                    gh_client=self.gh_client,
                    git_workspace=self.git_workspace,
                    sandbox=self.sandbox,
                    harness=harness,
                    dry_run=self.config.dry_run,
                )
                report.stage3 = stage3.run(repo_cfg)
            except Exception as e:
                err = f"Phase 3 error in {owner}/{repo}: {e}"
                logger.exception(err)
                report.errors.append(err)

        # Stage 4: Code Review & Proactive Ideation
        if 4 in active_phases:
            try:
                stage4 = Stage4IdeationEngine(
                    gh_client=self.gh_client,
                    git_workspace=self.git_workspace,
                    sandbox=self.sandbox,
                    harness=harness,
                    dry_run=self.config.dry_run,
                )
                report.stage4 = stage4.run(repo_cfg)
            except Exception as e:
                err = f"Phase 4 error in {owner}/{repo}: {e}"
                logger.exception(err)
                report.errors.append(err)

        return report


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Autonomous GitHub Maintenance & Development Agent")
    parser.add_argument("--config", "-c", type=str, help="Path to config.yaml file")
    parser.add_argument("--all", "-a", action="store_true", help="Run on all configured repositories")
    parser.add_argument("--repo", "-r", type=str, help="Run on specific repository (format: owner/repo)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate run without modifying remote repos")
    parser.add_argument(
        "--phase",
        "-p",
        type=int,
        nargs="+",
        choices=[1, 2, 3, 4],
        help="Specify which phases to run (1=comments, 2=merge, 3=issues, 4=ideation)",
    )
    return parser.parse_args(args)


def main() -> None:
    args = parse_args()
    config = load_config(args.config)

    if args.dry_run:
        config.dry_run = True

    target_repos = None
    if args.repo:
        matched = [r for r in config.repos if r.name.lower() == args.repo.lower()]
        if not matched:
            parts = args.repo.split("/")
            if len(parts) == 2:
                matched = [RepoConfig(name=args.repo)]
            else:
                logger.error("Invalid repo format: '%s'. Expected 'owner/repo'.", args.repo)
                sys.exit(1)
        target_repos = matched
    elif not args.all and not config.repos:
        logger.error("No repositories specified. Use --all or --repo <owner/repo>.")
        sys.exit(1)

    try:
        orchestrator = Orchestrator(config)
        summary = orchestrator.run(target_repos=target_repos, phases=args.phase)
        print("\n" + orchestrator.email_service.render_text(summary))
    except Exception as e:
        logger.exception("Fatal orchestrator error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
