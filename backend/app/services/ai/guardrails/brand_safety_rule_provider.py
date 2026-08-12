"""
brand_safety_rule_provider.py

Tenant-aware database and Redis rule provider for the FieldOps
brand-safety guardrail.

Read order
----------
1. Redis cache
2. PostgreSQL database
3. Platform defaults merged with tenant overrides

Redis is an optimization only.

A Redis failure falls back to the database.

A database failure raises BrandSafetyRuleProviderError so the
guardrail pipeline fails closed and selects a safe fallback.
"""

from __future__ import annotations

import json

from typing import Any, Final

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import AIBrandSafetyRule
from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationContext,
)
from app.services.ai.guardrails.brand_safety_validator import (
    BrandSafetyRule,
    default_brand_safety_rules,
)


class BrandSafetyRuleProviderError(RuntimeError):
    """
    Raised when tenant brand-safety rules cannot be loaded
    safely.
    """


class DatabaseRedisBrandSafetyRuleProvider:
    """
    Load tenant-specific brand-safety rules using PostgreSQL
    and Redis.

    Tenant rules may:

    - Add a new rule
    - Replace a platform rule using the same rule_id
    - Disable a platform rule using active=False
    """

    CACHE_NAMESPACE: Final[str] = (
        "ai:guardrails:brand-safety:v1"
    )

    DEFAULT_CACHE_TTL_SECONDS: Final[int] = 300

    def __init__(
        self,
        *,
        db: Session,
        tenant_id: str,
        redis_client: Any | None = None,
        cache_ttl_seconds: int = (
            DEFAULT_CACHE_TTL_SECONDS
        ),
        include_platform_defaults: bool = True,
    ) -> None:
        """
        Initialize the provider for one tenant.

        Parameters
        ----------
        db
            Existing SQLAlchemy session.

        tenant_id
            Tenant whose rules should be loaded.

        redis_client
            Existing FieldOps RedisCacheManager or compatible
            Redis client.

        cache_ttl_seconds
            Number of seconds the merged rule list remains
            cached.

        include_platform_defaults
            Whether the built-in FieldOps rules should be merged
            with tenant rules.
        """

        normalized_tenant_id = tenant_id.strip()

        if not normalized_tenant_id:
            raise ValueError(
                "tenant_id must not be empty."
            )

        if len(normalized_tenant_id) > 50:
            raise ValueError(
                "tenant_id exceeds the supported length."
            )

        if cache_ttl_seconds <= 0:
            raise ValueError(
                "cache_ttl_seconds must be greater than zero."
            )

        self._db = db
        self._tenant_id = normalized_tenant_id
        self._redis = redis_client

        self._cache_ttl_seconds = (
            cache_ttl_seconds
        )

        self._include_platform_defaults = (
            include_platform_defaults
        )

    @property
    def cache_key(self) -> str:
        """
        Return the tenant-specific Redis cache key.
        """

        return (
            f"{self.CACHE_NAMESPACE}:"
            f"{self._tenant_id}"
        )

    def get_rules(
        self,
        *,
        context: CommunicationContext,
    ) -> tuple[BrandSafetyRule, ...]:
        """
        Return active rules for the configured tenant.

        The CommunicationContext is accepted because this class
        implements the existing BrandSafetyRuleProvider
        protocol.

        Tenant identity is injected separately by the service.
        It is not read from untrusted additional_context.
        """

        _ = context

        cached_rules = self._read_cache()

        if cached_rules is not None:
            return cached_rules

        database_rules = (
            self._load_database_rules()
        )

        merged_rules = self._merge_rules(
            database_rules
        )

        self._write_cache(
            merged_rules
        )

        return merged_rules

    def invalidate_cache(self) -> bool:
        """
        Remove this tenant's cached rules.

        Admin create, update, deactivate, and delete operations
        must call this after a successful database commit.
        """

        if self._redis is None:
            return False

        try:
            result = self._redis.delete(
                self.cache_key
            )

        except Exception:
            return False

        return bool(result)

    def _read_cache(
        self,
    ) -> tuple[BrandSafetyRule, ...] | None:
        """
        Read and validate rules from Redis.

        Invalid Redis content is deleted and treated as a cache
        miss.
        """

        if self._redis is None:
            return None

        try:
            raw_payload = self._redis.get(
                self.cache_key
            )

        except Exception:
            return None

        if raw_payload is None:
            return None

        try:
            decoded_payload = json.loads(
                raw_payload
            )

            if not isinstance(
                decoded_payload,
                list,
            ):
                raise ValueError(
                    "Cached brand-safety rules must be a list."
                )

            return tuple(
                BrandSafetyRule.model_validate(
                    item
                )
                for item in decoded_payload
            )

        except (
            json.JSONDecodeError,
            ValidationError,
            TypeError,
            ValueError,
        ):
            self.invalidate_cache()

            return None

    def _load_database_rules(
        self,
    ) -> tuple[BrandSafetyRule, ...]:
        """
        Load and validate tenant rule overrides from the
        database.
        """

        try:
            rows = (
                self._db.query(
                    AIBrandSafetyRule
                )
                .filter(
                    AIBrandSafetyRule.tenant_id
                    == self._tenant_id
                )
                .order_by(
                    AIBrandSafetyRule.rule_id.asc()
                )
                .all()
            )

            return tuple(
                BrandSafetyRule(
                    rule_id=row.rule_id,
                    category=row.category,
                    match_type=row.match_type,
                    pattern=row.pattern,
                    severity=row.severity,
                    active=row.active,
                    case_sensitive=(
                        row.case_sensitive
                    ),
                )
                for row in rows
            )

        except (
            SQLAlchemyError,
            ValidationError,
            TypeError,
            ValueError,
        ) as exc:
            raise BrandSafetyRuleProviderError(
                "Tenant brand-safety rules could not be "
                "loaded."
            ) from exc

    def _merge_rules(
        self,
        database_rules: tuple[
            BrandSafetyRule,
            ...,
        ],
    ) -> tuple[BrandSafetyRule, ...]:
        """
        Merge platform defaults and tenant overrides.

        Active tenant rule:
            Add or replace the rule.

        Inactive tenant rule:
            Remove the rule with the same rule_id.
        """

        merged_by_id: dict[
            str,
            BrandSafetyRule,
        ] = {}

        if self._include_platform_defaults:
            merged_by_id.update(
                {
                    rule.rule_id: rule
                    for rule
                    in default_brand_safety_rules()
                }
            )

        for rule in database_rules:
            if rule.active:
                merged_by_id[
                    rule.rule_id
                ] = rule

            else:
                merged_by_id.pop(
                    rule.rule_id,
                    None,
                )

        return tuple(
            merged_by_id[rule_id]
            for rule_id in sorted(
                merged_by_id
            )
        )

    def _write_cache(
        self,
        rules: tuple[
            BrandSafetyRule,
            ...,
        ],
    ) -> None:
        """
        Store validated active rules in Redis.

        A Redis write failure does not discard valid rules
        already loaded from the database.
        """

        if self._redis is None:
            return

        payload = json.dumps(
            [
                rule.model_dump(
                    mode="json"
                )
                for rule in rules
            ],
            separators=(
                ",",
                ":",
            ),
            ensure_ascii=False,
        )

        try:
            self._redis.setex(
                self.cache_key,
                self._cache_ttl_seconds,
                payload,
            )

        except Exception:
            return