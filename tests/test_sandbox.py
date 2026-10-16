"""Unit tests for Docker sandbox and local runner fallback."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from src.config import SandboxConfig
from src.sandbox.container import DockerSandbox
from src.sandbox.git_workspace import GitWorkspace


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


def test_git_workspace_uses_bot_username(tmp_path):
    mock_auth = MagicMock()
    mock_auth.get_installation_token.return_value = "tok-123"
    ws = GitWorkspace(
        tmp_path,
        mock_auth,
        bot_username="ddvlamin-coding-agent[bot]",
        bot_email="343186873+ddvlamin-coding-agent[bot]@users.noreply.github.com",
    )

    with patch.object(ws, "_run_git") as mock_run_git:
        repo_dir = ws.prepare_repo("org", "repo")
        assert repo_dir == tmp_path / "org_repo"
        mock_run_git.assert_any_call(repo_dir, ["config", "user.name", "ddvlamin-coding-agent[bot]"])
        mock_run_git.assert_any_call(
            repo_dir,
            ["config", "user.email", "343186873+ddvlamin-coding-agent[bot]@users.noreply.github.com"],
        )
