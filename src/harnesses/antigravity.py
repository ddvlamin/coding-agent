"""Antigravity (agy) CLI harness implementation."""

from __future__ import annotations

import logging
from pathlib import Path

from src.config import SandboxConfig
from src.harnesses.base import AgentHarness, HarnessResult
from src.sandbox.container import DockerSandbox
from src.sandbox.git_workspace import GitWorkspace

logger = logging.getLogger(__name__)


class AntigravityHarness(AgentHarness):
    """Executes coding tasks using the Antigravity CLI (agy)."""

    def __init__(
        self,
        agy_path: str = "agy",
        model: str | None = None,
        effort: str | None = None,
    ) -> None:
        self.agy_path = agy_path
        self.model = model
        self.effort = effort

    def _run_agy(
        self,
        workspace_dir: Path,
        prompt: str,
        timeout: int,
        sandbox: DockerSandbox,
    ) -> tuple[int, str]:
        cmd = [
            self.agy_path,
            "--dangerously-skip-permissions",
            "--print",
            prompt,
        ]
        if self.model:
            cmd.extend(["--model", self.model])
        if self.effort:
            cmd.extend(["--effort", self.effort])

        logger.info("Executing agy in %s (timeout: %ds)", workspace_dir, timeout)
        return sandbox.run_command(workspace_dir, cmd, timeout=timeout)

    def execute(
        self,
        workspace_dir: Path,
        prompt: str,
        test_command: str | None = None,
        timeout: int = 1800,
        sandbox: DockerSandbox | None = None,
    ) -> HarnessResult:
        active_sandbox = sandbox or DockerSandbox(SandboxConfig(use_docker=False))
        full_prompt = (
            f"{prompt}\n\n"
            "Requirements:\n"
            "- Make minimal, surgical edits to solve the task.\n"
            "- Do not add extraneous dependencies or unnecessary abstractions.\n"
        )
        if test_command:
            full_prompt += f"- Ensure the project tests pass: `{test_command}`\n"

        code, output = self._run_agy(workspace_dir, full_prompt, timeout, active_sandbox)

        if code == -1 and "timed out" in output.lower():
            return HarnessResult(
                success=False,
                status="timeout",
                diff_summary=GitWorkspace.get_git_diff_summary(workspace_dir),
                test_passed=False,
                output=output,
                error="Execution timed out",
            )

        test_passed = True
        test_output = ""
        if test_command:
            test_passed, test_output = active_sandbox.run_tests(workspace_dir, test_command)
            if not test_passed:
                logger.warning("Initial tests failed. Triggering 1 repair loop with agy...")
                repair_prompt = (
                    f"Tests failed after edits. Here is the failure output:\n\n"
                    f"{test_output}\n\n"
                    f"Please fix the code so `{test_command}` passes."
                )
                _, repair_output = self._run_agy(workspace_dir, repair_prompt, min(600, timeout), active_sandbox)
                output += f"\n\n--- Repair Output ---\n{repair_output}"
                test_passed, test_output = active_sandbox.run_tests(workspace_dir, test_command)

        diff_summary = GitWorkspace.get_git_diff_summary(workspace_dir)
        success = (code == 0) and test_passed

        return HarnessResult(
            success=success,
            status="completed" if success else "failed",
            diff_summary=diff_summary,
            test_passed=test_passed,
            output=f"{output}\n\nTest Output:\n{test_output}".strip(),
            error=None if success else "Task failed or tests did not pass",
        )
