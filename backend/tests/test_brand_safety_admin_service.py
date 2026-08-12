"""
Tests for tenant-specific brand-safety administration.
"""

from __future__ import annotations

import pytest
from collections.abc import Iterator

from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import (
    Session,
    sessionmaker,
)
from sqlalchemy.pool import StaticPool

from app.models import AIBrandSafetyRule
from app.services.ai.guardrails.brand_safety_admin_schemas import (
    BrandSafetyRuleCreate,
    BrandSafetyRuleResponse,
    BrandSafetyRuleUpdate,
)
from app.services.ai.guardrails.brand_safety_admin_service import (
    BrandSafetyAdminService,
    BrandSafetyRuleConflictError,
    BrandSafetyRuleNotFoundError,
)
from app.services.ai.guardrails.brand_safety_validator import (
    BrandSafetyRuleCategory,
)


class FakeRedis:
    """
    Small Redis replacement used by service tests.
    """

    def __init__(
        self,
    ) -> None:
        self.values: dict[str, str] = {}
        self.deleted_keys: list[str] = []
        self.fail_delete = False

    def get(
        self,
        key: str,
    ) -> str | None:
        return self.values.get(
            key
        )

    def setex(
        self,
        key: str,
        seconds: int,
        value: str,
    ) -> bool:
        _ = seconds

        self.values[key] = value

        return True

    def delete(
        self,
        key: str,
    ) -> bool:
        self.deleted_keys.append(
            key
        )

        if self.fail_delete:
            raise RuntimeError(
                "Redis unavailable."
            )

        return (
            self.values.pop(
                key,
                None,
            )
            is not None
        )


@pytest.fixture
def db_session() -> Iterator[Session]:
    """
    Create an isolated in-memory rule database.
    """

    engine = create_engine(
        "sqlite://",
        connect_args={
            "check_same_thread": False,
        },
        poolclass=StaticPool,
    )

    AIBrandSafetyRule.__table__.create(
        bind=engine
    )

    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    session = testing_session()

    try:
        yield session

    finally:
        session.close()

        AIBrandSafetyRule.__table__.drop(
            bind=engine
        )

        engine.dispose()


def build_service(
    db: Session,
    *,
    tenant_id: str = "tenant-1",
    actor_id: str = "admin-1",
    redis: FakeRedis | None = None,
) -> BrandSafetyAdminService:
    """
    Build a tenant-scoped service.
    """

    return BrandSafetyAdminService(
        db=db,
        tenant_id=tenant_id,
        actor_id=actor_id,
        redis_client=redis,
    )


def build_create_payload(
    *,
    rule_id: str = "COMPETITOR_ACME",
    pattern: str = "Acme Services",
    category: str = "COMPETITOR",
    match_type: str = "PHRASE",
    active: bool = True,
) -> BrandSafetyRuleCreate:
    """
    Build a valid create payload.
    """

    return BrandSafetyRuleCreate(
        rule_id=rule_id,
        category=category,
        match_type=match_type,
        pattern=pattern,
        severity="ERROR",
        active=active,
        case_sensitive=False,
    )


def test_create_rule_persists_and_invalidates_cache(
    db_session: Session,
) -> None:
    """
    A valid rule is saved and the tenant cache is cleared.
    """

    redis = FakeRedis()

    service = build_service(
        db_session,
        redis=redis,
    )

    row = service.create_rule(
        build_create_payload()
    )

    assert row.tenant_id == "tenant-1"
    assert row.rule_id == "COMPETITOR_ACME"
    assert row.category == "COMPETITOR"
    assert row.match_type == "PHRASE"
    assert row.pattern == "Acme Services"
    assert row.active is True
    assert row.created_by == "admin-1"

    assert (
        db_session.query(
            AIBrandSafetyRule
        ).count()
        == 1
    )

    assert redis.deleted_keys == [
        (
            "ai:guardrails:brand-safety:v1:"
            "tenant-1"
        )
    ]

    response = (
        BrandSafetyRuleResponse
        .model_validate(
            row
        )
    )

    assert response.rule_id == "COMPETITOR_ACME"


def test_duplicate_rule_id_raises_conflict(
    db_session: Session,
) -> None:
    """
    One tenant cannot create the same rule ID twice.
    """

    service = build_service(
        db_session
    )

    service.create_rule(
        build_create_payload()
    )

    with pytest.raises(
        BrandSafetyRuleConflictError,
        match="already exists",
    ):
        service.create_rule(
            build_create_payload()
        )

    assert (
        db_session.query(
            AIBrandSafetyRule
        ).count()
        == 1
    )


def test_list_rules_is_tenant_isolated(
    db_session: Session,
) -> None:
    """
    Rules from another tenant are never returned.
    """

    build_service(
        db_session,
        tenant_id="tenant-1",
    ).create_rule(
        build_create_payload(
            rule_id="COMPETITOR_ONE",
            pattern="First Services",
        )
    )

    build_service(
        db_session,
        tenant_id="tenant-2",
    ).create_rule(
        build_create_payload(
            rule_id="COMPETITOR_TWO",
            pattern="Second Services",
        )
    )

    rules = build_service(
        db_session,
        tenant_id="tenant-1",
    ).list_rules()

    assert len(rules) == 1
    assert rules[0].rule_id == "COMPETITOR_ONE"


def test_list_rules_filters_active_state(
    db_session: Session,
) -> None:
    """
    Active and inactive rules can be listed separately.
    """

    service = build_service(
        db_session
    )

    service.create_rule(
        build_create_payload(
            rule_id="ACTIVE_RULE",
            pattern="Active Services",
            active=True,
        )
    )

    service.create_rule(
        build_create_payload(
            rule_id="INACTIVE_RULE",
            pattern="Inactive Services",
            active=False,
        )
    )

    active_rules = service.list_rules(
        active_only=True
    )

    inactive_rules = service.list_rules(
        active_only=False
    )

    assert {
        rule.rule_id
        for rule in active_rules
    } == {
        "ACTIVE_RULE",
    }

    assert {
        rule.rule_id
        for rule in inactive_rules
    } == {
        "INACTIVE_RULE",
    }


def test_list_rules_filters_category(
    db_session: Session,
) -> None:
    """
    Administrators may filter rules by category.
    """

    service = build_service(
        db_session
    )

    service.create_rule(
        build_create_payload(
            rule_id="COMPETITOR_RULE",
            pattern="Competitor Services",
            category="COMPETITOR",
        )
    )

    service.create_rule(
        build_create_payload(
            rule_id="OFF_BRAND_RULE",
            pattern="not acceptable",
            category="OFF_BRAND",
        )
    )

    rules = service.list_rules(
        category=(
            BrandSafetyRuleCategory.COMPETITOR
        )
    )

    assert len(rules) == 1
    assert rules[0].rule_id == "COMPETITOR_RULE"


def test_get_rule_rejects_cross_tenant_access(
    db_session: Session,
) -> None:
    """
    Another tenant's rule behaves as not found.
    """

    build_service(
        db_session,
        tenant_id="tenant-2",
    ).create_rule(
        build_create_payload()
    )

    with pytest.raises(
        BrandSafetyRuleNotFoundError
    ):
        build_service(
            db_session,
            tenant_id="tenant-1",
        ).get_rule(
            "COMPETITOR_ACME"
        )


def test_update_rule_changes_validated_fields(
    db_session: Session,
) -> None:
    """
    Rule fields and updated_by are changed safely.
    """

    redis = FakeRedis()

    service = build_service(
        db_session,
        actor_id="admin-creator",
        redis=redis,
    )

    service.create_rule(
        build_create_payload()
    )

    update_service = build_service(
        db_session,
        actor_id="manager-updater",
        redis=redis,
    )

    updated = update_service.update_rule(
        rule_id="COMPETITOR_ACME",
        payload=BrandSafetyRuleUpdate(
            pattern="New Acme Group",
            severity="CRITICAL",
            case_sensitive=True,
        ),
    )

    assert updated.pattern == "New Acme Group"
    assert updated.severity == "CRITICAL"
    assert updated.case_sensitive is True
    assert updated.updated_by == "manager-updater"

    assert len(redis.deleted_keys) == 2


def test_update_preserves_unsupplied_fields(
    db_session: Session,
) -> None:
    """
    Fields not included in the update remain unchanged.
    """

    service = build_service(
        db_session
    )

    service.create_rule(
        build_create_payload()
    )

    updated = service.update_rule(
        rule_id="COMPETITOR_ACME",
        payload=BrandSafetyRuleUpdate(
            severity="WARNING",
        ),
    )

    assert updated.pattern == "Acme Services"
    assert updated.category == "COMPETITOR"
    assert updated.match_type == "PHRASE"
    assert updated.severity == "WARNING"
    assert updated.active is True


def test_empty_update_payload_is_rejected() -> None:
    """
    An update must contain at least one field.
    """

    with pytest.raises(
        ValidationError,
        match="At least one rule field",
    ):
        BrandSafetyRuleUpdate()


def test_word_rule_rejects_multiple_words() -> None:
    """
    WORD rules cannot contain whitespace.
    """

    with pytest.raises(
        ValidationError,
        match="WORD brand-safety rules",
    ):
        BrandSafetyRuleCreate(
            rule_id="INVALID_WORD_RULE",
            category="COMPETITOR",
            match_type="WORD",
            pattern="Acme Services",
        )


def test_deactivate_rule_keeps_database_record(
    db_session: Session,
) -> None:
    """
    Deactivation changes active=False without deleting the row.
    """

    redis = FakeRedis()

    service = build_service(
        db_session,
        actor_id="admin-creator",
        redis=redis,
    )

    service.create_rule(
        build_create_payload()
    )

    deactivation_service = build_service(
        db_session,
        actor_id="manager-deactivator",
        redis=redis,
    )

    row = deactivation_service.deactivate_rule(
        "COMPETITOR_ACME"
    )

    assert row.active is False

    assert (
        row.updated_by
        == "manager-deactivator"
    )

    assert (
        db_session.query(
            AIBrandSafetyRule
        ).count()
        == 1
    )


def test_rule_can_be_reactivated_using_update(
    db_session: Session,
) -> None:
    """
    An inactive rule can later be enabled again.
    """

    service = build_service(
        db_session
    )

    service.create_rule(
        build_create_payload(
            active=False
        )
    )

    row = service.update_rule(
        rule_id="COMPETITOR_ACME",
        payload=BrandSafetyRuleUpdate(
            active=True,
        ),
    )

    assert row.active is True


def test_redis_failure_does_not_rollback_database(
    db_session: Session,
) -> None:
    """
    Redis is optional and must not invalidate a committed rule.
    """

    redis = FakeRedis()
    redis.fail_delete = True

    service = build_service(
        db_session,
        redis=redis,
    )

    row = service.create_rule(
        build_create_payload()
    )

    assert row.id is not None

    assert (
        db_session.query(
            AIBrandSafetyRule
        ).count()
        == 1
    )

    assert len(redis.deleted_keys) == 1


def test_update_missing_rule_raises_not_found(
    db_session: Session,
) -> None:
    """
    An unknown tenant rule cannot be updated.
    """

    service = build_service(
        db_session
    )

    with pytest.raises(
        BrandSafetyRuleNotFoundError
    ):
        service.update_rule(
            rule_id="MISSING_RULE",
            payload=BrandSafetyRuleUpdate(
                active=False,
            ),
        )
