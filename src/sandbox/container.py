"""Sandbox execution environments (BaseSandbox, DockerSandbox, LocalSandbox)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from src.config import SandboxConfig
from src.harnesses.base import AgentHarness, HarnessResult

logger = logging.getLogger(__name__)


class BaseSandbox(ABC):
    """Abstract base class for sandbox execution environments."""

    def __init__(
        self,
        config: SandboxConfig | AgentHarness | None = None,
        harness: AgentHarness | None = None,
        deepseek_api_key: str | None = None,
    ) -> None:
        if isinstance(config, AgentHarness):
            self.harness = config
            self.config = SandboxConfig()
        else:
            self.config = config or SandboxConfig()
            self.harness = harness
        self.deepseek_api_key = deepseek_api_key or os.getenv("DEEPSEEK_API_KEY")

    @abstractmethod
    def run_command(
        self,
        workspace_dir: Path,
        cmd: list[str] | str,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> tuple[int, str]:
        """Run a command inside the sandbox."""
        pass

    def run_tests(self, workspace_dir: Path, test_command: str, timeout: int = 600) -> tuple[bool, str]:
        """Run repository test/lint command inside the sandbox."""
        logger.info("Running test command in %s: %s", workspace_dir, test_command)
        code, output = self.run_command(workspace_dir, test_command, timeout=timeout)
        return code == 0, output

    def execute(
        self,
        workspace_dir: Path | AgentHarness,
        prompt: str | Path,
        test_command: str | None = None,
        timeout: int = 1800,
        harness: AgentHarness | None = None,
    ) -> HarnessResult:
        """Run agent harness task lifecycle inside the sandbox."""
        if isinstance(workspace_dir, AgentHarness):
            actual_harness = workspace_dir
            actual_workspace_dir = prompt  # type: ignore[assignment]
            actual_prompt = test_command or ""
            actual_test_command = timeout if isinstance(timeout, str) else None
            actual_timeout = 1800 if isinstance(timeout, str) else timeout
            return actual_harness.execute(
                workspace_dir=actual_workspace_dir,
                prompt=actual_prompt,
                test_command=actual_test_command,
                timeout=actual_timeout,
                sandbox=self,
            )

        active_harness = harness or self.harness
        if active_harness is None:
            raise ValueError("No harness provided for sandbox execution")
        return active_harness.execute(
            workspace_dir=workspace_dir,
            prompt=prompt,  # type: ignore[arg-type]
            test_command=test_command,
            timeout=timeout,
            sandbox=self,
        )

    def execute_harness(
        self,
        harness: AgentHarness,
        workspace_dir: Path,
        prompt: str,
        test_command: str | None = None,
        timeout: int = 1800,
    ) -> HarnessResult:
        """Run agent harness inside the sandbox (delegates to execute)."""
        return self.execute(
            workspace_dir=workspace_dir,
            prompt=prompt,
            test_command=test_command,
            timeout=timeout,
            harness=harness,
        )


class LocalSandbox(BaseSandbox):
    """Executes commands directly on the local host via subprocess.run."""

    def run_command(
        self,
        workspace_dir: Path,
        cmd: list[str] | str,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> tuple[int, str]:
        """Run a command locally on the host."""
        timeout_sec = timeout or self.config.timeout_seconds
        return self._run_local(workspace_dir, cmd, env, timeout_sec)

    def _run_local(
        self,
        workspace_dir: Path,
        cmd: list[str] | str,
        env: dict[str, str] | None,
        timeout: int,
    ) -> tuple[int, str]:
        logger.info("Running local command in %s: %s", workspace_dir, cmd)
        full_env = os.environ.copy()
        if self.deepseek_api_key:
            full_env["DEEPSEEK_API_KEY"] = self.deepseek_api_key
            full_env["OPENAI_API_KEY"] = self.deepseek_api_key
        if env:
            full_env.update(env)

        try:
            is_shell = isinstance(cmd, str)
            res = subprocess.run(
                cmd,
                shell=is_shell,
                cwd=workspace_dir,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=full_env,
            )
            output = f"{res.stdout}\n{res.stderr}".strip()
            return res.returncode, output
        except subprocess.TimeoutExpired:
            return -1, f"Command timed out after {timeout} seconds"
        except Exception as e:
            return -1, f"Command execution failed: {e}"


class DockerSandbox(BaseSandbox):
    """Manages ephemeral Docker containers for sandboxed agent tasks."""

    def __init__(
        self,
        config: SandboxConfig | AgentHarness | None = None,
        harness: AgentHarness | None = None,
        deepseek_api_key: str | None = None,
    ) -> None:
        super().__init__(config=config, harness=harness, deepseek_api_key=deepseek_api_key)
        self._docker_client = None
        self._checked_docker = False

    def get_docker_client(self) -> Any:
        """Lazily initialize and return Docker client if available."""
        if self._checked_docker:
            return self._docker_client

        self._checked_docker = True
        if not self.config.use_docker:
            logger.info("Docker disabled via configuration (AGENT_USE_DOCKER=false).")
            return None

        try:
            import docker
            client = docker.from_env()
            client.ping()
            self._docker_client = client
            logger.info("Connected to Docker daemon successfully.")
            return self._docker_client
        except Exception as e:
            logger.warning("Docker daemon unavailable (%s).", e)
            self._docker_client = None
            return None

    def is_docker_available(self) -> bool:
        return self.get_docker_client() is not None

    def run_command(
        self,
        workspace_dir: Path,
        cmd: list[str] | str,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> tuple[int, str]:
        """Run a command inside the Docker sandbox."""
        timeout_sec = timeout or self.config.timeout_seconds
        client = self.get_docker_client()

        if not client:
            return -1, "Docker daemon unavailable or Docker is disabled"

        return self._run_docker(client, workspace_dir, cmd, env, timeout_sec)

    def _run_docker(
        self,
        client: Any,
        workspace_dir: Path,
        cmd: list[str] | str,
        env: dict[str, str] | None,
        timeout: int,
    ) -> tuple[int, str]:
        container_env = {
            "DEEPSEEK_API_KEY": self.deepseek_api_key or "",
            "OPENAI_API_KEY": self.deepseek_api_key or "",
            "WORKSPACE_DIR": "/workspace",
        }
        if env:
            container_env.update(env)

        volumes = {
            str(workspace_dir.resolve()): {
                "bind": "/workspace",
                "mode": "rw",
            }
        }
        host_oauth_token = Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
        if host_oauth_token.is_file():
            volumes[str(host_oauth_token.resolve())] = {
                "bind": "/home/runner/.gemini/antigravity-cli/antigravity-oauth-token",
                "mode": "rw",
            }

        command = cmd if isinstance(cmd, list) else ["/bin/bash", "-c", cmd]
        container = None
        try:
            logger.info("Starting ephemeral sandbox container with image: %s", self.config.image)
            nano_cpus = int(self.config.cpu_count * 1e9)
            container = client.containers.create(
                image=self.config.image,
                command=command,
                working_dir="/workspace",
                volumes=volumes,
                environment=container_env,
                mem_limit=self.config.memory_limit,
                nano_cpus=nano_cpus,
                user="1000:1000",
                detach=True,
            )
            container.start()
            result = container.wait(timeout=timeout)
            logs = container.logs().decode("utf-8", errors="replace")
            exit_code = result.get("StatusCode", -1)
            return exit_code, logs
        except Exception as e:
            logger.error("Docker container execution failed: %s", e)
            return -1, str(e)
        finally:
            if container:
                try:
                    container.remove(force=True)
                except Exception:
                    pass


def get_sandbox(
    config: SandboxConfig | None = None,
    harness: AgentHarness | None = None,
    deepseek_api_key: str | None = None,
    **kwargs: Any,
) -> BaseSandbox:
    """Factory to return DockerSandbox when Docker is enabled and reachable, otherwise LocalSandbox."""
    cfg = config or SandboxConfig()
    api_key = deepseek_api_key or os.getenv("DEEPSEEK_API_KEY")

    if cfg.use_docker:
        docker_sandbox = DockerSandbox(config=cfg, harness=harness, deepseek_api_key=api_key, **kwargs)
        if docker_sandbox.is_docker_available():
            return docker_sandbox
        logger.warning("Docker daemon unavailable. Falling back to LocalSandbox.")

    return LocalSandbox(config=cfg, harness=harness, deepseek_api_key=api_key, **kwargs)
