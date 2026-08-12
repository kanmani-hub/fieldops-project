from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import UniqueConstraint, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import (
    Base,
    NotificationTemplate,
    TemplateVersion,
)
from app.services.ai.FieldOpsAI.schemas.prompt_template import (
    AgentType,
    PromptChannel,
    PromptLanguage,
    PromptTemplateCreate,
    PromptTemplateUpdate,
)
from app.services.ai.FieldOpsAI.services.managed_prompt_template_registry import (
    ConflictError,
    ManagedPromptTemplateRegistry,
    NotFoundError,
)


# ==========================================================
# Test database
# ==========================================================


engine = create_engine(
    "sqlite:///:memory:",
    connect_args={
        "check_same_thread": False,
    },
    poolclass=StaticPool,
)

TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


# ==========================================================
# Fake Redis
# ==========================================================


class FakeRedis:
    """
    Small in-memory Redis replacement used by unit tests.
    """

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.expires: dict[str, int] = {}
        self.deleted_keys: list[str] = []

    def get(
        self,
        key: str,
    ) -> Any:
        return self.data.get(key)

    def setex(
        self,
        key: str,
        ttl: int,
        value: Any,
    ) -> bool:
        self.data[key] = value
        self.expires[key] = ttl
        return True

    def incr(
        self,
        key: str,
        amount: int = 1,
    ) -> int:
        current = int(
            self.data.get(
                key,
                0,
            )
        )

        updated = current + amount
        self.data[key] = updated

        return updated

    def delete(
        self,
        key: str,
    ) -> int:
        self.deleted_keys.append(key)
        self.data.pop(key, None)
        self.expires.pop(key, None)
        return 1

    def reset(self) -> None:
        self.data.clear()
        self.expires.clear()
        self.deleted_keys.clear()


class BrokenRedis:
    """
    Redis fake that raises for every operation.

    Registry operations must continue using the database.
    """

    def get(
        self,
        key: str,
    ) -> Any:
        raise RuntimeError(
            "Redis unavailable"
        )

    def setex(
        self,
        key: str,
        ttl: int,
        value: Any,
    ) -> bool:
        raise RuntimeError(
            "Redis unavailable"
        )

    def incr(
        self,
        key: str,
        amount: int = 1,
    ) -> int:
        raise RuntimeError(
            "Redis unavailable"
        )

    def delete(
        self,
        key: str,
    ) -> int:
        raise RuntimeError(
            "Redis unavailable"
        )


# ==========================================================
# Fixtures
# ==========================================================


@pytest.fixture
def db_session() -> Session:
    Base.metadata.drop_all(
        bind=engine
    )

    Base.metadata.create_all(
        bind=engine
    )

    session = TestingSessionLocal()

    try:
        yield session
    finally:
        session.rollback()
        session.close()

        Base.metadata.drop_all(
            bind=engine
        )


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def registry(
    db_session: Session,
    fake_redis: FakeRedis,
) -> ManagedPromptTemplateRegistry:
    return ManagedPromptTemplateRegistry(
        db=db_session,
        tenant_id="tenant_1",
        actor_id="actor_1",
        redis_client=fake_redis,
        cache_ttl_seconds=60,
    )


# ==========================================================
# Test helpers
# ==========================================================


def make_prompt(
    **overrides: Any,
) -> PromptTemplateCreate:
    values: dict[str, Any] = {
        "name": "Test prompt",
        "agent_type": AgentType.CommsAgent,
        "channel": PromptChannel.sms,
        "language": PromptLanguage.en,
        "status": "assigned",
        "body": (
            "Hello {{ customer_name }}"
        ),
        "variables": [
            "customer_name",
        ],
        "is_active": True,
    }

    values.update(overrides)

    from app.services.ai.FieldOpsAI.schemas.prompt_template import (
        normalize_template_status,
        UnsupportedTemplateStatusError,
    )
    st = values.get("status")
    if isinstance(st, str):
        try:
            norm = normalize_template_status(st, allow_default=True)
            values["status"] = norm.value if hasattr(norm, "value") else str(norm)
        except UnsupportedTemplateStatusError:
            values["status"] = "assigned"

    return PromptTemplateCreate(
        **values
    )


def make_registry(
    db_session: Session,
    redis_client: Any,
    tenant_id: str,
) -> ManagedPromptTemplateRegistry:
    return ManagedPromptTemplateRegistry(
        db=db_session,
        tenant_id=tenant_id,
        actor_id="test_actor",
        redis_client=redis_client,
        cache_ttl_seconds=60,
    )


# ==========================================================
# Schema tests
# ==========================================================


@pytest.mark.parametrize(
    "agent_type",
    [
        AgentType.CommsAgent,
        AgentType.SentimentAgent,
    ],
)
def test_schema_supports_both_agent_types(
    agent_type: AgentType,
) -> None:
    payload = make_prompt(
        agent_type=agent_type
    )

    assert payload.agent_type == agent_type


@pytest.mark.parametrize(
    "channel",
    [
        PromptChannel.sms,
        PromptChannel.email,
        PromptChannel.push,
        PromptChannel.portal,
    ],
)
def test_schema_supports_all_channels(
    channel: PromptChannel,
) -> None:
    payload = make_prompt(
        channel=channel
    )

    assert payload.channel == channel


@pytest.mark.parametrize(
    "language",
    [
        PromptLanguage.en,
        PromptLanguage.es,
        PromptLanguage.ta,
        PromptLanguage.hi,
    ],
)
def test_schema_supports_all_languages(
    language: PromptLanguage,
) -> None:
    payload = make_prompt(
        language=language
    )

    assert payload.language == language


def test_update_schema_supports_lookup_fields() -> None:
    update = PromptTemplateUpdate(
        agent_type=AgentType.SentimentAgent,
        channel=PromptChannel.email,
        language=PromptLanguage.ta,
    )

    assert (
        update.agent_type
        == AgentType.SentimentAgent
    )

    assert (
        update.channel
        == PromptChannel.email
    )

    assert (
        update.language
        == PromptLanguage.ta
    )


def test_declared_variable_is_accepted() -> None:
    payload = make_prompt(
        body="Hello {{ customer_name }}",
        variables=["customer_name"],
    )

    assert payload.variables == [
        "customer_name"
    ]


def test_undeclared_body_variable_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="Template validation failed.",
    ):
        make_prompt(
            body="Hello {{ customer_name }}",
            variables=[],
        )


def test_undeclared_title_variable_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="Template validation failed.",
    ):
        make_prompt(
            title="Hello {{ customer_name }}",
            body="Service update",
            variables=[],
        )


def test_invalid_jinja_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="Template validation failed.",
    ):
        make_prompt(
            body="{{ customer_name",
            variables=["customer_name"],
        )


def test_duplicate_variables_are_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="Template validation failed.",
    ):
        make_prompt(
            variables=[
                "customer_name",
                "customer_name",
            ]
        )


def test_unsafe_jinja_attribute_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="Template validation failed.",
    ):
        make_prompt(
            body="{{ customer.__class__ }}",
            variables=["customer"],
        )


def test_client_version_is_rejected() -> None:
    """
    PromptTemplateCreate must not accept a version field.
    Clients cannot control the assigned version number.
    """
    with pytest.raises(
        (ValueError, TypeError),
    ):
        PromptTemplateCreate(
            name="Test",
            agent_type=AgentType.CommsAgent,
            channel=PromptChannel.sms,
            language=PromptLanguage.en,
            status="assigned",
            body="Hello {{ customer_name }}",
            variables=["customer_name"],
            version=5,  # must be rejected
        )


# ==========================================================
# CRUD tests
# ==========================================================


def test_create_get_update_and_soft_delete(
    registry: ManagedPromptTemplateRegistry,
) -> None:
    created = registry.create(
        make_prompt()
    )

    assert created.id is not None
    assert created.name == "Test prompt"
    assert created.is_active is True

    fetched = registry.get(
        created.id
    )

    assert fetched.id == created.id

    updated = registry.update(
        created.id,
        PromptTemplateUpdate(
            name="Updated prompt",
            body="Updated body",
            variables=[],
        ),
    )

    assert updated.name == "Updated prompt"
    assert updated.body == "Updated body"

    registry.delete(
        created.id
    )

    stored_template = (
        registry.db.query(
            NotificationTemplate
        )
        .filter(
            NotificationTemplate.id
            == created.id
        )
        .one()
    )

    assert stored_template.is_active is False
    assert stored_template.is_deleted is True
    assert stored_template.deleted_at is not None
    assert stored_template.deleted_by == "actor_1"

    stored_versions = (
        registry.db.query(
            TemplateVersion
        )
        .filter(
            TemplateVersion.template_id
            == created.id
        )
        .all()
    )

    assert stored_versions
    assert all(
        version.is_active is False
        for version in stored_versions
    )
    assert all(
        version.is_deleted is True
        for version in stored_versions
    )
    assert all(
        version.deleted_at is not None
        for version in stored_versions
    )
    assert all(
        version.deleted_by == "actor_1"
        for version in stored_versions
    )

    with pytest.raises(
        NotFoundError
    ):
        registry.get(
            created.id
        )


def test_patch_updates_agent_channel_and_language(
    registry: ManagedPromptTemplateRegistry,
) -> None:
    registry.create(
        make_prompt(
            status="patch_fields",
            language=PromptLanguage.en
        )
    )
    created = registry.create(
        make_prompt(
            status="patch_fields",
            language=PromptLanguage.es
        )
    )

    registry.create(
        make_prompt(
            agent_type=AgentType.SentimentAgent,
            channel=PromptChannel.email,
            language=PromptLanguage.en,
            status="patch_fields"
        )
    )
    
    updated = registry.update(
        created.id,
        PromptTemplateUpdate(
            agent_type=AgentType.SentimentAgent,
            channel=PromptChannel.email,
            language=PromptLanguage.ta,
        ),
    )

    assert (
        updated.agent_type
        == AgentType.SentimentAgent
    )

    assert (
        updated.channel
        == PromptChannel.email
    )

    assert (
        updated.language
        == PromptLanguage.ta
    )


def test_cross_tenant_get_update_and_delete_are_blocked(
    db_session: Session,
    fake_redis: FakeRedis,
) -> None:
    tenant_one = make_registry(
        db_session,
        fake_redis,
        "tenant_1",
    )

    tenant_two = make_registry(
        db_session,
        fake_redis,
        "tenant_2",
    )

    created = tenant_one.create(
        make_prompt()
    )

    with pytest.raises(NotFoundError):
        tenant_two.get(
            created.id
        )

    with pytest.raises(NotFoundError):
        tenant_two.update(
            created.id,
            PromptTemplateUpdate(
                name="Forbidden update"
            ),
        )

    with pytest.raises(NotFoundError):
        tenant_two.delete(
            created.id
        )


def test_duplicate_version_returns_conflict(
    registry: ManagedPromptTemplateRegistry,
) -> None:
    registry.create(
        make_prompt()
    )

    with pytest.raises(ConflictError):
        registry.create(
            make_prompt(
                name="Duplicate record"
            )
        )


# ==========================================================
# Filtering tests
# ==========================================================


def test_list_filters_by_combined_fields(
    registry: ManagedPromptTemplateRegistry,
) -> None:
    registry.create(
        make_prompt(
            name="SMS English",
            status="assigned",
            channel=PromptChannel.sms,
            language=PromptLanguage.en,
        )
    )
    registry.create(
        make_prompt(
            name="Email English",
            status="completed",
            channel=PromptChannel.email,
            language=PromptLanguage.en,
        )
    )

    registry.create(
        make_prompt(
            name="Email Tamil",
            status="completed",
            channel=PromptChannel.email,
            language=PromptLanguage.ta,
        )
    )

    results = registry.list(
        agent_type="CommsAgent",
        channel="email",
        language="ta",
        status="completed",
        is_active=True,
    )

    assert len(results) == 1
    assert results[0].name == "Email Tamil"


# ==========================================================
# Fallback tests
# ==========================================================


@pytest.mark.parametrize(
    (
        "candidate_tenant",
        "candidate_language",
        "candidate_status",
        "expected_source",
    ),
    [
        (
            "tenant_1",
            "es",
            "enroute",
            "tenant",
        ),
        (
            "tenant_1",
            "en",
            "enroute",
            "tenant",
        ),
        (
            "tenant_1",
            "es",
            "default",
            "tenant",
        ),
        (
            "tenant_1",
            "en",
            "default",
            "tenant",
        ),
        (
            "**platform**",
            "es",
            "enroute",
            "platform",
        ),
        (
            "**platform**",
            "en",
            "enroute",
            "platform",
        ),
        (
            "**platform**",
            "es",
            "default",
            "platform",
        ),
        (
            "**platform**",
            "en",
            "default",
            "platform",
        ),
    ],
)
def test_each_fallback_level(
    db_session: Session,
    fake_redis: FakeRedis,
    candidate_tenant: str,
    candidate_language: str,
    candidate_status: str,
    expected_source: str,
) -> None:
    tenant_registry = make_registry(
        db_session,
        fake_redis,
        "tenant_1",
    )

    candidate_registry = make_registry(
        db_session,
        fake_redis,
        candidate_tenant,
    )

    if candidate_language != "en":
        candidate_registry.create(
            make_prompt(
                name="Fallback candidate EN",
                language=PromptLanguage.en,
                status=candidate_status,
                body="Selected fallback",
                variables=[],
            )
        )
    candidate_registry.create(
        make_prompt(
            name="Fallback candidate",
            language=PromptLanguage(
                candidate_language
            ),
            status=candidate_status,
            body="Selected fallback",
            variables=[],
        )
    )

    result = tenant_registry.find(
        agent_type="CommsAgent",
        channel="sms",
        language="es",
        status="enroute",
    )

    assert result.body == "Selected fallback"
    assert result.source == expected_source


def test_builtin_default_is_returned_when_database_is_empty(
    registry: ManagedPromptTemplateRegistry,
) -> None:
    result = registry.find(
        agent_type="CommsAgent",
        channel="push",
        language="hi",
        status="assigned",
    )

    assert result.id is None
    assert result.source == "builtin_default"
    assert result.is_active is True


def test_highest_version_is_selected(
    registry: ManagedPromptTemplateRegistry,
) -> None:
    registry.create(
        make_prompt(
            body="Version one",
            variables=[],
        )
    )

    second_version = NotificationTemplate(
        name="Version two",
        type="assigned",
        channel="sms",
        locale="en",
        format="text",
        title_template=None,
        body_template="Version two",
        variables=[],
        version=2,
        is_active=True,
        tenant_id="tenant_1",
        agent_type="CommsAgent",
    )

    registry.db.add(
        second_version
    )

    registry.db.commit()

    result = registry.find(
        agent_type="CommsAgent",
        channel="sms",
        language="en",
        status="assigned",
    )

    assert result.body == "Version two"
    assert result.version == 2


# ==========================================================
# Portal mapping
# ==========================================================


def test_portal_is_stored_as_in_app(
    registry: ManagedPromptTemplateRegistry,
) -> None:
    created = registry.create(
        make_prompt(
            channel=PromptChannel.portal,
            status="portal_test",
        )
    )

    stored = (
        registry.db.query(
            NotificationTemplate
        )
        .filter(
            NotificationTemplate.id
            == created.id
        )
        .one()
    )

    assert stored.channel == "in_app"
    assert created.channel == PromptChannel.portal


# ==========================================================
# Cache tests
# ==========================================================


def test_cache_miss_writes_json_with_ttl_60(
    registry: ManagedPromptTemplateRegistry,
    fake_redis: FakeRedis,
) -> None:
    created = registry.create(
        make_prompt(
            status="cache_miss"
        )
    )

    fake_redis.reset()

    result = registry.get(
        created.id
    )

    assert result.id == created.id

    cache_key = registry._build_cache_key(
        "prompt_get",
        id=created.id,
    )

    assert cache_key in fake_redis.data
    assert fake_redis.expires[
        cache_key
    ] == 60


def test_cache_hit_avoids_database_lookup(
    registry: ManagedPromptTemplateRegistry,
    fake_redis: FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = registry.create(
        make_prompt(
            status="cache_hit"
        )
    )

    fake_redis.reset()

    registry.get(
        created.id
    )

    def fail_if_database_called(
        template_id: int,
    ) -> None:
        raise AssertionError(
            "Database lookup occurred during a cache hit."
        )

    monkeypatch.setattr(
        registry.repo,
        "get_by_id",
        fail_if_database_called,
    )

    cached_result = registry.get(
        created.id
    )

    assert cached_result.id == created.id


def test_corrupted_json_cache_is_deleted(
    registry: ManagedPromptTemplateRegistry,
    fake_redis: FakeRedis,
) -> None:
    created = registry.create(
        make_prompt(
            status="bad_json_cache"
        )
    )

    fake_redis.reset()

    cache_key = registry._build_cache_key(
        "prompt_get",
        id=created.id,
    )

    fake_redis.data[
        cache_key
    ] = "{not-valid-json"

    result = registry.get(
        created.id
    )

    assert result.id == created.id
    assert cache_key in fake_redis.deleted_keys


def test_schema_invalid_cache_is_deleted(
    registry: ManagedPromptTemplateRegistry,
    fake_redis: FakeRedis,
) -> None:
    created = registry.create(
        make_prompt(
            status="bad_schema_cache"
        )
    )

    fake_redis.reset()

    cache_key = registry._build_cache_key(
        "prompt_get",
        id=created.id,
    )

    fake_redis.data[
        cache_key
    ] = json.dumps(
        {
            "id": "invalid-id",
            "name": "Broken cache",
        }
    )

    result = registry.get(
        created.id
    )

    assert result.id == created.id
    assert cache_key in fake_redis.deleted_keys


def test_inactive_get_is_not_cached(
    registry: ManagedPromptTemplateRegistry,
    fake_redis: FakeRedis,
) -> None:
    created = registry.create(
        make_prompt(
            status="inactive_cache"
        )
    )
    tenant_hash = registry._hash_tenant(
        "tenant_1"
    )

    generation_before = registry._get_generation(
        tenant_hash
    )

    registry.delete(
        created.id
    )
    generation_after = registry._get_generation(
        tenant_hash
    )

    assert generation_after == (
        generation_before + 1
    )

    fake_redis.reset()

    with pytest.raises(
        NotFoundError
    ):
        registry.get(
            created.id
        )

    cache_key = registry._build_cache_key(
        "prompt_get",
        id=created.id,
    )

    assert cache_key not in fake_redis.data


def test_redis_failure_fails_open(
    db_session: Session,
) -> None:
    registry = make_registry(
        db_session,
        BrokenRedis(),
        "tenant_1",
    )

    created = registry.create(
        make_prompt(
            status="redis_failure"
        )
    )

    fetched = registry.get(
        created.id
    )

    assert fetched.id == created.id


# ==========================================================
# Generation invalidation tests
# ==========================================================


def test_tenant_mutation_changes_only_tenant_generation(
    registry: ManagedPromptTemplateRegistry,
    fake_redis: FakeRedis,
) -> None:
    created = registry.create(
        make_prompt(
            status="tenant_generation"
        )
    )

    tenant_hash = registry._hash_tenant(
        "tenant_1"
    )

    platform_hash = registry._hash_tenant(
        "**platform**"
    )

    tenant_before = registry._get_generation(
        tenant_hash
    )

    platform_before = registry._get_generation(
        platform_hash
    )

    registry.update(
        created.id,
        PromptTemplateUpdate(
            name="Updated"
        ),
    )

    tenant_after = registry._get_generation(
        tenant_hash
    )

    platform_after = registry._get_generation(
        platform_hash
    )

    assert tenant_after == tenant_before + 1
    assert platform_after == platform_before


def test_platform_mutation_changes_platform_generation(
    db_session: Session,
    fake_redis: FakeRedis,
) -> None:
    platform_registry = make_registry(
        db_session,
        fake_redis,
        "**platform**",
    )

    created = platform_registry.create(
        make_prompt(
            status="platform_generation"
        )
    )

    platform_hash = (
        platform_registry._hash_tenant(
            "**platform**"
        )
    )

    before = (
        platform_registry._get_generation(
            platform_hash
        )
    )

    platform_registry.update(
        created.id,
        PromptTemplateUpdate(
            name="Updated platform prompt"
        ),
    )

    after = (
        platform_registry._get_generation(
            platform_hash
        )
    )

    assert after == before + 1


# ==========================================================
# Server-assigned version tests (§7)
# ==========================================================


def test_new_live_template_version_is_1(
    registry: ManagedPromptTemplateRegistry,
    db_session: Session,
) -> None:
    """A freshly created live template must have version=1 server-assigned."""
    created = registry.create(make_prompt(status="ver_new"))

    stored = (
        db_session.query(NotificationTemplate)
        .filter(NotificationTemplate.id == created.id)
        .one()
    )
    assert stored.version == 1


def test_initial_history_version_is_1(
    registry: ManagedPromptTemplateRegistry,
    db_session: Session,
) -> None:
    """The initial TemplateVersion record must carry version_number=1."""
    created = registry.create(make_prompt(status="ver_hist"))

    versions = (
        db_session.query(TemplateVersion)
        .filter(TemplateVersion.template_id == created.id)
        .all()
    )
    assert len(versions) >= 1
    assert versions[0].version_number == 1


def test_live_and_history_versions_match(
    registry: ManagedPromptTemplateRegistry,
    db_session: Session,
) -> None:
    """live template version and first history snapshot version_number must agree."""
    created = registry.create(make_prompt(status="ver_sync"))

    stored = (
        db_session.query(NotificationTemplate)
        .filter(NotificationTemplate.id == created.id)
        .one()
    )
    first_version = (
        db_session.query(TemplateVersion)
        .filter(TemplateVersion.template_id == created.id)
        .order_by(TemplateVersion.version_number)
        .first()
    )
    assert first_version is not None
    assert stored.version == first_version.version_number


# ==========================================================
# Translation completeness & locale resolution tests (§6)
# ==========================================================


def test_non_english_create_without_english(registry, db_session):
    from app.services.ai.FieldOpsAI.schemas.prompt_template import PromptTemplateCreate
    from app.services.ai.FieldOpsAI.services.managed_prompt_template_registry import TemplateValidationServiceError

    payload = PromptTemplateCreate(
        name="test-es",
        agent_type="CommsAgent",
        channel="sms",
        language="es",
        status="job_assigned",
        body="Hola",
    )
    with pytest.raises(TemplateValidationServiceError) as e:
        registry.create(payload)
    assert "canonical English template is required" in str(e.value)


def test_non_english_update_without_english(registry, db_session):
    from app.services.ai.FieldOpsAI.schemas.prompt_template import PromptTemplateCreate, PromptTemplateUpdate
    from app.services.ai.FieldOpsAI.services.managed_prompt_template_registry import TemplateValidationServiceError

    en_payload = PromptTemplateCreate(name="test-en", agent_type="CommsAgent", channel="sms", language="en", status="job_assigned", body="Hello")
    en_t = registry.create(en_payload)

    es_payload = PromptTemplateCreate(name="test-es", agent_type="CommsAgent", channel="sms", language="es", status="job_assigned", body="Hola")
    es_t = registry.create(es_payload)

    registry.delete(en_t.id)

    upd = PromptTemplateUpdate(body="Hola 2")
    with pytest.raises(TemplateValidationServiceError) as e:
        registry.update(es_t.id, upd)
    assert "canonical English template is required" in str(e.value)


def test_es_mx_database_lookup_falls_back_to_es(registry, db_session):
    from app.services.ai.FieldOpsAI.schemas.prompt_template import PromptTemplateCreate

    en_payload = PromptTemplateCreate(name="test-en", agent_type="CommsAgent", channel="sms", language="en", status="job_assigned", body="Hello")
    registry.create(en_payload)

    es_payload = PromptTemplateCreate(name="test-es", agent_type="CommsAgent", channel="sms", language="es", status="job_assigned", body="Hola desde es")
    registry.create(es_payload)

    match = registry.find("CommsAgent", "sms", "es-MX", "job_assigned")
    assert (match.language.value if hasattr(match.language, "value") else match.language) == "es"
    assert match.body == "Hola desde es"


def test_create_unsupported_format_raises_error() -> None:
    """Formats other than text/html must be rejected at schema level."""
    from pydantic import ValidationError

    with pytest.raises((ValueError, ValidationError)):
        PromptTemplateCreate(
            name="Bad format",
            agent_type=AgentType.CommsAgent,
            channel=PromptChannel.sms,
            language=PromptLanguage.en,
            status="fmt_bad",
            body="Hello {{ customer_name }}",
            format="markdown",
            variables=["customer_name"],
        )

def test_es_mx_exact_locale_wins(
    registry,
    db_session,
):
    english = NotificationTemplate(
        name="English",
        type="regional_exact",
        channel="sms",
        locale="en",
        format="text",
        title_template=None,
        body_template="English body",
        variables=[],
        version=1,
        is_active=True,
        is_deleted=False,
        tenant_id="tenant_1",
        agent_type="CommsAgent",
    )

    spanish = NotificationTemplate(
        name="Spanish",
        type="created",
        channel="sms",
        locale="es",
        format="text",
        title_template=None,
        body_template="Spanish body",
        variables=[],
        version=1,
        is_active=True,
        is_deleted=False,
        tenant_id="tenant_1",
        agent_type="CommsAgent",
    )

    mexican_spanish = NotificationTemplate(
        name="Mexican Spanish",
        type="created",
        channel="sms",
        locale="es-MX",
        format="text",
        title_template=None,
        body_template="Mexican Spanish body",
        variables=[],
        version=1,
        is_active=True,
        is_deleted=False,
        tenant_id="tenant_1",
        agent_type="CommsAgent",
    )

    db_session.add_all(
        [
            english,
            spanish,
            mexican_spanish,
        ]
    )
    db_session.commit()

    result = registry.find(
        "CommsAgent",
        "sms",
        "es-MX",
        "created",
    )

    assert result.language == "es-MX"
    assert result.body == (
        "Mexican Spanish body"
    )

def test_es_mx_falls_back_to_english(
    registry,
    db_session,
):
    english = NotificationTemplate(
        name="English fallback",
        type="onsite",
        channel="sms",
        locale="en",
        format="text",
        title_template=None,
        body_template="English fallback body",
        variables=[],
        version=1,
        is_active=True,
        is_deleted=False,
        tenant_id="tenant_1",
        agent_type="CommsAgent",
    )

    db_session.add(english)
    db_session.commit()

    result = registry.find(
        "CommsAgent",
        "sms",
        "es-MX",
        "onsite",
    )

    assert result.language == "en"
    assert result.body == (
        "English fallback body"
    )