"""Git workspace operations (cloning, branching, pushing)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from src.github.auth import GitHubAppAuth

logger = logging.getLogger(__name__)


class GitWorkspace:
    """Manages cloning, branch checkout, committing, and pushing for a repository."""

    def __init__(
        self,
        base_workdir: Path,
        auth: GitHubAppAuth,
        bot_username: str = "app[bot]",
        bot_email: str = "41898282+github-actions[bot]@users.noreply.github.com",
    ) -> None:
        self.base_workdir = Path(base_workdir)
        self.auth = auth
        self.bot_username = bot_username
        self.bot_email = bot_email

    def prepare_repo(self, owner: str, repo: str) -> Path:
        """Clones or updates repository workspace."""
        repo_dir = self.base_workdir / f"{owner}_{repo}"
        token = self.auth.get_installation_token(f"{owner}/{repo}")
        clone_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"

        if repo_dir.exists() and (repo_dir / ".git").exists():
            logger.info("Updating existing repo at %s", repo_dir)
            # Update remote URL with fresh token
            self._run_git(repo_dir, ["remote", "set-url", "origin", clone_url])
            self._run_git(repo_dir, ["fetch", "--all", "--prune"])
        else:
            if repo_dir.exists():
                shutil.rmtree(repo_dir, ignore_errors=True)
            repo_dir.parent.mkdir(parents=True, exist_ok=True)
            logger.info("Cloning %s/%s into %s", owner, repo, repo_dir)
            self._run_git(self.base_workdir, ["clone", clone_url, str(repo_dir)])

        # Configure git identity
        self._run_git(repo_dir, ["config", "user.name", self.bot_username])
        self._run_git(repo_dir, ["config", "user.email", self.bot_email])
        return repo_dir

    def checkout_branch(self, repo_dir: Path, branch_name: str, create: bool = False, base_branch: str = "main") -> None:
        """Check out an existing or new branch."""
        self._run_git(repo_dir, ["fetch", "origin"])
        if create:
            # Checkout base branch first
            self._run_git(repo_dir, ["checkout", base_branch])
            self._run_git(repo_dir, ["pull", "origin", base_branch])
            self._run_git(repo_dir, ["checkout", "-B", branch_name])
        else:
            self._run_git(repo_dir, ["checkout", branch_name])
            self._run_git(repo_dir, ["pull", "origin", branch_name])

    def commit_all(self, repo_dir: Path, message: str) -> bool:
        """Stage all changes and commit. Returns True if a commit was made."""
        self._run_git(repo_dir, ["add", "-A"])
        res = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=repo_dir,
            check=False,
        )
        if res.returncode == 0:
            logger.info("No staged changes to commit in %s", repo_dir)
            return False

        self._run_git(repo_dir, ["commit", "-m", message])
        logger.info("Committed changes in %s: %s", repo_dir, message)
        return True

    def push(self, repo_dir: Path, branch_name: str, force: bool = False) -> None:
        """Push branch to origin."""
        cmd = ["push", "-u", "origin", branch_name]
        if force:
            cmd.append("--force")
        self._run_git(repo_dir, cmd)

    @staticmethod
    def get_git_diff_summary(workspace_dir: Path) -> str:
        """Get summary of git changes in workspace."""
        try:
            res = subprocess.run(
                ["git", "diff", "--stat"],
                cwd=workspace_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            summary = res.stdout.strip()
            if not summary:
                res_status = subprocess.run(
                    ["git", "status", "--short"],
                    cwd=workspace_dir,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                summary = res_status.stdout.strip()
            return summary or "No changes detected"
        except Exception as e:
            logger.warning("Failed to get git diff summary in %s: %s", workspace_dir, e)
            return "Unable to determine git diff"

    def _run_git(self, cwd: Path, args: list[str]) -> str:
        cmd = ["git"] + args
        res = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise RuntimeError(f"Git command failed: {' '.join(cmd)}\nSTDERR: {res.stderr}\nSTDOUT: {res.stdout}")
        return res.stdout.strip()
