"""Unit tests for orchestrator CLI entrypoint."""

from unittest.mock import MagicMock, patch
import pytest

from src.config import AgentConfig, RepoConfig
from src.main import Orchestrator, parse_args


def test_parse_args():
    args1 = parse_args(["--all", "--dry-run"])
    assert args1.all is True
    assert args1.dry_run is True

    args2 = parse_args(["--repo", "org/repo-x", "--phase", "1", "3"])
    assert args2.repo == "org/repo-x"
    assert args2.phase == [1, 3]


def test_orchestrator_dry_run(rsa_key_pem):
    config = AgentConfig(
        repos=[RepoConfig(name="test-org/test-repo")],
        dry_run=True,
    )
    config.github.app_id = "12345"
    config.github.private_key = rsa_key_pem

    with patch("src.main.GitHubAppAuth") as mock_auth_cls, \
         patch("src.main.GitHubClient") as mock_gh_cls, \
         patch("src.main.GitWorkspace") as mock_git_cls, \
         patch("src.main.EmailDigestService") as mock_email_cls:

        orchestrator = Orchestrator(config)
        orchestrator._run_for_repo = MagicMock(return_value=MagicMock())

        summary = orchestrator.run()
        assert len(summary.repo_reports) == 1
        orchestrator._run_for_repo.assert_called_once()
