"""Unit tests for BaseSandbox, DockerSandbox, LocalSandbox, and get_sandbox factory."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from src.config import SandboxConfig
from src.harnesses.base import AgentHarness, HarnessResult
from src.sandbox.container import BaseSandbox, DockerSandbox, LocalSandbox, get_sandbox
from src.sandbox.git_workspace import GitWorkspace


class DummyHarness(AgentHarness):
    def execute(
        self,
        workspace_dir: Path,
        prompt: str,
        test_command: str | None = None,
        timeout: int = 1800,
        sandbox: BaseSandbox | None = None,
    ) -> HarnessResult:
        return HarnessResult(
            success=True,
            status="completed",
            diff_summary="dummy diff",
            test_passed=True,
            output=f"executed prompt: {prompt}",
        )


def test_base_sandbox_is_abc():
    with pytest.raises(TypeError):
        BaseSandbox()


def test_base_sandbox_execute_lifecycle(tmp_path):
    class ConcreteSandbox(BaseSandbox):
        def run_command(self, workspace_dir, cmd, env=None, timeout=None):
            return 0, "ok"

    harness = DummyHarness()
    sandbox = ConcreteSandbox(harness=harness)
    res = sandbox.execute(tmp_path, "Fix bug", test_command="pytest")
    assert res.success is True
    assert "Fix bug" in res.output

    # execute with explicit harness
    sandbox_no_harness = ConcreteSandbox()
    res2 = sandbox_no_harness.execute(tmp_path, "Fix another bug", harness=harness)
    assert res2.success is True

    # execute_harness delegating to execute
    res3 = sandbox_no_harness.execute_harness(harness, tmp_path, "Fix third bug")
    assert res3.success is True

    # execute without harness raises ValueError
    with pytest.raises(ValueError, match="No harness provided"):
        sandbox_no_harness.execute(tmp_path, "No harness")


def test_local_sandbox_run_command(tmp_path):
    sandbox = LocalSandbox(deepseek_api_key="sk-test")

    code, out = sandbox.run_command(tmp_path, "echo 'hello world'")
    assert code == 0
    assert "hello world" in out


def test_local_sandbox_timeout(tmp_path):
    sandbox = LocalSandbox()

    code, out = sandbox.run_command(tmp_path, "sleep 2", timeout=1)
    assert code == -1
    assert "timed out" in out


def test_local_sandbox_run_tests(tmp_path):
    sandbox = LocalSandbox()

    passed, out = sandbox.run_tests(tmp_path, "echo 'tests ok'")
    assert passed is True
    assert "tests ok" in out

    passed_fail, _ = sandbox.run_tests(tmp_path, "exit 1")
    assert passed_fail is False


def test_docker_sandbox_daemon_unavailable(tmp_path):
    config = SandboxConfig(use_docker=True)
    sandbox = DockerSandbox(config)

    # Force docker unavailable
    sandbox._docker_client = None
    sandbox._checked_docker = True

    code, out = sandbox.run_command(tmp_path, "echo 'test'")
    assert code == -1
    assert "Docker daemon unavailable" in out


def test_docker_sandbox_run_command_mocked(tmp_path):
    config = SandboxConfig(use_docker=True, image="test-image:latest")
    sandbox = DockerSandbox(config, deepseek_api_key="sk-test")

    mock_client = MagicMock()
    mock_container = MagicMock()
    mock_container.wait.return_value = {"StatusCode": 0}
    mock_container.logs.return_value = b"container run output\n"
    mock_client.containers.create.return_value = mock_container

    sandbox._docker_client = mock_client
    sandbox._checked_docker = True

    code, out = sandbox.run_command(tmp_path, "python main.py")
    assert code == 0
    assert "container run output" in out

    mock_client.containers.create.assert_called_once()
    create_kwargs = mock_client.containers.create.call_args[1]
    assert create_kwargs["image"] == "test-image:latest"
    assert create_kwargs["environment"]["DEEPSEEK_API_KEY"] == "sk-test"
    mock_container.start.assert_called_once()
    mock_container.remove.assert_called_once_with(force=True)


def test_get_sandbox_returns_docker_when_available():
    config = SandboxConfig(use_docker=True)
    with patch.object(DockerSandbox, "is_docker_available", return_value=True):
        sandbox = get_sandbox(config)
        assert isinstance(sandbox, DockerSandbox)


def test_get_sandbox_falls_back_to_local_when_docker_unavailable():
    config = SandboxConfig(use_docker=True)
    with patch.object(DockerSandbox, "is_docker_available", return_value=False):
        sandbox = get_sandbox(config)
        assert isinstance(sandbox, LocalSandbox)


def test_get_sandbox_returns_local_when_docker_disabled():
    config = SandboxConfig(use_docker=False)
    sandbox = get_sandbox(config)
    assert isinstance(sandbox, LocalSandbox)


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


def test_git_workspace_diff_summary(tmp_path):
    import subprocess
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    assert GitWorkspace.get_git_diff_summary(tmp_path) == "No changes detected"

    (tmp_path / "hello.txt").write_text("world", encoding="utf-8")
    summary = GitWorkspace.get_git_diff_summary(tmp_path)
    assert "hello.txt" in summary
