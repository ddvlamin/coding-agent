"""Unit tests for GitHub App authentication and tokens."""

import time
import httpx
import jwt
import pytest

from src.github.auth import GitHubAppAuth, GitHubAuthError


def test_jwt_generation(rsa_key_pem):
    auth = GitHubAppAuth(app_id="12345", private_key_pem=rsa_key_pem)
    token = auth.create_jwt()
    assert isinstance(token, str)

    # Decode and verify header & claims
    decoded = jwt.decode(token, options={"verify_signature": False})
    assert decoded["iss"] == "12345"
    assert "iat" in decoded
    assert "exp" in decoded
    assert decoded["exp"] > decoded["iat"]


def test_installation_token_caching(rsa_key_pem, monkeypatch):
    auth = GitHubAppAuth(app_id="12345", private_key_pem=rsa_key_pem, installation_id="67890")

    call_count = 0

    def mock_post(url, headers, json=None):
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            status_code=201,
            json={"token": f"ghs_mock_token_{call_count}", "expires_at": "2099-01-01T00:00:00Z"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", lambda self, url, headers=None, json=None: mock_post(url, headers, json))

    # First call: fetches new token
    t1 = auth.get_installation_token()
    assert t1 == "ghs_mock_token_1"
    assert call_count == 1

    # Second call: served from cache
    t2 = auth.get_installation_token()
    assert t2 == "ghs_mock_token_1"
    assert call_count == 1


def test_graphql_execution(rsa_key_pem, monkeypatch):
    auth = GitHubAppAuth(app_id="12345", private_key_pem=rsa_key_pem, installation_id="67890")
    auth._token_cache["67890"] = ("ghs_dummy", time.time() + 3600)

    def mock_post(url, headers=None, json=None):
        assert "Authorization" in headers
        return httpx.Response(
            status_code=200,
            json={"data": {"viewer": {"login": "test-bot"}}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", lambda self, url, headers=None, json=None: mock_post(url, headers, json))

    data = auth.execute_graphql("query { viewer { login } }")
    assert data["viewer"]["login"] == "test-bot"


def test_graphql_error_handling(rsa_key_pem, monkeypatch):
    auth = GitHubAppAuth(app_id="12345", private_key_pem=rsa_key_pem, installation_id="67890")
    auth._token_cache["67890"] = ("ghs_dummy", time.time() + 3600)

    def mock_post(url, headers=None, json=None):
        return httpx.Response(
            status_code=200,
            json={"errors": [{"message": "Field not found"}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.Client, "post", lambda self, url, headers=None, json=None: mock_post(url, headers, json))

    with pytest.raises(GitHubAuthError) as exc_info:
        auth.execute_graphql("query { badField }", retries=1)
    assert "Field not found" in str(exc_info.value)
