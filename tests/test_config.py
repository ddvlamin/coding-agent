"""Unit tests for configuration loading and validation."""

import os
from pathlib import Path
import pytest
import yaml

from src.config import AgentConfig, RepoConfig, load_config, load_repo_override


def test_repo_config_properties():
    cfg = RepoConfig(name="owner/repo-name", default_branch="develop")
    assert cfg.owner == "owner"
    assert cfg.repo_name == "repo-name"
    assert cfg.default_branch == "develop"
    assert cfg.max_issues_per_run == 2
    assert cfg.ideation_count == 2
    assert cfg.enabled_phases == [1, 2, 3, 4]


def test_load_repo_override(tmp_path):
    base_cfg = RepoConfig(name="acme/service", test_command="pytest", max_issues_per_run=2)
    # No override file
    res1 = load_repo_override(tmp_path, base_cfg)
    assert res1.test_command == "pytest"
    assert res1.max_issues_per_run == 2

    # With .github/agent.yaml override
    gh_dir = tmp_path / ".github"
    gh_dir.mkdir(parents=True)
    override_file = gh_dir / "agent.yaml"
    override_file.write_text(
        yaml.dump({
            "test_command": "pytest --strict",
            "max_issues_per_run": 5,
            "harness": "opencode",
        }),
        encoding="utf-8",
    )

    res2 = load_repo_override(tmp_path, base_cfg)
    assert res2.test_command == "pytest --strict"
    assert res2.max_issues_per_run == 5
    assert res2.harness == "opencode"


def test_load_config_from_yaml(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        yaml.dump({
            "github": {
                "app_id": 9999,
                "private_key": "dummy-key",
            },
            "repos": [
                {"name": "org/repo-a", "test_command": "make test"},
            ],
            "dry_run": True,
        }),
        encoding="utf-8",
    )

    cfg = load_config(config_file)
    assert str(cfg.github.app_id) == "9999"
    assert cfg.github.private_key == "dummy-key"
    assert len(cfg.repos) == 1
    assert cfg.repos[0].name == "org/repo-a"
    assert cfg.repos[0].test_command == "make test"
    assert cfg.dry_run is True
