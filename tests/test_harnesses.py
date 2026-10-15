"""Unit tests for Antigravity and OpenCode harnesses."""

import subprocess
from pathlib import Path
import pytest

from src.config import AgentConfig, RepoConfig
from src.harnesses import AntigravityHarness, OpenCodeHarness, get_harness


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

    # Mock _run_agy
    monkeypatch.setattr(harness, "_run_agy", lambda ws, prompt, timeout: (0, "Successfully executed"))
    # Mock test command
    monkeypatch.setattr(harness, "run_tests", lambda ws, cmd: (True, "All 5 tests passed"))
    monkeypatch.setattr(harness, "get_git_diff_summary", lambda ws: "src/app.py | 2 +-")

    res = harness.execute(tmp_path, "Fix bug", test_command="pytest")
    assert res.success is True
    assert res.status == "completed"
    assert res.test_passed is True
    assert "src/app.py" in res.diff_summary


def test_antigravity_harness_repair_loop(tmp_path, monkeypatch):
    harness = AntigravityHarness(agy_path="agy")

    run_count = 0
    test_runs = [
        (False, "AssertionError: expected 1 got 2"),
        (True, "All tests passed"),
    ]

    def mock_run_agy(ws, prompt, timeout):
        nonlocal run_count
        run_count += 1
        return (0, f"Agy run {run_count}")

    def mock_run_tests(ws, cmd):
        return test_runs.pop(0)

    monkeypatch.setattr(harness, "_run_agy", mock_run_agy)
    monkeypatch.setattr(harness, "run_tests", mock_run_tests)
    monkeypatch.setattr(harness, "get_git_diff_summary", lambda ws: "src/app.py | 5 +++--")

    res = harness.execute(tmp_path, "Fix bug", test_command="pytest")
    assert res.success is True
    assert run_count == 2  # initial + 1 repair run
    assert res.test_passed is True


def test_opencode_harness_execution(tmp_path, monkeypatch):
    harness = OpenCodeHarness(api_key="sk-test-key", model="deepseek/deepseek-chat")

    monkeypatch.setattr(harness, "_run_opencode", lambda ws, prompt, timeout: (0, "OpenCode completed"))
    monkeypatch.setattr(harness, "run_tests", lambda ws, cmd: (True, "Tests passed"))
    monkeypatch.setattr(harness, "get_git_diff_summary", lambda ws: "lib/math.py | 1 +")

    res = harness.execute(tmp_path, "Add function", test_command="npm test")
    assert res.success is True
    assert res.test_passed is True
