"""GitHub App authentication and token management."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any
import httpx
import jwt

logger = logging.getLogger(__name__)


class GitHubAuthError(Exception):
    """Raised when GitHub authentication or API call fails."""
    pass


class GitHubAppAuth:
    """Manages GitHub App authentication, JWT generation, and installation tokens."""

    def __init__(
        self,
        app_id: int | str,
        private_key_pem: str,
        installation_id: int | str | None = None,
        base_url: str = "https://api.github.com",
    ) -> None:
        self.app_id = str(app_id).strip()
        self.private_key_pem = private_key_pem.strip()
        self.default_installation_id = str(installation_id).strip() if installation_id else None
        self.base_url = base_url.rstrip("/")
        # Cache tokens per installation_id: {installation_id: (token, expires_at_timestamp)}
        self._token_cache: dict[str, tuple[str, float]] = {}

    def create_jwt(self) -> str:
        """Create RS256 JWT valid for 10 minutes (with 60-second clock drift margin)."""
        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + (10 * 60) - 60,
            "iss": self.app_id,
        }
        try:
            return jwt.encode(payload, self.private_key_pem, algorithm="RS256")
        except Exception as e:
            raise GitHubAuthError(f"Failed to generate GitHub App JWT: {e}") from e

    def get_installation_id_for_repo(self, owner: str, repo: str) -> str:
        """Auto-discover installation ID for a specific repository if not configured."""
        jwt_token = self.create_jwt()
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        url = f"{self.base_url}/repos/{owner}/{repo}/installation"
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return str(data["id"])
            raise GitHubAuthError(
                f"Failed to resolve installation ID for {owner}/{repo}: {resp.status_code} {resp.text}"
            )

    def get_installation_token(self, owner_repo: str | None = None) -> str:
        """Get or renew installation token for target repository or default installation."""
        inst_id = self.default_installation_id
        if not inst_id and owner_repo:
            parts = owner_repo.split("/")
            if len(parts) == 2:
                inst_id = self.get_installation_id_for_repo(parts[0], parts[1])

        if not inst_id:
            raise GitHubAuthError("No installation_id configured and no owner/repo provided to resolve one")

        # Check cache (renew if less than 5 minutes remain)
        if inst_id in self._token_cache:
            token, exp = self._token_cache[inst_id]
            if time.time() < exp - 300:
                return token

        jwt_token = self.create_jwt()
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        url = f"{self.base_url}/app/installations/{inst_id}/access_tokens"
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, headers=headers)
            if resp.status_code not in (200, 201):
                raise GitHubAuthError(
                    f"Failed to create installation access token for {inst_id}: {resp.status_code} {resp.text}"
                )
            data = resp.json()
            token = data["token"]
            expires_at_str = data.get("expires_at")
            if expires_at_str:
                exp_dt = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                exp_ts = exp_dt.timestamp()
            else:
                exp_ts = time.time() + 3600

            self._token_cache[inst_id] = (token, exp_ts)
            logger.info("Generated new GitHub installation token for installation %s (expires: %s)", inst_id, expires_at_str)
            return token

    def get_auth_headers(self, owner_repo: str | None = None) -> dict[str, str]:
        """Headers for REST and GraphQL requests."""
        token = self.get_installation_token(owner_repo)
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def execute_graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        owner_repo: str | None = None,
        retries: int = 3,
    ) -> dict[str, Any]:
        """Execute GraphQL query or mutation with retries."""
        url = f"{self.base_url}/graphql"
        last_error = None

        for attempt in range(1, retries + 1):
            headers = self.get_auth_headers(owner_repo)
            try:
                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(url, json={"query": query, "variables": variables or {}}, headers=headers)
                    if resp.status_code != 200:
                        raise GitHubAuthError(f"GraphQL request failed ({resp.status_code}): {resp.text}")

                    payload = resp.json()
                    if "errors" in payload and payload["errors"]:
                        error_messages = "; ".join(err.get("message", str(err)) for err in payload["errors"])
                        raise GitHubAuthError(f"GraphQL returned errors: {error_messages}")
                    return payload.get("data", {})
            except Exception as e:
                last_error = e
                logger.warning("GraphQL attempt %d/%d failed: %s", attempt, retries, e)
                if attempt < retries:
                    time.sleep(attempt * 2)

        raise GitHubAuthError(f"GraphQL execution failed after {retries} retries: {last_error}") from last_error
