"""Base interface and data types for Agent Harnesses."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.sandbox.container import BaseSandbox, DockerSandbox


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
        sandbox: BaseSandbox | None = None,
    ) -> HarnessResult:
        """
        Executes an agent task inside the target workspace via the sandbox.
        Returns a HarnessResult containing status, diff summary, and test output.
        """
        pass
