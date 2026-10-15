"""Base interface and data types for Agent Harnesses."""

from __future__ import annotations

import logging
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class HarnessResult:
    """Result of an agent harness execution."""
    success: bool
    status: str  # "completed", "failed", "timeout"
    diff_summary: str
    test_passed: bool
    output: str
    error: str | None = None


class AgentHarness(ABC):
    """Abstract base class for coding agent harnesses (agy, opencode, etc.)."""

    @abstractmethod
    def execute(
        self,
        workspace_dir: Path,
        prompt: str,
        test_command: str | None = None,
        timeout: int = 1800,
    ) -> HarnessResult:
        """
        Executes an agent task inside the target workspace.
        Returns a HarnessResult containing status, diff summary, and test output.
        """
        pass

    def get_git_diff_summary(self, workspace_dir: Path) -> str:
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
                # Check untracked files
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

    def run_tests(self, workspace_dir: Path, test_command: str, timeout: int = 600) -> tuple[bool, str]:
        """Run repository test/lint command in workspace directory."""
        logger.info("Running test command in %s: %s", workspace_dir, test_command)
        try:
            res = subprocess.run(
                test_command,
                shell=True,
                cwd=workspace_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}".strip()
            passed = (res.returncode == 0)
            return passed, output
        except subprocess.TimeoutExpired:
            return False, f"Test command timed out after {timeout} seconds"
        except Exception as e:
            return False, f"Test execution failed: {e}"
