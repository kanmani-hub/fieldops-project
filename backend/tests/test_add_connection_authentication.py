"""
test_add_connection_authentication.py

Automated test suite for the story:
Add connection authentication as an integrated part of the
WebSocket Real-Time Events capability.

Expected Output (JSON):
{
    "status": "AUTHENTICATED",
    "tenant_source": "VERIFIED_TOKEN_CLAIMS"
}
"""

import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi.testclient import TestClient

from app.services.websocket_auth_service import (
    DEFAULT_WEBSOCKET_AUTH_CONFIG,
    JWT_ALGORITHM,
    AuthenticationError,
    WebSocketAuthService,
    validate_connection_authentication,
    get_websocket_auth_config,
    get_websocket_auth_status,
    AddConnectionAuthentication,
    Add_connection_authentication,
    ValidateConnectionAuthentication,
    Validate_connection_authentication,
    get_connection_auth_status,
    get_connection_auth_config,
    _get_jwt_secret,
)


def _generate_test_token(
    user_id: str = "user-test-1",
    tenant_id: str = "tenant-alpha",
    role: str = "dispatcher",
    expires_in_seconds: int = 3600,
    secret: str = None,
) -> str:
    """Helper to generate signed JWT for connection authentication testing."""
    secret = secret or _get_jwt_secret()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "tenant_id": tenant_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in_seconds)).timestamp()),
        "type": "access",
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


class TestWebSocketAuthContract:
    """Verifies canonical JSON contract and configuration."""

    def test_canonical_contract_defaults(self):
        result = validate_connection_authentication()
        assert isinstance(result, dict)
        assert result.get("status") == "AUTHENTICATED"
        assert result.get("tenant_source") == "VERIFIED_TOKEN_CLAIMS"

    def test_default_config_constants(self):
        assert DEFAULT_WEBSOCKET_AUTH_CONFIG["status"] == "AUTHENTICATED"
        assert DEFAULT_WEBSOCKET_AUTH_CONFIG["tenant_source"] == "VERIFIED_TOKEN_CLAIMS"

    def test_get_websocket_auth_config(self):
        config = get_websocket_auth_config()
        assert config["status"] == "AUTHENTICATED"
        assert config["tenant_source"] == "VERIFIED_TOKEN_CLAIMS"
        assert config["algorithm"] == "HS256"
        assert "sub" in config["required_claims"]
        assert "tenant_id" in config["required_claims"]
        assert "role" in config["required_claims"]

    def test_get_websocket_auth_status(self):
        status = get_websocket_auth_status()
        assert status == DEFAULT_WEBSOCKET_AUTH_CONFIG


class TestVerifiedTokenClaims:
    """Verifies JWT token verification and tenant identity isolation."""

    def test_valid_token_claims_extraction(self):
        token = _generate_test_token(
            user_id="usr-999",
            tenant_id="tenant-corp",
            role="dispatcher",
        )
        result = validate_connection_authentication(token=token)
        assert result["status"] == "AUTHENTICATED"
        assert result["tenant_source"] == "VERIFIED_TOKEN_CLAIMS"
        assert result["tenant_id"] == "tenant-corp"
        assert result["user_id"] == "usr-999"
        assert result["role"] == "dispatcher"
        assert result["authenticated"] is True

    def test_token_with_bearer_prefix(self):
        token = _generate_test_token(user_id="usr-888", tenant_id="tenant-beta")
        result = validate_connection_authentication(token=f"Bearer {token}")
        assert result["status"] == "AUTHENTICATED"
        assert result["tenant_source"] == "VERIFIED_TOKEN_CLAIMS"
        assert result["tenant_id"] == "tenant-beta"

    def test_forged_tenant_mismatch_overridden_with_claims(self):
        """Disallow client to forge tenant_id different from verified token claims."""
        token = _generate_test_token(tenant_id="tenant-legitimate")
        result = validate_connection_authentication(
            token=token,
            tenant_id="tenant-attacker-spoofed",
        )
        assert result["status"] == "AUTHENTICATED"
        assert result["tenant_source"] == "VERIFIED_TOKEN_CLAIMS"
        assert result["tenant_id"] == "tenant-legitimate"

    def test_expired_token_rejected(self):
        expired_token = _generate_test_token(expires_in_seconds=-100)
        result = validate_connection_authentication(token=expired_token)
        assert result["status"] == "UNAUTHENTICATED"
        assert result["authenticated"] is False
        assert "expired" in result.get("error", "").lower()

    def test_invalid_signature_rejected(self):
        bad_token = _generate_test_token(secret="wrong-secret-key-signature")
        result = validate_connection_authentication(token=bad_token)
        assert result["status"] == "UNAUTHENTICATED"
        assert result["authenticated"] is False

    def test_service_decode_token_raises_authentication_error(self):
        service = WebSocketAuthService()
        with pytest.raises(AuthenticationError):
            service.decode_token("")

        expired_token = _generate_test_token(expires_in_seconds=-50)
        with pytest.raises(AuthenticationError):
            service.decode_token(expired_token)


class TestFallbackAndBoundaryConditions:
    """Verifies fallback behavior and boundary conditions."""

    def test_unauthenticated_fallback_when_allowed(self):
        result = validate_connection_authentication(
            token=None,
            tenant_id="tenant-dev-mode",
            allow_fallback=True,
        )
        assert result["status"] == "AUTHENTICATED"
        assert result["tenant_source"] == "QUERY_PARAM_FALLBACK"
        assert result["tenant_id"] == "tenant-dev-mode"

    def test_unauthenticated_rejection_when_fallback_disabled(self):
        service = WebSocketAuthService()
        with pytest.raises(AuthenticationError):
            service.validate_connection(
                token=None,
                tenant_id="tenant-dev",
                allow_fallback=False,
            )


class TestStoryAliasesAndExports:
    """Verifies naming convention aliases across services and re-exports."""

    def test_story_aliases(self):
        assert AddConnectionAuthentication is validate_connection_authentication
        assert Add_connection_authentication is validate_connection_authentication
        assert ValidateConnectionAuthentication is validate_connection_authentication
        assert Validate_connection_authentication is validate_connection_authentication
        assert get_connection_auth_status is get_websocket_auth_status
        assert get_connection_auth_config is get_websocket_auth_config

    def test_redis_client_re_exports(self):
        import app.redis_client as rc
        assert hasattr(rc, "DEFAULT_WEBSOCKET_AUTH_CONFIG")
        assert hasattr(rc, "validate_connection_authentication")
        assert hasattr(rc, "get_websocket_auth_status")
        assert hasattr(rc, "get_websocket_auth_config")
        assert hasattr(rc, "AddConnectionAuthentication")

    def test_celery_app_re_exports(self):
        import app.celery_app as ca
        assert hasattr(ca, "DEFAULT_WEBSOCKET_AUTH_CONFIG")
        assert hasattr(ca, "validate_connection_authentication")
        assert hasattr(ca, "get_websocket_auth_status")
        assert hasattr(ca, "get_websocket_auth_config")

    def test_event_publisher_re_exports(self):
        import app.services.event_publisher as ep
        assert hasattr(ep, "DEFAULT_WEBSOCKET_AUTH_CONFIG")
        assert hasattr(ep, "validate_connection_authentication")
        assert hasattr(ep, "get_websocket_auth_status")
        assert hasattr(ep, "get_websocket_auth_config")

    def test_tasks_re_exports_and_aliases(self):
        import app.tasks as tasks
        assert hasattr(tasks, "validate_connection_authentication_task")
        assert hasattr(tasks, "ValidateConnectionAuthenticationTask")
        assert hasattr(tasks, "Validate_connection_authentication_task")
        assert hasattr(tasks, "AddConnectionAuthenticationTask")
        assert hasattr(tasks, "Add_connection_authentication_task")


class TestCeleryTaskExecution:
    """Verifies background Celery task execution."""

    def test_celery_task_direct_run(self):
        from app.tasks import validate_connection_authentication_task

        token = _generate_test_token(user_id="u-celery", tenant_id="tenant-celery")
        res = validate_connection_authentication_task(token=token)
        assert res["status"] == "AUTHENTICATED"
        assert res["tenant_source"] == "VERIFIED_TOKEN_CLAIMS"
        assert res["tenant_id"] == "tenant-celery"


class TestAdminQueueRestEndpoints:
    """Verifies REST endpoints for connection authentication."""

    @pytest.fixture
    def test_client(self):
        from app.main import app
        from app.auth.dependencies import get_current_user, require_permission
        from app.auth.rbac import Permission

        # Mock current user
        mock_user = MagicMock()
        mock_user.tenant_id = "tenant-1"
        mock_user.role = "admin"
        mock_user.email = "admin@fieldops.com"

        app.dependency_overrides[get_current_user] = lambda: mock_user
        app.dependency_overrides[require_permission(Permission.DISPATCH_QUEUE_VIEW)] = lambda: True

        yield TestClient(app)

        app.dependency_overrides.clear()

    def test_get_websocket_auth_status_endpoint(self, test_client):
        response = test_client.get("/admin/queue/websocket/auth/status")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "AUTHENTICATED"
        assert data.get("tenant_source") == "VERIFIED_TOKEN_CLAIMS"

    def test_get_websocket_auth_config_endpoint(self, test_client):
        response = test_client.get("/admin/queue/websocket/auth/config")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "AUTHENTICATED"
        assert data.get("tenant_source") == "VERIFIED_TOKEN_CLAIMS"
        assert data.get("algorithm") == "HS256"

    def test_post_websocket_auth_verify_endpoint(self, test_client):
        token = _generate_test_token(user_id="u-api", tenant_id="tenant-api")
        response = test_client.post(
            "/admin/queue/websocket/auth/verify",
            json={"token": token},
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "AUTHENTICATED"
        assert data.get("tenant_source") == "VERIFIED_TOKEN_CLAIMS"
        assert data.get("tenant_id") == "tenant-api"
