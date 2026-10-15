"""Agent harness registry and factory."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from src.harnesses.base import AgentHarness, HarnessResult
from src.harnesses.antigravity import AntigravityHarness
from src.harnesses.opencode import OpenCodeHarness

if TYPE_CHECKING:
    from src.config import AgentConfig, RepoConfig


def get_harness(repo_config: Any, agent_config: Any) -> AgentHarness:
    """Factory to return appropriate AgentHarness for a repository."""
    harness_type = repo_config.harness or agent_config.harness.default_harness

    if harness_type == "opencode":
        return OpenCodeHarness(
            opencode_path=agent_config.harness.opencode_path,
            model=repo_config.harness_model,
            api_key=agent_config.harness.deepseek_api_key,
            base_url=agent_config.harness.deepseek_base_url,
        )
    return AntigravityHarness(
        agy_path=agent_config.harness.agy_path,
        model=repo_config.harness_model,
    )


__all__ = [
    "AgentHarness",
    "HarnessResult",
    "AntigravityHarness",
    "OpenCodeHarness",
    "get_harness",
]
