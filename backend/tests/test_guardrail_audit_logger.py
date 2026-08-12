"""
test_guardrail_audit_logger.py

Tests for immutable AI guardrail audit persistence.
"""

from __future__ import annotations

import pytest
from collections.abc import Iterator
from sqlalchemy import create_engine
from sqlalchemy.orm import (
    Session,
    sessionmaker,
)
from sqlalchemy.pool import StaticPool

from app.models import AIGuardrailViolation
from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationContext,
    CommunicationDecision,
)
from app.services.ai.guardrails.audit_logger import (
    GuardrailAuditLogger,
)
from app.services.ai.guardrails.contracts import (
    GuardrailCategory,
    GuardrailCheckResult,
    GuardrailPipelineResult,
    GuardrailSeverity,
    GuardrailViolation,
)


# ==========================================================
# Database Fixture
# ==========================================================


@pytest.fixture
def db_session() -> Iterator[Session]:
    """
    Create an isolated in-memory audit database.
    """

    engine = create_engine(
        "sqlite://",
        connect_args={
            "check_same_thread": False,
        },
        poolclass=StaticPool,
    )

    AIGuardrailViolation.__table__.create(
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

        AIGuardrailViolation.__table__.drop(
            bind=engine
        )

        engine.dispose()


# ==========================================================
# Helpers
# ==========================================================


def build_context() -> CommunicationContext:
    """
    Build a sanitized communication context.
    """

    return CommunicationContext(
        job_id="JOB-1001",
        correlation_id=(
            "correlation-1001"
        ),
        notification_type="job_assigned",
        recipient_type="CUSTOMER",
        channel="SMS",
        customer_name="{{CUSTOMER_NAME_1}}",
        technician_name=(
            "{{TECHNICIAN_NAME_1}}"
        ),
        job_status="ASSIGNED",
        sentiment="NEUTRAL",
    )


def build_decision(
    message: str = (
        "Your technician is on the way."
    ),
) -> CommunicationDecision:
    """
    Build a valid SMS decision.
    """

    return CommunicationDecision(
        channel="SMS",
        title=None,
        subject=None,
        message=message,
        tone="PROFESSIONAL",
        confidence=0.95,
    )


def build_violation(
    *,
    code: str = "SMS_MESSAGE_TOO_LONG",
    category: GuardrailCategory = (
        GuardrailCategory.LENGTH
    ),
    severity: GuardrailSeverity = (
        GuardrailSeverity.ERROR
    ),
) -> GuardrailViolation:
    """
    Build an audit-safe test violation.
    """

    return GuardrailViolation(
        code=code,
        category=category,
        severity=severity,
        message=(
            "Generated communication failed a test "
            "guardrail."
        ),
        field="message",
        safe_metadata={
            "actual_length": 161,
            "maximum_length": 160,
        },
    )


def build_fallback_result(
    *,
    checker_name: str = (
        "length_validator"
    ),
    violation: (
        GuardrailViolation
        | None
    ) = None,
) -> GuardrailPipelineResult:
    """
    Build a failed pipeline result.
    """

    check = GuardrailCheckResult(
        checker_name=checker_name,
        passed=False,
        violations=(
            violation or build_violation(),
        ),
        latency_ms=0.5,
    )

    return GuardrailPipelineResult.from_checks(
        checks=(
            check,
        ),
        total_latency_ms=1.2,
    )


def build_allow_result() -> GuardrailPipelineResult:
    """
    Build a successful pipeline result.
    """

    check = GuardrailCheckResult(
        checker_name="length_validator",
        passed=True,
        violations=(),
        latency_ms=0.2,
    )

    return GuardrailPipelineResult.from_checks(
        checks=(
            check,
        ),
        total_latency_ms=0.2,
    )


def build_logger(
    db_session: Session,
    *,
    key: str = "unit-test-secret-key",
) -> GuardrailAuditLogger:
    """
    Build an audit logger for tests.
    """

    return GuardrailAuditLogger(
        db=db_session,
        fingerprint_key=key,
    )


# ==========================================================
# Successful Pipeline Test
# ==========================================================


def test_allow_result_creates_no_audit_rows(
    db_session: Session,
) -> None:
    """
    Safe communication does not create violation records.
    """

    records = build_logger(
        db_session
    ).record_pipeline_result(
        tenant_id="tenant-1",
        context=build_context(),
        decision=build_decision(),
        result=build_allow_result(),
        fallback_triggered=False,
    )

    assert records == ()

    assert (
        db_session.query(
            AIGuardrailViolation
        ).count()
        == 0
    )


# ==========================================================
# Persistence Test
# ==========================================================


def test_fallback_result_creates_audit_record(
    db_session: Session,
) -> None:
    """
    One violation creates one immutable database row.
    """

    records = build_logger(
        db_session
    ).record_pipeline_result(
        tenant_id="tenant-1",
        context=build_context(),
        decision=build_decision(),
        result=build_fallback_result(),
        fallback_triggered=True,
    )

    assert len(records) == 1

    stored = db_session.query(
        AIGuardrailViolation
    ).one()

    assert stored.tenant_id == "tenant-1"

    assert (
        stored.correlation_id
        == "correlation-1001"
    )

    assert stored.job_id == "JOB-1001"

    assert (
        stored.agent_name
        == "communication_agent"
    )

    assert (
        stored.checker_name
        == "length_validator"
    )

    assert (
        stored.violation_code
        == "SMS_MESSAGE_TOO_LONG"
    )

    assert stored.category == "LENGTH"
    assert stored.severity == "ERROR"
    assert stored.affected_field == "message"
    assert stored.pipeline_decision == "FALLBACK"
    assert stored.fallback_triggered is True

    assert stored.safe_metadata == {
        "actual_length": 161,
        "maximum_length": 160,
    }

    assert len(stored.prompt_hash) == 64
    assert len(stored.output_hash) == 64


# ==========================================================
# Multiple Violation Test
# ==========================================================


def test_multiple_violations_create_one_row_each(
    db_session: Session,
) -> None:
    """
    Every checker violation receives its own audit row.
    """

    length_violation = build_violation()

    pii_violation = GuardrailViolation(
        code="PII_EMAIL_DETECTED",
        category=GuardrailCategory.PII,
        severity=GuardrailSeverity.CRITICAL,
        message=(
            "Generated communication contains prohibited "
            "email information."
        ),
        field="message",
        safe_metadata={
            "pii_type": "EMAIL",
            "match_count": 1,
        },
    )

    length_check = GuardrailCheckResult(
        checker_name="length_validator",
        passed=False,
        violations=(
            length_violation,
        ),
        latency_ms=0.4,
    )

    pii_check = GuardrailCheckResult(
        checker_name="pii_output_detector",
        passed=False,
        violations=(
            pii_violation,
        ),
        latency_ms=0.8,
    )

    result = GuardrailPipelineResult.from_checks(
        checks=(
            length_check,
            pii_check,
        ),
        total_latency_ms=1.2,
    )

    build_logger(
        db_session
    ).record_pipeline_result(
        tenant_id="tenant-1",
        context=build_context(),
        decision=build_decision(),
        result=result,
        fallback_triggered=True,
    )

    stored_records = db_session.query(
        AIGuardrailViolation
    ).all()

    assert len(stored_records) == 2

    assert {
        record.violation_code
        for record in stored_records
    } == {
        "SMS_MESSAGE_TOO_LONG",
        "PII_EMAIL_DETECTED",
    }


# ==========================================================
# Privacy Test
# ==========================================================


def test_raw_prompt_and_output_are_not_stored(
    db_session: Session,
) -> None:
    """
    Audit rows contain fingerprints, not raw PII or output.
    """

    private_name = "Ruby Customer"

    private_email = (
        "private.customer@example.com"
    )

    private_prompt = {
        "customer_name": private_name,
        "customer_email": private_email,
        "instruction": (
            "Generate a communication update."
        ),
    }

    private_output = (
        f"Contact {private_email} for {private_name}."
    )

    logger = build_logger(
        db_session
    )

    logger.record_pipeline_result(
        tenant_id="tenant-1",
        context=build_context(),
        decision=build_decision(
            private_output
        ),
        result=build_fallback_result(
            checker_name=(
                "pii_output_detector"
            ),
            violation=GuardrailViolation(
                code="PII_EMAIL_DETECTED",
                category=(
                    GuardrailCategory.PII
                ),
                severity=(
                    GuardrailSeverity.CRITICAL
                ),
                message=(
                    "Generated communication contains "
                    "prohibited email information."
                ),
                field="message",
                safe_metadata={
                    "pii_type": "EMAIL",
                    "match_count": 1,
                },
            ),
        ),
        fallback_triggered=True,
        prompt_payload=private_prompt,
    )

    stored = db_session.query(
        AIGuardrailViolation
    ).one()

    serialized_record = str(
        {
            column.name: getattr(
                stored,
                column.name,
            )
            for column in (
                AIGuardrailViolation
                .__table__
                .columns
            )
        }
    )

    assert (
        private_name
        not in serialized_record
    )

    assert (
        private_email
        not in serialized_record
    )

    assert (
        private_output
        not in serialized_record
    )


# ==========================================================
# Fingerprint Tests
# ==========================================================


def test_fingerprint_is_deterministic_and_keyed(
    db_session: Session,
) -> None:
    """
    Equivalent payloads match, while different keys do not.
    """

    first_payload = {
        "channel": "SMS",
        "job_id": "JOB-1001",
    }

    reordered_payload = {
        "job_id": "JOB-1001",
        "channel": "SMS",
    }

    first_logger = build_logger(
        db_session,
        key="first-secret",
    )

    second_logger = build_logger(
        db_session,
        key="second-secret",
    )

    first_hash = (
        first_logger.fingerprint_payload(
            first_payload
        )
    )

    reordered_hash = (
        first_logger.fingerprint_payload(
            reordered_payload
        )
    )

    different_key_hash = (
        second_logger.fingerprint_payload(
            first_payload
        )
    )

    assert first_hash == reordered_hash
    assert first_hash != different_key_hash
    assert len(first_hash) == 64


# ==========================================================
# Validation Tests
# ==========================================================


def test_logger_requires_fingerprint_key(
    db_session: Session,
) -> None:
    """
    Fingerprinting must never use an empty secret.
    """

    with pytest.raises(
        ValueError,
        match="fingerprint_key must not be empty",
    ):
        GuardrailAuditLogger(
            db=db_session,
            fingerprint_key="",
        )


def test_logger_requires_tenant_id(
    db_session: Session,
) -> None:
    """
    Guardrail records must always be tenant-scoped.
    """

    with pytest.raises(
        ValueError,
        match="tenant_id must not be empty",
    ):
        build_logger(
            db_session
        ).record_pipeline_result(
            tenant_id=" ",
            context=build_context(),
            decision=build_decision(),
            result=build_fallback_result(),
            fallback_triggered=True,
        )


def test_allow_cannot_report_fallback(
    db_session: Session,
) -> None:
    """
    ALLOW and fallback_triggered cannot both be true.
    """

    with pytest.raises(
        ValueError,
        match=(
            "An ALLOW result cannot report that "
            "fallback was triggered"
        ),
    ):
        build_logger(
            db_session
        ).record_pipeline_result(
            tenant_id="tenant-1",
            context=build_context(),
            decision=build_decision(),
            result=build_allow_result(),
            fallback_triggered=True,
        )


# ==========================================================
# Immutability Tests
# ==========================================================


def test_audit_record_cannot_be_updated(
    db_session: Session,
) -> None:
    """
    Existing audit records are immutable.
    """

    record = build_logger(
        db_session
    ).record_pipeline_result(
        tenant_id="tenant-1",
        context=build_context(),
        decision=build_decision(),
        result=build_fallback_result(),
        fallback_triggered=True,
    )[0]

    db_session.commit()

    record.severity = "WARNING"

    with pytest.raises(
        ValueError,
        match=(
            "AIGuardrailViolation is immutable"
        ),
    ):
        db_session.flush()

    db_session.rollback()


def test_audit_record_cannot_be_deleted(
    db_session: Session,
) -> None:
    """
    Existing audit records cannot be deleted.
    """

    record = build_logger(
        db_session
    ).record_pipeline_result(
        tenant_id="tenant-1",
        context=build_context(),
        decision=build_decision(),
        result=build_fallback_result(),
        fallback_triggered=True,
    )[0]

    db_session.commit()

    db_session.delete(
        record
    )

    with pytest.raises(
        ValueError,
        match=(
            "AIGuardrailViolation is immutable"
        ),
    ):
        db_session.flush()

    db_session.rollback()