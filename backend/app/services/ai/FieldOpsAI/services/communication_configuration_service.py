"""
communication_configuration_service.py

Authoritative domain service for system-wide communication channel configuration.

Story 14.3 — Cache-aside with bounded staleness
-----------------------------------------------
get_channel_configuration now implements a cache-aside pattern:

  1. Normalize and validate the channel.
  2. Build the single namespaced cache key.
  3. Attempt Redis GET.
  4. Valid payload → return cached configuration without querying the database.
  5. Cache miss / corrupt payload / Redis failure → query the database.
  6. Valid database row → write to Redis with SETEX(60) then return.
  7. Redis SETEX failure → still return the valid database result.
  8. Missing row (compatibility default) → return default, do NOT cache.
  9. Database exception → return CONFIGURATION_UNAVAILABLE, do NOT cache.

evaluate_delivery calls get_channel_configuration so all delivery paths
benefit from the cache automatically.  The cache is the only Redis touch
point; no provider adapter reads Redis directly.

Story 14.3 does NOT implement:
- environment overrides (Story 14.5)

Story 14.4 Implementation details:
- post-commit exact-key invalidation
- SETEX fallback
- 60-second bounded staleness only when both Redis commands fail asymmetrically
- database-only behavior when Redis is unavailable
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from typing import Optional

import datetime

from sqlalchemy.orm import Session

from ..schemas.communication_configuration import (
    CommunicationChannelState,
    CommunicationMessageCategory,
    DeliveryDecision,
    CommunicationConfigurationResponse,
    CommunicationConfigurationCachePayload,
    _CACHE_PAYLOAD_MAX_BYTES,
    normalize_channel,
    UnsupportedCommunicationChannelError,
    CommunicationConfigurationNotFoundError,
    CommunicationConfigurationUnavailableError,
    CommunicationConfigurationConflictError,
)
from ..repositories.communication_configuration_repository import (
    CommunicationConfigurationRepository,
)
from app.models import CommunicationConfigurationAudit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache key namespace — matches project convention fieldops:{domain}:v1:{detail}
# ---------------------------------------------------------------------------
_CACHE_KEY_PREFIX = "fieldops:communication-config:v1"
_OVERRIDE_ENV_BY_CHANNEL = {
    "SMS": "FIELDOPS_SMS_EMERGENCY_OVERRIDE",
    "EMAIL": "FIELDOPS_EMAIL_EMERGENCY_OVERRIDE",
}

_OVERRIDE_MAX_LENGTH = 20

_STATE_RESTRICTIVENESS = {
    CommunicationChannelState.ENABLED: 0,
    CommunicationChannelState.EMERGENCY_ONLY: 1,
    CommunicationChannelState.DISABLED: 2,
}


class CommunicationConfigurationService:
    """
    System-wide communication channel configuration domain service.

    Accepts an optional Redis client for cache-aside reads (Story 14.3).
    When redis_client is None the service operates database-only, which
    preserves Stories 14.1/14.2 semantics exactly.
    """

    CACHE_TTL_SECONDS: int = 60

    def __init__(
        self,
        repository: CommunicationConfigurationRepository,
        db: Session,
        redis_client=None,
        environment: Optional[Mapping[str, object]] = None,
    ) -> None:
        self.repository = repository
        self.db = db
        self._redis = redis_client
        self._environment = (
            environment
            if environment is not None
            else os.environ
        )

    # ------------------------------------------------------------------
    # Private: cache key
    # ------------------------------------------------------------------

    @staticmethod
    def _cache_key(channel: str) -> str:
        """
        Build a safe, namespaced Redis key for a normalized channel.

        channel must already be normalized (uppercase, validated).
        The key contains no tenant ID, actor ID, PII, or message content.
        """
        return f"{_CACHE_KEY_PREFIX}:{channel.lower()}"

    # ------------------------------------------------------------------
    # Private: Redis helpers (all failures are safe-logged and suppressed)
    # ------------------------------------------------------------------

    def _cache_get(self, key: str) -> Optional[CommunicationConfigurationCachePayload]:
        """
        Attempt to read and validate a cache entry.

        Returns the validated payload, or None on any failure, including:
        - Redis unavailable
        - Redis timeout
        - Missing key
        - Invalid / corrupt JSON
        - Failed schema validation
        - Oversized payload
        - Wrong schema_version or channel
        """
        if self._redis is None:
            return None
        try:
            raw = self._redis.get(key)
        except Exception:
            logger.warning(
                "communication_config_cache_error operation=get redis_available=False",
            )
            return None

        if raw is None:
            return None

        # Oversized guard — do not log raw value
        if isinstance(raw, (str, bytes)) and len(raw) > _CACHE_PAYLOAD_MAX_BYTES:
            logger.warning(
                "communication_config_cache_invalid reason=oversized",
            )
            self._cache_delete(key)
            return None

        try:
            data = json.loads(raw)
        except Exception:
            logger.warning(
                "communication_config_cache_invalid reason=invalid_json",
            )
            self._cache_delete(key)
            return None

        if not isinstance(data, dict):
            logger.warning(
                "communication_config_cache_invalid reason=non_object_json",
            )
            self._cache_delete(key)
            return None

        try:
            payload = CommunicationConfigurationCachePayload.model_validate(data)
        except Exception:
            logger.warning(
                "communication_config_cache_invalid reason=schema_validation_failed",
            )
            self._cache_delete(key)
            return None

        return payload

    def _cache_set(
        self,
        key: str,
        channel: str,
        config,
    ) -> bool:
        """
        Write a validated configuration row to the cache with TTL=60.

        Failures are logged and suppressed — the caller returns the valid
        database result regardless.
        """
        if self._redis is None:
            return False
        try:
            # Build the payload from the authoritative ORM row
            updated_at = config.updated_at
            if updated_at is not None and updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=datetime.timezone.utc)

            payload = CommunicationConfigurationCachePayload(
                schema_version=1,
                channel=channel,
                state=CommunicationChannelState(config.state),
                revision=config.revision,
                updated_at=updated_at,
                updated_by=config.updated_by,
            )
            serialized = payload.model_dump_json()
            self._redis.setex(key, self.CACHE_TTL_SECONDS, serialized)
            return True
        except Exception:
            logger.warning(
                "communication_config_cache_error operation=setex",
            )
            return False

    def _cache_delete(self, key: str) -> bool:
        """Safe single-key deletion for corrupt-cache cleanup and invalidation."""
        if self._redis is None:
            return False
        try:
            self._redis.delete(key)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Private: build response from cache payload
    # ------------------------------------------------------------------

    @staticmethod
    def _payload_to_response(
        payload: CommunicationConfigurationCachePayload,
    ) -> CommunicationConfigurationResponse:
        return CommunicationConfigurationResponse(
            channel=payload.channel,
            state=payload.state,
            revision=payload.revision,
            updated_at=payload.updated_at,
            updated_by=payload.updated_by,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_channel_configuration(self, channel: str) -> CommunicationConfigurationResponse:
        """
        Return the current channel configuration.

        Cache-aside algorithm (Story 14.3):
          1. Validate the channel.
          2. Check Redis for a valid cached payload → return on hit.
          3. On miss / invalid / Redis failure → query the database.
          4. Valid DB row → populate cache with SETEX(60) → return.
          5. Missing row → return compatibility default, do NOT cache.
          6. DB exception → re-raise (caller handles as 503).
        """
        channel = normalize_channel(channel)
        key = self._cache_key(channel)

        # Step 2: cache read
        payload = self._cache_get(key)
        if payload is not None:
            # Validate the payload channel matches the requested channel
            if payload.channel != channel:
                logger.warning(
                    "communication_config_cache_invalid reason=channel_mismatch",
                )
                self._cache_delete(key)
                # fall through to database
            else:
                logger.debug(
                    "communication_config_cache_hit channel=%s revision=%s",
                    channel,
                    payload.revision,
                )
                return self._payload_to_response(payload)

        # Step 3–6: database read
        config = self.repository.get_by_channel(channel)
        if not config:
            # Compatibility default — NOT cached (row may appear later)
            logger.debug(
                "communication_config_cache_miss channel=%s result=compatibility_default",
                channel,
            )
            return CommunicationConfigurationResponse(
                channel=channel,
                state=CommunicationChannelState.ENABLED,
                revision=0,
                updated_at=datetime.datetime.now(datetime.timezone.utc),
                updated_by="system_default",
            )

        # Populate cache (Step 4)
        self._cache_set(key, channel, config)

        logger.debug(
            "communication_config_cache_miss channel=%s revision=%s",
            channel,
            config.revision,
        )
        return self._to_response(config)

    def update_channel_state(
        self,
        channel: str,
        new_state: CommunicationChannelState,
        actor_id: str,
        actor_tenant_id: str,
        reason: str,
        correlation_id: str = None,
    ) -> CommunicationConfigurationResponse:
        """
        Atomically update channel state and write an audit record.

        Features:
        - post-commit exact-key invalidation
        - SETEX fallback
        - database-only behavior when Redis is unavailable
        """
        channel = normalize_channel(channel)
        reason = reason.strip()
        if not 10 <= len(reason) <= 500:
            raise CommunicationConfigurationConflictError(
                "Invalid configuration change reason."
            )

        try:
            config = self.repository.get_by_channel(channel, for_update=True)
            if not config:
                raise CommunicationConfigurationNotFoundError(channel)

            previous_state = config.state
            previous_revision = config.revision

            if previous_state == new_state.value:
                return self._to_response(config)

            self.repository.update_state(config, new_state.value, actor_id)

            audit = CommunicationConfigurationAudit(
                tenant_id=actor_tenant_id,
                channel=channel,
                previous_state=previous_state,
                new_state=new_state.value,
                previous_revision=previous_revision,
                new_revision=config.revision,
                actor_id=actor_id,
                actor_tenant_id=actor_tenant_id,
                reason=reason,
                correlation_id=correlation_id,
            )
            self.repository.add_audit(audit)

            self.db.flush()
            self.db.refresh(config)
            
            response = self._to_response(config)
            
            self.db.commit()
            
        except (
            UnsupportedCommunicationChannelError,
            CommunicationConfigurationNotFoundError,
            CommunicationConfigurationConflictError,
        ):
            self.db.rollback()
            raise

        except Exception:
            self.db.rollback()
            raise CommunicationConfigurationUnavailableError(
                "Communication configuration unavailable."
            ) from None

        cache_key = self._cache_key(channel)
        delete_success = self._cache_delete(cache_key)
        if not delete_success:
            set_success = self._cache_set(cache_key, channel, response)
            if not set_success:
                logger.warning(
                    "cache_sync_degraded operation=delete channel=%s result=failure",
                    channel
                )

        return response

    def evaluate_delivery(
        self,
        channel: str,
        category: CommunicationMessageCategory = (
            CommunicationMessageCategory.STANDARD
        ),
    ) -> DeliveryDecision:
        """
        Evaluate delivery using persistent configuration and
        the restrictive Story 14.5 environment override.
        """

        channel = normalize_channel(channel)

        try:
            category = CommunicationMessageCategory(category)
        except (TypeError, ValueError):
            category = CommunicationMessageCategory.STANDARD

        (
            override_active,
            override_state,
            override_reason,
        ) = self._parse_environment_override(channel)

        # Invalid environment values fail closed immediately.
        if override_active and override_state is None:
            return DeliveryDecision(
                allowed=False,
                channel=channel,
                state=CommunicationChannelState.DISABLED,
                category=category,
                reason_code=override_reason,
                revision=0,
                persistent_state=None,
                override_active=True,
                effective_state=(
                    CommunicationChannelState.DISABLED.value
                ),
                policy_source="environment",
            )

        persistent_state = None
        revision = 0
        configuration_failed = False

        try:
            config = self.get_channel_configuration(channel)
            revision = config.revision
            persistent_state = CommunicationChannelState(
                config.state
            )
        except Exception:
            configuration_failed = True

        # DISABLED is always the most restrictive state.
        # It can safely block even when Redis or the DB is down.
        if (
            override_active
            and override_state
            == CommunicationChannelState.DISABLED
        ):
            return DeliveryDecision(
                allowed=False,
                channel=channel,
                state=CommunicationChannelState.DISABLED,
                category=category,
                reason_code="ENV_OVERRIDE_DISABLED",
                revision=revision,
                persistent_state=(
                    persistent_state.value
                    if persistent_state is not None
                    else None
                ),
                override_active=True,
                effective_state=(
                    CommunicationChannelState.DISABLED.value
                ),
                policy_source="environment",
            )

        # EMERGENCY_ONLY cannot allow anything when the
        # persistent configuration is unavailable.
        if configuration_failed or persistent_state is None:
            return DeliveryDecision(
                allowed=False,
                channel=channel,
                state=CommunicationChannelState.DISABLED,
                category=category,
                reason_code="CONFIGURATION_UNAVAILABLE",
                revision=0,
                persistent_state=None,
                override_active=override_active,
                effective_state=(
                    CommunicationChannelState.DISABLED.value
                ),
                policy_source="fallback",
            )

        restrictiveness = {
            CommunicationChannelState.ENABLED: 1,
            CommunicationChannelState.EMERGENCY_ONLY: 2,
            CommunicationChannelState.DISABLED: 3,
        }

        if (
            override_active
            and override_state is not None
            and restrictiveness[override_state]
            > restrictiveness[persistent_state]
        ):
            effective_state = override_state
            policy_source = "environment"
        else:
            effective_state = persistent_state
            policy_source = "persistent"

        if effective_state == CommunicationChannelState.ENABLED:
            reason_code = (
                "COMPATIBILITY_DEFAULT"
                if revision == 0
                else f"{channel}_ENABLED"
            )

            return DeliveryDecision(
                allowed=True,
                channel=channel,
                state=effective_state,
                category=category,
                reason_code=reason_code,
                revision=revision,
                persistent_state=persistent_state.value,
                override_active=override_active,
                effective_state=effective_state.value,
                policy_source=policy_source,
            )

        if effective_state == CommunicationChannelState.DISABLED:
            return DeliveryDecision(
                allowed=False,
                channel=channel,
                state=effective_state,
                category=category,
                reason_code=f"{channel}_DISABLED",
                revision=revision,
                persistent_state=persistent_state.value,
                override_active=override_active,
                effective_state=effective_state.value,
                policy_source=policy_source,
            )

        if category == CommunicationMessageCategory.EMERGENCY:
            reason_code = (
                "ENV_OVERRIDE_EMERGENCY_ALLOWED"
                if policy_source == "environment"
                else f"{channel}_EMERGENCY_ALLOWED"
            )

            return DeliveryDecision(
                allowed=True,
                channel=channel,
                state=effective_state,
                category=category,
                reason_code=reason_code,
                revision=revision,
                persistent_state=persistent_state.value,
                override_active=override_active,
                effective_state=effective_state.value,
                policy_source=policy_source,
            )

        reason_code = (
            "ENV_OVERRIDE_EMERGENCY_REQUIRED"
            if policy_source == "environment"
            else f"{channel}_EMERGENCY_REQUIRED"
        )

        return DeliveryDecision(
            allowed=False,
            channel=channel,
            state=effective_state,
            category=category,
            reason_code=reason_code,
            revision=revision,
            persistent_state=persistent_state.value,
            override_active=override_active,
            effective_state=effective_state.value,
            policy_source=policy_source,
        )
    def _parse_environment_override(
        self,
        channel: str,
    ) -> tuple[
        bool,
        Optional[CommunicationChannelState],
        str,
    ]:
        """
        Return:
            override_active
            override_state
            reason_code

        Invalid overrides return:
            True, None, "CONFIGURATION_OVERRIDE_INVALID"
        """

        channel = normalize_channel(channel)

        variable_name = _OVERRIDE_ENV_BY_CHANNEL[channel]
        raw_value = self._environment.get(variable_name)

        # Variable does not exist.
        if raw_value is None:
            return False, None, ""

        # Only strings are accepted.
        if not isinstance(raw_value, str):
            logger.warning(
                "communication_config_override_invalid "
                "reason=non_string_value channel=%s",
                channel,
            )
            return (
                True,
                None,
                "CONFIGURATION_OVERRIDE_INVALID",
            )

        # Check the original value before trusting it.
        if len(raw_value) > _OVERRIDE_MAX_LENGTH:
            logger.warning(
                "communication_config_override_invalid "
                "reason=oversized channel=%s",
                channel,
            )
            return (
                True,
                None,
                "CONFIGURATION_OVERRIDE_INVALID",
            )

        if not raw_value.isprintable():
            logger.warning(
                "communication_config_override_invalid "
                "reason=control_character channel=%s",
                channel,
            )
            return (
                True,
                None,
                "CONFIGURATION_OVERRIDE_INVALID",
            )

        value = raw_value.strip().upper()

        # No active override.
        if value in {"", "INHERIT"}:
            return False, None, ""

        # Most restrictive override.
        if value == "DISABLED":
            return (
                True,
                CommunicationChannelState.DISABLED,
                "ENV_OVERRIDE_DISABLED",
            )

        # Emergency messages only.
        if value == "EMERGENCY_ONLY":
            return (
                True,
                CommunicationChannelState.EMERGENCY_ONLY,
                "",
            )

        # ENABLED, TRUE, FALSE, UNKNOWN and all other
        # unexpected values fail closed.
        logger.warning(
            "communication_config_override_invalid "
            "reason=invalid_value channel=%s",
            channel,
        )

        return (
            True,
            None,
            "CONFIGURATION_OVERRIDE_INVALID",
        )
    # ------------------------------------------------------------------
    # Private: ORM → response conversion
    # ------------------------------------------------------------------

    def _to_response(self, config) -> CommunicationConfigurationResponse:
        return CommunicationConfigurationResponse(
            channel=config.channel,
            state=CommunicationChannelState(config.state),
            revision=config.revision,
            updated_at=config.updated_at,
            updated_by=config.updated_by,
        )
