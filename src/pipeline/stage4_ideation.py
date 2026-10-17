"""Phase 4: Code Review & Ideation Engine."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from src.config import RepoConfig
from src.github.client import GitHubClient, IssueInfo, LABEL_AGENT_PROPOSAL
from src.harnesses.base import AgentHarness
from src.sandbox.container import DockerSandbox
from src.sandbox.git_workspace import GitWorkspace

logger = logging.getLogger(__name__)


@dataclass
class ProposalItem:
    title: str
    category: str  # "tech-debt", "testing", "feature"
    description: str


@dataclass
class Stage4Result:
    ideas_generated: int = 0
    proposals_created: list[int] = field(default_factory=list)  # Issue numbers created
    errors: list[str] = field(default_factory=list)


class Stage4IdeationEngine:
    """Inspects codebase and creates proactive improvement proposals tagged 'agent proposal'."""

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

    def run(self, repo_config: RepoConfig) -> Stage4Result:
        owner = repo_config.owner
        repo = repo_config.repo_name
        default_branch = repo_config.default_branch
        ideation_count = repo_config.ideation_count

        logger.info("Running Phase 4 (Review & Ideation) for %s/%s (target %d ideas)", owner, repo, ideation_count)
        result = Stage4Result()

        try:
            # 1. Fetch past 90-day issues for deduplication
            recent_issues = self.gh.get_recent_issues(owner, repo, days=90)
            existing_titles = [i.title.lower() for i in recent_issues]

            # 2. Prepare repo and extract codebase summary
            repo_dir = self.git.prepare_repo(owner, repo)
            self.git.checkout_branch(repo_dir, default_branch)

            tree_summary = self._get_repo_structure(repo_dir)
            recent_commits = self._get_recent_commits(repo_dir)
            readme_excerpt = self._get_readme_excerpt(repo_dir)

            # 3. Generate proposals via harness
            prompt = self._build_prompt(repo_config, tree_summary, recent_commits, readme_excerpt, ideation_count)
            harness_res = self.sandbox.execute_harness(
                self.harness,
                repo_dir,
                prompt,
                test_command=None,
                timeout=600,
            )

            # Extract structured ideas from output or from an ideas file written by harness
            proposals = self._parse_proposals(repo_dir, harness_res.output)
            result.ideas_generated = len(proposals)

            # 4. Filter duplicates and file GitHub issues
            for prop in proposals[:ideation_count]:
                if self._is_duplicate(prop.title, existing_titles):
                    logger.info("Skipping duplicate proposal: '%s'", prop.title)
                    continue

                if self.dry_run:
                    logger.info("[DRY RUN] Would create proposal issue: '%s'", prop.title)
                    result.proposals_created.append(8888)
                    continue

                body = (
                    f"## 💡 Autonomous Agent Proposal: {prop.category.title()}\n\n"
                    f"{prop.description}\n\n"
                    "---\n"
                    "*Proposed autonomously by Weekly Maintenance Agent based on codebase review.*"
                )
                labels = [LABEL_AGENT_PROPOSAL, f"proposal:{prop.category}"]
                issue = self.gh.create_issue(
                    owner=owner,
                    repo=repo,
                    title=f"[Proposal] {prop.title}",
                    body=body,
                    labels=labels,
                )
                result.proposals_created.append(issue.number)
                logger.info("Created proposal issue #%d: '%s'", issue.number, prop.title)

        except Exception as e:
            err = f"Error during ideation for {owner}/{repo}: {e}"
            logger.exception(err)
            result.errors.append(err)

        return result

    def _get_repo_structure(self, repo_dir: Path) -> str:
        files = []
        for p in repo_dir.rglob("*"):
            if ".git" in p.parts:
                continue
            if p.is_file():
                rel = p.relative_to(repo_dir)
                files.append(str(rel))
            if len(files) >= 100:
                break
        return "\n".join(files[:60])

    def _get_recent_commits(self, repo_dir: Path) -> str:
        res = subprocess.run(
            ["git", "log", "-n", "10", "--oneline"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        return res.stdout.strip()

    def _get_readme_excerpt(self, repo_dir: Path) -> str:
        for fname in ["README.md", "readme.md", "README.rst"]:
            p = repo_dir / fname
            if p.exists():
                try:
                    return p.read_text(encoding="utf-8")[:1500]
                except Exception:
                    pass
        return "No README file found."

    def _build_prompt(
        self,
        repo_config: RepoConfig,
        tree_summary: str,
        recent_commits: str,
        readme_excerpt: str,
        count: int,
    ) -> str:
        return (
            f"You are evaluating the codebase `{repo_config.name}` to identify {count} proactive improvements.\n\n"
            f"Codebase Structure:\n```\n{tree_summary}\n```\n\n"
            f"Recent Git History:\n```\n{recent_commits}\n```\n\n"
            f"README Excerpt:\n```\n{readme_excerpt}\n```\n\n"
            f"Task:\n"
            f"Propose exactly {count} concrete, actionable improvements across these categories:\n"
            "- Tech debt / code quality\n"
            "- Missing unit/integration tests\n"
            "- Useful feature or developer experience improvement\n\n"
            "Format your response as a valid JSON array of objects inside a ```json ... ``` codeblock with fields:\n"
            "[\n"
            "  {\n"
            '    "title": "Clear concise issue title",\n'
            '    "category": "tech-debt" or "testing" or "feature",\n'
            '    "description": "Problem statement, architectural rationale, and proposed implementation steps"\n'
            "  }\n"
            "]\n"
        )

    def _parse_proposals(self, repo_dir: Path, output: str) -> list[ProposalItem]:
        proposals: list[ProposalItem] = []
        # Try finding JSON block in output
        match = re.search(r"```(?:json)?\s*(\[\s*\{.*?\}\s*\])\s*```", output, re.DOTALL)
        raw_json = match.group(1) if match else None

        if not raw_json:
            # Try raw JSON array
            match_raw = re.search(r"(\[\s*\{.*?\}\s*\])", output, re.DOTALL)
            if match_raw:
                raw_json = match_raw.group(1)

        if raw_json:
            try:
                items = json.loads(raw_json)
                for it in items:
                    if "title" in it and "description" in it:
                        proposals.append(
                            ProposalItem(
                                title=it["title"].strip(),
                                category=it.get("category", "tech-debt").strip(),
                                description=it["description"].strip(),
                            )
                        )
            except Exception as e:
                logger.warning("Failed to parse JSON proposals: %s", e)

        # Fallback heuristic if JSON parsing did not return items
        if not proposals:
            lines = [l.strip() for l in output.splitlines() if l.strip()]
            for line in lines:
                if line.startswith(("- Proposal:", "* Proposal:", "### Proposal:")):
                    title = line.split(":", 1)[1].strip()
                    proposals.append(
                        ProposalItem(
                            title=title,
                            category="tech-debt",
                            description="Identified during weekly repository review.",
                        )
                    )

        return proposals

    def _is_duplicate(self, title: str, existing_titles: list[str]) -> bool:
        t_clean = re.sub(r"[^\w\s]", "", title.lower()).strip()
        words = set(t_clean.split())
        for existing in existing_titles:
            e_clean = re.sub(r"[^\w\s]", "", existing).strip()
            e_words = set(e_clean.split())
            if not words or not e_words:
                continue
            overlap = len(words & e_words) / float(len(words | e_words))
            if overlap > 0.6:  # 60% Jaccard word similarity
                return True
        return False
