"""Unit tests for Antigravity and OpenCode harnesses."""

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from src.config import AgentConfig, RepoConfig, SandboxConfig
from src.harnesses import AntigravityHarness, OpenCodeHarness, get_harness
from src.sandbox.container import DockerSandbox
from src.sandbox.git_workspace import GitWorkspace


def test_harness_factory():
    agent_cfg = AgentConfig()
    repo_cfg_agy = RepoConfig(name="org/repo1", harness="antigravity")
    repo_cfg_oc = RepoConfig(name="org/repo2", harness="opencode", harness_model="deepseek/deepseek-chat")

    h1 = get_harness(repo_cfg_agy, agent_cfg)
    assert isinstance(h1, AntigravityHarness)

    h2 = get_harness(repo_cfg_oc, agent_cfg)
    assert isinstance(h2, OpenCodeHarness)
    assert h2.model == "deepseek/deepseek-chat"


def test_antigravity_harness_execution(tmp_path, monkeypatch):
    harness = AntigravityHarness(agy_path="agy")
    sandbox = DockerSandbox(SandboxConfig(use_docker=False))

    calls = []

    def mock_run_command(ws, cmd, env=None, timeout=None):
        calls.append(cmd)
        if isinstance(cmd, list) and cmd[0] == "agy":
            return (0, "Successfully executed")
        return (0, "All 5 tests passed")

    monkeypatch.setattr(sandbox, "run_command", mock_run_command)
    monkeypatch.setattr(GitWorkspace, "get_git_diff_summary", staticmethod(lambda ws: "src/app.py | 2 +-"))

    res = sandbox.execute_harness(harness, tmp_path, "Fix bug", test_command="pytest")
    assert res.success is True
    assert res.status == "completed"
    assert res.test_passed is True
    assert "src/app.py" in res.diff_summary
    assert len(calls) == 2
    assert calls[0][0] == "agy"
    assert calls[1] == "pytest"


def test_antigravity_harness_repair_loop(tmp_path, monkeypatch):
    harness = AntigravityHarness(agy_path="agy")
    sandbox = DockerSandbox(SandboxConfig(use_docker=False))

    agy_runs = 0
    test_outputs = [
        (1, "AssertionError: expected 1 got 2"),
        (0, "All tests passed"),
    ]

    def mock_run_command(ws, cmd, env=None, timeout=None):
        nonlocal agy_runs
        if isinstance(cmd, list) and cmd[0] == "agy":
            agy_runs += 1
            return (0, f"Agy run {agy_runs}")
        return test_outputs.pop(0)

    monkeypatch.setattr(sandbox, "run_command", mock_run_command)
    monkeypatch.setattr(GitWorkspace, "get_git_diff_summary", staticmethod(lambda ws: "src/app.py | 5 +++--"))

    res = sandbox.execute_harness(harness, tmp_path, "Fix bug", test_command="pytest")
    assert res.success is True
    assert agy_runs == 2  # initial + 1 repair run
    assert res.test_passed is True


def test_opencode_harness_execution(tmp_path, monkeypatch):
    harness = OpenCodeHarness(api_key="sk-test-key", model="deepseek/deepseek-chat")
    sandbox = DockerSandbox(SandboxConfig(use_docker=False))

    calls = []

    def mock_run_command(ws, cmd, env=None, timeout=None):
        calls.append((cmd, env))
        if isinstance(cmd, list) and cmd[0] == "opencode":
            return (0, "OpenCode completed")
        return (0, "Tests passed")

    monkeypatch.setattr(sandbox, "run_command", mock_run_command)
    monkeypatch.setattr(GitWorkspace, "get_git_diff_summary", staticmethod(lambda ws: "lib/math.py | 1 +"))

    res = sandbox.execute_harness(harness, tmp_path, "Add function", test_command="npm test")
    assert res.success is True
    assert res.test_passed is True
    assert len(calls) == 2
    assert calls[0][0][0] == "opencode"
    assert calls[0][1]["DEEPSEEK_API_KEY"] == "sk-test-key"
    assert calls[1][0] == "npm test"
