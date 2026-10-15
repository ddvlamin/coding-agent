"""Unit tests for Docker sandbox and local runner fallback."""

from pathlib import Path
import pytest

from src.config import SandboxConfig
from src.sandbox.container import DockerSandbox


def test_sandbox_local_fallback(tmp_path):
    config = SandboxConfig(use_docker=False)
    sandbox = DockerSandbox(config, deepseek_api_key="sk-test")

    code, out = sandbox.run_command(tmp_path, "echo 'hello world'")
    assert code == 0
    assert "hello world" in out


def test_sandbox_local_timeout(tmp_path):
    config = SandboxConfig(use_docker=False)
    sandbox = DockerSandbox(config)

    code, out = sandbox.run_command(tmp_path, "sleep 2", timeout=1)
    assert code == -1
    assert "timed out" in out
