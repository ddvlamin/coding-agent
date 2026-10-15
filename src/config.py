"""Configuration models and loader for Autonomous GitHub Maintenance Agent."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal
from dotenv import load_dotenv
from pydantic import BaseModel, Field
import yaml

load_dotenv()


class RepoConfig(BaseModel):
    name: str  # Format: "owner/repo"
    default_branch: str = "main"
    test_command: str | None = None
    lint_command: str | None = None
    harness: Literal["antigravity", "opencode"] = "antigravity"
    harness_model: str | None = None
    max_issues_per_run: int = Field(default=2, ge=1, le=10)
    ideation_count: int = Field(default=2, ge=1, le=5)
    enabled_phases: list[int] = Field(default_factory=lambda: [1, 2, 3, 4])

    @property
    def owner(self) -> str:
        return self.name.split("/")[0]

    @property
    def repo_name(self) -> str:
        return self.name.split("/")[1]


class GitHubConfig(BaseModel):
    app_id: int | str = Field(default_factory=lambda: os.getenv("GITHUB_APP_ID", ""))
    private_key_path: str | None = Field(
        default_factory=lambda: os.getenv("GITHUB_APP_PRIVATE_KEY_PATH")
    )
    private_key: str | None = Field(
        default_factory=lambda: os.getenv("GITHUB_APP_PRIVATE_KEY")
    )
    installation_id: int | str | None = Field(
        default_factory=lambda: os.getenv("GITHUB_APP_INSTALLATION_ID")
    )
    bot_username: str = Field(
        default_factory=lambda: os.getenv("GITHUB_BOT_USERNAME", "app[bot]")
    )

    def get_private_key_pem(self) -> str:
        if self.private_key:
            return self.private_key
        if self.private_key_path:
            path = Path(self.private_key_path).expanduser().resolve()
            if path.exists():
                return path.read_text(encoding="utf-8")
            raise FileNotFoundError(f"Private key file not found at: {self.private_key_path}")
        raise ValueError("Neither GITHUB_APP_PRIVATE_KEY nor GITHUB_APP_PRIVATE_KEY_PATH is configured")


class SMTPConfig(BaseModel):
    host: str = Field(default_factory=lambda: os.getenv("SMTP_HOST", "smtp.gmail.com"))
    port: int = Field(default_factory=lambda: int(os.getenv("SMTP_PORT", "587")))
    user: str = Field(default_factory=lambda: os.getenv("SMTP_USER", ""))
    password: str = Field(default_factory=lambda: os.getenv("SMTP_PASSWORD", ""))
    from_email: str | None = Field(default_factory=lambda: os.getenv("SMTP_FROM_EMAIL"))
    to_emails: list[str] = Field(default_factory=lambda: [
        e.strip() for e in os.getenv("SMTP_TO_EMAILS", "").split(",") if e.strip()
    ])
    enabled: bool = Field(default_factory=lambda: os.getenv("SMTP_ENABLED", "true").lower() in ("true", "1", "yes"))


class HarnessConfig(BaseModel):
    default_harness: Literal["antigravity", "opencode"] = "antigravity"
    timeout_seconds: int = 1800
    deepseek_api_key: str | None = Field(
        default_factory=lambda: os.getenv("DEEPSEEK_API_KEY")
    )
    deepseek_base_url: str = "https://api.deepseek.com"
    agy_path: str = "agy"
    opencode_path: str = "opencode"


class SandboxConfig(BaseModel):
    use_docker: bool = Field(
        default_factory=lambda: os.getenv("AGENT_USE_DOCKER", "true").lower() in ("true", "1", "yes")
    )
    image: str = "repo-agent-sandbox:latest"
    memory_limit: str = "4g"
    cpu_count: float = 2.0
    timeout_seconds: int = 1800


class AgentConfig(BaseModel):
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    smtp: SMTPConfig = Field(default_factory=SMTPConfig)
    harness: HarnessConfig = Field(default_factory=HarnessConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    repos: list[RepoConfig] = Field(default_factory=list)
    dry_run: bool = False
    workdir: str = "/tmp/repo-agent-workspace"


def load_repo_override(workspace_dir: Path, base_repo_config: RepoConfig) -> RepoConfig:
    """Check for .github/agent.yaml in repository and apply overrides."""
    override_file = workspace_dir / ".github" / "agent.yaml"
    if not override_file.exists():
        override_file = workspace_dir / ".github" / "agent.yml"
    if not override_file.exists():
        return base_repo_config

    try:
        content = yaml.safe_load(override_file.read_text(encoding="utf-8")) or {}
        repo_data = base_repo_config.model_dump()
        for key in ["default_branch", "test_command", "lint_command", "harness", "harness_model", "max_issues_per_run", "ideation_count", "enabled_phases"]:
            if key in content and content[key] is not None:
                repo_data[key] = content[key]
        return RepoConfig(**repo_data)
    except Exception:
        return base_repo_config


def load_config(config_path: str | Path | None = None) -> AgentConfig:
    """Load configuration from YAML file and environment variables."""
    if config_path is None:
        env_config = os.getenv("REPO_AGENT_CONFIG")
        if env_config:
            config_path = Path(env_config)
        else:
            candidates = [Path("config.yaml"), Path("config.yml"), Path("/etc/repo-agent/config.yaml")]
            for c in candidates:
                if c.exists():
                    config_path = c
                    break

    data: dict[str, Any] = {}
    if config_path and Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    return AgentConfig(**data)
