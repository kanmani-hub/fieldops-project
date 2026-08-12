"""
Tests for the tenant-aware database and Redis brand-safety
rule provider.
"""

from __future__ import annotations

import json

import pytest

from collections.abc import Iterator
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session,sessionmaker

from sqlalchemy.pool import StaticPool

from app.models import AIBrandSafetyRule
from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationContext,
    CommunicationDecision,
)
from app.services.ai.guardrails.brand_safety_rule_provider import (
    BrandSafetyRuleProviderError,
    DatabaseRedisBrandSafetyRuleProvider,
)
from app.services.ai.guardrails.brand_safety_validator import (
    BrandSafetyRuleProvider,
    BrandSafetyValidator,
)


class FakeRedis:
    """
    Small in-memory Redis replacement for unit tests.
    """

    def __init__(
        self,
    ) -> None:
        self.values: dict[str, str] = {}

        self.get_calls = 0
        self.setex_calls = 0
        self.delete_calls = 0

        self.fail_get = False
        self.fail_setex = False
        self.fail_delete = False

    def get(
        self,
        key: str,
    ) -> str | None:
        self.get_calls += 1

        if self.fail_get:
            raise RuntimeError(
                "Redis unavailable."
            )

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

        self.setex_calls += 1

        if self.fail_setex:
            raise RuntimeError(
                "Redis unavailable."
            )

        self.values[key] = value

        return True

    def delete(
        self,
        key: str,
    ) -> int:
        self.delete_calls += 1

        if self.fail_delete:
            raise RuntimeError(
                "Redis unavailable."
            )

        return int(
            self.values.pop(
                key,
                None,
            )
            is not None
        )


@pytest.fixture
def db_session() -> Iterator[Session]:
    """
    Create an isolated in-memory brand-rule database.
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


def build_context() -> CommunicationContext:
    """
    Build a valid communication context.
    """

    return CommunicationContext(
        job_id="JOB-1001",
        notification_type="job_assigned",
        recipient_type="CUSTOMER",
        channel="SMS",
        job_status="ASSIGNED",
    )


def add_rule(
    db: Session,
    *,
    tenant_id: str,
    rule_id: str,
    pattern: str,
    category: str = "COMPETITOR",
    match_type: str = "PHRASE",
    severity: str = "ERROR",
    active: bool = True,
    case_sensitive: bool = False,
) -> AIBrandSafetyRule:
    """
    Insert one tenant rule.
    """

    row = AIBrandSafetyRule(
        tenant_id=tenant_id,
        rule_id=rule_id,
        category=category,
        match_type=match_type,
        pattern=pattern,
        severity=severity,
        active=active,
        case_sensitive=case_sensitive,
        created_by="admin-1",
    )

    db.add(
        row
    )

    db.commit()

    return row


def build_provider(
    db: Session,
    redis: FakeRedis | None = None,
    *,
    tenant_id: str = "tenant-1",
    include_platform_defaults: bool = True,
) -> DatabaseRedisBrandSafetyRuleProvider:
    """
    Build a provider for tests.
    """

    return DatabaseRedisBrandSafetyRuleProvider(
        db=db,
        tenant_id=tenant_id,
        redis_client=redis,
        cache_ttl_seconds=60,
        include_platform_defaults=(
            include_platform_defaults
        ),
    )


def test_provider_implements_rule_provider(
    db_session: Session,
) -> None:
    """
    Provider follows the existing protocol.
    """

    provider = build_provider(
        db_session
    )

    assert isinstance(
        provider,
        BrandSafetyRuleProvider,
    )


def test_empty_database_returns_platform_defaults(
    db_session: Session,
) -> None:
    """
    Existing platform rules remain active without tenant rules.
    """

    rules = build_provider(
        db_session
    ).get_rules(
        context=build_context()
    )

    rule_ids = {
        rule.rule_id
        for rule in rules
    }

    assert (
        "POLITICAL_VOTE_FOR"
        in rule_ids
    )

    assert (
        "BLOCKED_GUARANTEED_REFUND"
        in rule_ids
    )


def test_tenant_rule_is_merged_with_defaults(
    db_session: Session,
) -> None:
    """
    Tenant rules and platform defaults are combined.
    """

    add_rule(
        db_session,
        tenant_id="tenant-1",
        rule_id="COMPETITOR_ACME",
        pattern="Acme Services",
    )

    rules = build_provider(
        db_session
    ).get_rules(
        context=build_context()
    )

    assert any(
        rule.rule_id
        == "COMPETITOR_ACME"
        for rule in rules
    )

    assert any(
        rule.rule_id
        == "POLITICAL_VOTE_FOR"
        for rule in rules
    )


def test_tenant_rules_are_isolated(
    db_session: Session,
) -> None:
    """
    A tenant must never receive another tenant's rules.
    """

    add_rule(
        db_session,
        tenant_id="tenant-2",
        rule_id="COMPETITOR_OTHER",
        pattern="Other Services",
    )

    rules = build_provider(
        db_session,
        tenant_id="tenant-1",
        include_platform_defaults=False,
    ).get_rules(
        context=build_context()
    )

    assert rules == ()


def test_cache_miss_loads_database_and_writes_cache(
    db_session: Session,
) -> None:
    """
    A Redis miss causes a database read and Redis write.
    """

    redis = FakeRedis()

    add_rule(
        db_session,
        tenant_id="tenant-1",
        rule_id="COMPETITOR_ACME",
        pattern="Acme Services",
    )

    provider = build_provider(
        db_session,
        redis,
    )

    rules = provider.get_rules(
        context=build_context()
    )

    assert redis.get_calls == 1
    assert redis.setex_calls == 1

    assert (
        provider.cache_key
        in redis.values
    )

    assert any(
        rule.rule_id
        == "COMPETITOR_ACME"
        for rule in rules
    )


def test_cache_hit_returns_cached_rules(
    db_session: Session,
) -> None:
    """
    Cached rules can be returned without another database read.
    """

    redis = FakeRedis()

    row = add_rule(
        db_session,
        tenant_id="tenant-1",
        rule_id="COMPETITOR_ACME",
        pattern="Acme Services",
    )

    provider = build_provider(
        db_session,
        redis,
        include_platform_defaults=False,
    )

    first_rules = provider.get_rules(
        context=build_context()
    )

    db_session.delete(
        row
    )

    db_session.commit()

    second_rules = provider.get_rules(
        context=build_context()
    )

    assert first_rules == second_rules
    assert redis.get_calls == 2
    assert redis.setex_calls == 1


def test_invalid_cache_falls_back_to_database(
    db_session: Session,
) -> None:
    """
    Corrupted Redis content is discarded safely.
    """

    redis = FakeRedis()

    add_rule(
        db_session,
        tenant_id="tenant-1",
        rule_id="COMPETITOR_ACME",
        pattern="Acme Services",
    )

    provider = build_provider(
        db_session,
        redis,
        include_platform_defaults=False,
    )

    redis.values[
        provider.cache_key
    ] = "not-json"

    rules = provider.get_rules(
        context=build_context()
    )

    assert redis.delete_calls == 1
    assert redis.setex_calls == 1
    assert len(rules) == 1

    assert (
        rules[0].rule_id
        == "COMPETITOR_ACME"
    )


def test_inactive_custom_rule_is_not_returned(
    db_session: Session,
) -> None:
    """
    Disabled custom rules do not run.
    """

    add_rule(
        db_session,
        tenant_id="tenant-1",
        rule_id="COMPETITOR_ACME",
        pattern="Acme Services",
        active=False,
    )

    rules = build_provider(
        db_session,
        include_platform_defaults=False,
    ).get_rules(
        context=build_context()
    )

    assert rules == ()


def test_inactive_override_disables_platform_default(
    db_session: Session,
) -> None:
    """
    Tenant override may disable a platform default by rule_id.
    """

    add_rule(
        db_session,
        tenant_id="tenant-1",
        rule_id="POLITICAL_VOTE_FOR",
        pattern="vote for",
        category="POLITICAL",
        active=False,
    )

    rules = build_provider(
        db_session
    ).get_rules(
        context=build_context()
    )

    assert all(
        rule.rule_id
        != "POLITICAL_VOTE_FOR"
        for rule in rules
    )


def test_active_tenant_rule_replaces_default_with_same_id(
    db_session: Session,
) -> None:
    """
    An active tenant rule replaces a default with the same ID.
    """

    add_rule(
        db_session,
        tenant_id="tenant-1",
        rule_id="POLITICAL_VOTE_FOR",
        pattern="support this campaign",
        category="POLITICAL",
    )

    rules = build_provider(
        db_session
    ).get_rules(
        context=build_context()
    )

    matching_rule = next(
        rule
        for rule in rules
        if (
            rule.rule_id
            == "POLITICAL_VOTE_FOR"
        )
    )

    assert (
        matching_rule.pattern
        == "support this campaign"
    )


def test_redis_failure_falls_back_to_database(
    db_session: Session,
) -> None:
    """
    Redis failure does not prevent database rules from loading.
    """

    redis = FakeRedis()

    redis.fail_get = True
    redis.fail_setex = True

    add_rule(
        db_session,
        tenant_id="tenant-1",
        rule_id="COMPETITOR_ACME",
        pattern="Acme Services",
    )

    rules = build_provider(
        db_session,
        redis,
        include_platform_defaults=False,
    ).get_rules(
        context=build_context()
    )

    assert len(rules) == 1

    assert (
        rules[0].rule_id
        == "COMPETITOR_ACME"
    )


def test_database_failure_fails_closed() -> None:
    """
    Database failure raises a safe provider exception.
    """

    class BrokenSession:
        def query(
            self,
            model,
        ):
            _ = model

            raise SQLAlchemyError(
                "Database unavailable."
            )

    provider = (
        DatabaseRedisBrandSafetyRuleProvider(
            db=BrokenSession(),  # type: ignore[arg-type]
            tenant_id="tenant-1",
        )
    )

    with pytest.raises(
        BrandSafetyRuleProviderError,
        match="could not be loaded",
    ):
        provider.get_rules(
            context=build_context()
        )


def test_invalidate_cache_removes_tenant_key(
    db_session: Session,
) -> None:
    """
    Admin operations can clear the tenant cache.
    """

    redis = FakeRedis()

    provider = build_provider(
        db_session,
        redis,
    )

    redis.values[
        provider.cache_key
    ] = json.dumps([])

    assert (
        provider.invalidate_cache()
        is True
    )

    assert (
        provider.cache_key
        not in redis.values
    )


def test_validator_blocks_database_competitor_rule(
    db_session: Session,
) -> None:
    """
    Database rules connect correctly to BrandSafetyValidator.
    """

    add_rule(
        db_session,
        tenant_id="tenant-1",
        rule_id="COMPETITOR_ACME",
        pattern="Acme Services",
    )

    provider = build_provider(
        db_session,
        include_platform_defaults=False,
    )

    validator = BrandSafetyValidator(
        rule_provider=provider
    )

    decision = CommunicationDecision(
        channel="SMS",
        title=None,
        subject=None,
        message=(
            "Use Acme Services instead."
        ),
        tone="PROFESSIONAL",
        confidence=0.95,
    )

    result = validator.check(
        context=build_context(),
        decision=decision,
    )

    assert result.passed is False

    assert (
        result.violations[0].code
        == "BRAND_COMPETITOR_MENTION"
    )


def test_cache_keys_are_tenant_scoped(
    db_session: Session,
) -> None:
    """
    Every tenant receives a separate Redis cache key.
    """

    first = build_provider(
        db_session,
        tenant_id="tenant-1",
    )

    second = build_provider(
        db_session,
        tenant_id="tenant-2",
    )

    assert (
        first.cache_key
        != second.cache_key
    )