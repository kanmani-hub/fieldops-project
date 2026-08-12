"""
brand_safety_admin_service.py

Administration service for tenant-specific AI brand-safety
rules.

Responsibilities
----------------
- Create brand-safety rules
- Retrieve one rule
- List tenant rules
- Update rules
- Deactivate rules without deleting them
- Enforce tenant isolation
- Validate rules using the production guardrail contract
- Commit or roll back database changes
- Invalidate the tenant Redis cache after successful changes

The service does not:

- Define FastAPI routes
- Decode authentication tokens
- Run BrandSafetyValidator
- Generate customer communication
- Permanently delete rules
"""

from __future__ import annotations

from sqlalchemy.exc import (
    IntegrityError,
    SQLAlchemyError,
)
from sqlalchemy.orm import Session

from app.models import AIBrandSafetyRule
from app.services.ai.guardrails.brand_safety_admin_schemas import (
    BrandSafetyRuleCreate,
    BrandSafetyRuleUpdate,
)
from app.services.ai.guardrails.brand_safety_rule_provider import (
    DatabaseRedisBrandSafetyRuleProvider,
)
from app.services.ai.guardrails.brand_safety_validator import (
    BrandSafetyRule,
    BrandSafetyRuleCategory,
)


# ==========================================================
# Service Exceptions
# ==========================================================


class BrandSafetyAdminError(RuntimeError):
    """
    Base error for brand-safety administration.
    """


class BrandSafetyRuleNotFoundError(
    BrandSafetyAdminError
):
    """
    Raised when a tenant rule does not exist.
    """


class BrandSafetyRuleConflictError(
    BrandSafetyAdminError
):
    """
    Raised when the tenant already has the same rule ID.
    """


class BrandSafetyRulePersistenceError(
    BrandSafetyAdminError
):
    """
    Raised when a database operation cannot be completed.
    """


# ==========================================================
# Administration Service
# ==========================================================


class BrandSafetyAdminService:
    """
    Manage brand-safety rules for one trusted tenant.
    """

    def __init__(
        self,
        *,
        db: Session,
        tenant_id: str,
        actor_id: str,
        redis_client=None,
    ) -> None:
        """
        Initialize a tenant-scoped administration service.

        Parameters
        ----------
        db
            Existing SQLAlchemy session.

        tenant_id
            Trusted tenant identifier from the authenticated
            request layer.

        actor_id
            Administrator or manager performing the change.

        redis_client
            Existing FieldOps RedisCacheManager.

            Redis is optional. Database changes remain valid when
            Redis is unavailable.
        """

        normalized_tenant_id = tenant_id.strip()
        normalized_actor_id = actor_id.strip()

        if not normalized_tenant_id:
            raise ValueError(
                "tenant_id must not be empty."
            )

        if len(normalized_tenant_id) > 50:
            raise ValueError(
                "tenant_id exceeds the supported length."
            )

        if not normalized_actor_id:
            raise ValueError(
                "actor_id must not be empty."
            )

        if len(normalized_actor_id) > 100:
            raise ValueError(
                "actor_id exceeds the supported length."
            )

        self._db = db
        self._tenant_id = normalized_tenant_id
        self._actor_id = normalized_actor_id

        self._rule_provider = (
            DatabaseRedisBrandSafetyRuleProvider(
                db=db,
                tenant_id=normalized_tenant_id,
                redis_client=redis_client,
            )
        )

    # ------------------------------------------------------

    def create_rule(
        self,
        payload: BrandSafetyRuleCreate,
    ) -> AIBrandSafetyRule:
        """
        Create one tenant-specific brand-safety rule.

        Duplicate tenant_id + rule_id combinations are rejected.
        """

        validated_rule = BrandSafetyRule(
            rule_id=payload.rule_id,
            category=payload.category,
            match_type=payload.match_type,
            pattern=payload.pattern,
            severity=payload.severity,
            active=payload.active,
            case_sensitive=payload.case_sensitive,
        )

        row = AIBrandSafetyRule(
            tenant_id=self._tenant_id,
            rule_id=validated_rule.rule_id,
            category=validated_rule.category.value,
            match_type=validated_rule.match_type.value,
            pattern=validated_rule.pattern,
            severity=validated_rule.severity.value,
            active=validated_rule.active,
            case_sensitive=(
                validated_rule.case_sensitive
            ),
            created_by=self._actor_id,
            updated_by=None,
        )

        self._db.add(
            row
        )

        self._commit_rule(
            row,
            conflict_message=(
                "A brand-safety rule with this rule_id "
                "already exists for the tenant."
            ),
        )

        self._invalidate_cache()

        return row

    # ------------------------------------------------------

    def get_rule(
        self,
        rule_id: str,
    ) -> AIBrandSafetyRule:
        """
        Retrieve one rule belonging to this tenant.
        """

        normalized_rule_id = self._normalize_rule_id(
            rule_id
        )

        row = (
            self._db.query(
                AIBrandSafetyRule
            )
            .filter(
                AIBrandSafetyRule.tenant_id
                == self._tenant_id,
                AIBrandSafetyRule.rule_id
                == normalized_rule_id,
            )
            .first()
        )

        if row is None:
            raise BrandSafetyRuleNotFoundError(
                "Brand-safety rule was not found."
            )

        return row

    # ------------------------------------------------------

    def list_rules(
        self,
        *,
        active_only: bool | None = None,
        category: (
            BrandSafetyRuleCategory
            | None
        ) = None,
    ) -> tuple[AIBrandSafetyRule, ...]:
        """
        List persisted rules belonging to this tenant.

        Parameters
        ----------
        active_only
            True:
                Return only active rules.

            False:
                Return only inactive rules.

            None:
                Return both active and inactive rules.

        category
            Optional category filter.
        """

        try:
            query = (
                self._db.query(
                    AIBrandSafetyRule
                )
                .filter(
                    AIBrandSafetyRule.tenant_id
                    == self._tenant_id
                )
            )

            if active_only is not None:
                query = query.filter(
                    AIBrandSafetyRule.active
                    == active_only
                )

            if category is not None:
                query = query.filter(
                    AIBrandSafetyRule.category
                    == category.value
                )

            rows = (
                query
                .order_by(
                    AIBrandSafetyRule.rule_id.asc()
                )
                .all()
            )

            return tuple(
                rows
            )

        except SQLAlchemyError as exc:
            raise BrandSafetyRulePersistenceError(
                "Brand-safety rules could not be listed."
            ) from exc

    # ------------------------------------------------------

    def update_rule(
        self,
        *,
        rule_id: str,
        payload: BrandSafetyRuleUpdate,
    ) -> AIBrandSafetyRule:
        """
        Update an existing tenant rule.

        The final combined rule is validated before any database
        value is changed.
        """

        row = self.get_rule(
            rule_id
        )

        changes = payload.model_dump(
            exclude_unset=True,
        )

        category = changes.get(
            "category",
            row.category,
        )

        match_type = changes.get(
            "match_type",
            row.match_type,
        )

        pattern = changes.get(
            "pattern",
            row.pattern,
        )

        severity = changes.get(
            "severity",
            row.severity,
        )

        active = changes.get(
            "active",
            row.active,
        )

        case_sensitive = changes.get(
            "case_sensitive",
            row.case_sensitive,
        )

        # Validate the complete future state before modifying
        # the SQLAlchemy row.
        validated_rule = BrandSafetyRule(
            rule_id=row.rule_id,
            category=category,
            match_type=match_type,
            pattern=pattern,
            severity=severity,
            active=active,
            case_sensitive=case_sensitive,
        )

        row.category = (
            validated_rule.category.value
        )

        row.match_type = (
            validated_rule.match_type.value
        )

        row.pattern = (
            validated_rule.pattern
        )

        row.severity = (
            validated_rule.severity.value
        )

        row.active = (
            validated_rule.active
        )

        row.case_sensitive = (
            validated_rule.case_sensitive
        )

        row.updated_by = self._actor_id

        self._commit_rule(
            row
        )

        self._invalidate_cache()

        return row

    # ------------------------------------------------------

    def deactivate_rule(
        self,
        rule_id: str,
    ) -> AIBrandSafetyRule:
        """
        Disable a rule without deleting its database record.

        This operation is idempotent. Calling it for an already
        inactive rule returns the same inactive rule.
        """

        row = self.get_rule(
            rule_id
        )

        if row.active is False:
            return row

        row.active = False
        row.updated_by = self._actor_id

        self._commit_rule(
            row
        )

        self._invalidate_cache()

        return row

    # ------------------------------------------------------

    def _commit_rule(
        self,
        row: AIBrandSafetyRule,
        *,
        conflict_message: str | None = None,
    ) -> None:
        """
        Commit and refresh one rule safely.

        Integrity errors produce a conflict error.

        Other database errors produce a persistence error.

        Every failed transaction is rolled back.
        """

        try:
            self._db.commit()
            self._db.refresh(
                row
            )

        except IntegrityError as exc:
            self._db.rollback()

            raise BrandSafetyRuleConflictError(
                conflict_message
                or (
                    "The brand-safety rule conflicts with "
                    "an existing database record."
                )
            ) from exc

        except SQLAlchemyError as exc:
            self._db.rollback()

            raise BrandSafetyRulePersistenceError(
                "The brand-safety rule could not be saved."
            ) from exc

    # ------------------------------------------------------

    def _invalidate_cache(
        self,
    ) -> None:
        """
        Clear the tenant's merged rule cache.

        Cache invalidation is attempted only after a successful
        database commit.

        Redis is an optimization. If Redis is unavailable, the
        database change remains committed and the cache expires
        naturally according to its TTL.
        """

        self._rule_provider.invalidate_cache()

    # ------------------------------------------------------

    @staticmethod
    def _normalize_rule_id(
        rule_id: str,
    ) -> str:
        """
        Validate a rule identifier received from a path or
        service call.
        """

        normalized_rule_id = rule_id.strip()

        if not normalized_rule_id:
            raise ValueError(
                "rule_id must not be empty."
            )

        if len(normalized_rule_id) > 100:
            raise ValueError(
                "rule_id exceeds the supported length."
            )

        return normalized_rule_id