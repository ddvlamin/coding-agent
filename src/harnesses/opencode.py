"""OpenCode CLI harness implementation (DeepSeek and OpenAI-compatible models)."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from src.harnesses.base import AgentHarness, HarnessResult

logger = logging.getLogger(__name__)


class OpenCodeHarness(AgentHarness):
    """Executes coding tasks using the OpenCode CLI."""

    def __init__(
        self,
        opencode_path: str = "opencode",
        model: str | None = None,
        api_key: str | None = None,
        base_url: str = "https://api.deepseek.com",
    ) -> None:
        self.opencode_path = opencode_path
        self.model = model or "deepseek/deepseek-chat"
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self.base_url = base_url

    def _run_opencode(self, workspace_dir: Path, prompt: str, timeout: int) -> tuple[int, str]:
        cmd = [
            self.opencode_path,
            "run",
            prompt,
            "--auto",
        ]
        if self.model:
            cmd.extend(["--model", self.model])

        logger.info("Executing opencode in %s with model %s (timeout: %ds)", workspace_dir, self.model, timeout)
        env = os.environ.copy()
        if self.api_key:
            env["DEEPSEEK_API_KEY"] = self.api_key
            env["OPENAI_API_KEY"] = self.api_key
        if self.base_url:
            env["DEEPSEEK_BASE_URL"] = self.base_url
            env["OPENAI_BASE_URL"] = self.base_url

        try:
            res = subprocess.run(
                cmd,
                cwd=workspace_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            output = f"{res.stdout}\n{res.stderr}".strip()
            return res.returncode, output
        except subprocess.TimeoutExpired:
            return -1, f"OpenCode CLI timed out after {timeout} seconds"
        except Exception as e:
            return -1, f"Failed to execute OpenCode CLI: {e}"

    def execute(
        self,
        workspace_dir: Path,
        prompt: str,
        test_command: str | None = None,
        timeout: int = 1800,
    ) -> HarnessResult:
        full_prompt = (
            f"{prompt}\n\n"
            "Requirements:\n"
            "- Make minimal, surgical edits to solve the task.\n"
            "- Do not touch unrelated files or refactor working code.\n"
        )
        if test_command:
            full_prompt += f"- Run and satisfy project tests: `{test_command}`\n"

        code, output = self._run_opencode(workspace_dir, full_prompt, timeout)

        if code == -1 and "timed out" in output:
            return HarnessResult(
                success=False,
                status="timeout",
                diff_summary=self.get_git_diff_summary(workspace_dir),
                test_passed=False,
                output=output,
                error="Execution timed out",
            )

        test_passed = True
        test_output = ""
        if test_command:
            test_passed, test_output = self.run_tests(workspace_dir, test_command)
            if not test_passed:
                logger.warning("Initial tests failed with OpenCode. Attempting repair loop...")
                repair_prompt = (
                    f"Tests failed after edits. Failure output:\n\n"
                    f"{test_output}\n\n"
                    f"Please fix the code so `{test_command}` passes."
                )
                _, repair_output = self._run_opencode(workspace_dir, repair_prompt, min(600, timeout))
                output += f"\n\n--- Repair Output ---\n{repair_output}"
                test_passed, test_output = self.run_tests(workspace_dir, test_command)

        diff_summary = self.get_git_diff_summary(workspace_dir)
        success = (code == 0) and test_passed

        return HarnessResult(
            success=success,
            status="completed" if success else "failed",
            diff_summary=diff_summary,
            test_passed=test_passed,
            output=f"{output}\n\nTest Output:\n{test_output}".strip(),
            error=None if success else "Task failed or tests did not pass",
        )
