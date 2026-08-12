"""
Tests for the production communication integration adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.context import correlation_id_ctx
from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationContext,
    CommunicationDecision,
)
from app.services.ai.FieldOpsAI.services.communication_service import (
    CommunicationServiceResult,
)
from app.services.ai.guardrails.contracts import (
    GuardrailPipelineResult,
)
from app.services.ai.integrations.communication_integration import (
    CommunicationIntegration,
    CommunicationIntegrationError,
)


# ==========================================================
# Test Doubles
# ==========================================================


@dataclass
class FakeJobStatusEvent:
    """
    Minimal event matching JobStatusEventLike.
    """

    job_id: str = "101"
    tenant_id: str = "tenant-123"

    to_status: str = "EN_ROUTE"

    job_title: str = "Leak Repair"

    technician_name: str | None = "John Tech"
    customer_name: str | None = "Alice"

    eta: str | None = "15 minutes"


class FakeSession:
    """
    Small database-session replacement.
    """

    def __init__(
        self,
    ) -> None:
        self.closed = False

    def close(
        self,
    ) -> None:
        self.closed = True


class FakeCommunicationService:
    """
    Controlled replacement for CommunicationService.
    """

    def __init__(
        self,
        *,
        result: CommunicationServiceResult,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error

        self.received_context: (
            CommunicationContext
            | None
        ) = None

    def generate(
        self,
        *,
        context: CommunicationContext,
    ) -> CommunicationServiceResult:
        self.received_context = context

        if self.error is not None:
            raise self.error

        return self.result


class FakeServiceFactory:
    """
    Records trusted service-construction arguments.
    """

    def __init__(
        self,
        *,
        result: CommunicationServiceResult,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error

        self.db: Any | None = None
        self.tenant_id: str | None = None
        self.redis_client: Any | None = None

        self.service: (
            FakeCommunicationService
            | None
        ) = None

    def __call__(
        self,
        *,
        db,
        tenant_id: str,
        redis_client=None,
    ) -> FakeCommunicationService:
        self.db = db
        self.tenant_id = tenant_id
        self.redis_client = redis_client

        self.service = FakeCommunicationService(
            result=self.result,
            error=self.error,
        )

        return self.service


# ==========================================================
# Helpers
# ==========================================================


def build_result(
    *,
    channel: str = "SMS",
) -> CommunicationServiceResult:
    """
    Build one successful production-service result.
    """

    if channel == "EMAIL":
        decision = CommunicationDecision(
            channel="EMAIL",
            title=None,
            subject="Technician en route",
            message=(
                "<p>Your technician is on the way.</p>"
            ),
            tone="PROFESSIONAL",
            confidence=1.0,
        )

    elif channel == "PUSH":
        decision = CommunicationDecision(
            channel="PUSH",
            title="Technician en route",
            subject=None,
            message=(
                "Your technician is on the way."
            ),
            tone="PROFESSIONAL",
            confidence=1.0,
        )

    elif channel == "IN_APP":
        decision = CommunicationDecision(
            channel="IN_APP",
            title="Technician en route",
            subject=None,
            message=(
                "Your technician is on the way."
            ),
            tone="PROFESSIONAL",
            confidence=1.0,
        )

    else:
        decision = CommunicationDecision(
            channel="SMS",
            title=None,
            subject=None,
            message=(
                "Your technician is on the way."
            ),
            tone="PROFESSIONAL",
            confidence=1.0,
        )

    guardrail_result = (
        GuardrailPipelineResult.from_checks(
            checks=(),
            total_latency_ms=0.0,
        )
    )

    return CommunicationServiceResult(
        decision=decision,
        used_fallback=False,
        fallback_source=None,
        fallback_template_id=None,
        fallback_template_version=None,
        guardrail_result=guardrail_result,
        audit_record_count=0,
    )


# ==========================================================
# Tests
# ==========================================================


@pytest.mark.anyio
async def test_integration_builds_customer_sms_context(
) -> None:
    """
    Existing event fields become a validated communication
    context.
    """

    session = FakeSession()
    redis = object()

    factory = FakeServiceFactory(
        result=build_result()
    )

    integration = CommunicationIntegration(
        session_factory=lambda: session,  # type: ignore[arg-type]
        redis_client=redis,
        service_factory=factory,  # type: ignore[arg-type]
    )

    token = correlation_id_ctx.set(
        "correlation-123"
    )

    try:
        result = await integration.generate(
            event=FakeJobStatusEvent(),
            recipient_type="customer",
            channel="sms",
            notification_type=(
                "technician_en_route"
            ),
        )

    finally:
        correlation_id_ctx.reset(
            token
        )

    assert result.decision.channel == "SMS"

    assert factory.tenant_id == "tenant-123"
    assert factory.redis_client is redis
    assert factory.db is session

    assert factory.service is not None

    context = (
        factory.service.received_context
    )

    assert context is not None

    assert context.job_id == "101"

    assert (
        context.correlation_id
        == "correlation-123"
    )

    assert (
        context.notification_type
        == "technician_en_route"
    )

    assert context.recipient_type == "CUSTOMER"
    assert context.channel == "SMS"

    assert context.customer_name == "Alice"

    assert (
        context.technician_name
        == "John Tech"
    )

    assert context.job_status == "EN_ROUTE"
    assert context.job_title == "Leak Repair"
    assert context.eta == "15 minutes"

    assert session.closed is True


@pytest.mark.anyio
async def test_integration_normalizes_in_app_channel(
) -> None:
    """
    Existing lower-case in_app values become IN_APP.
    """

    session = FakeSession()

    factory = FakeServiceFactory(
        result=build_result(
            channel="IN_APP"
        )
    )

    integration = CommunicationIntegration(
        session_factory=lambda: session,  # type: ignore[arg-type]
        redis_client=object(),
        service_factory=factory,  # type: ignore[arg-type]
    )

    result = await integration.generate(
        event=FakeJobStatusEvent(),
        recipient_type="dispatcher",
        channel="in_app",
        notification_type="dispatcher_en_route",
    )

    assert result.decision.channel == "IN_APP"

    assert factory.service is not None

    context = (
        factory.service.received_context
    )

    assert context is not None
    assert context.channel == "IN_APP"

    assert (
        context.recipient_type
        == "DISPATCHER"
    )

    assert session.closed is True


@pytest.mark.anyio
async def test_blank_optional_values_become_none(
) -> None:
    """
    Blank names and ETA values do not enter the schema.
    """

    session = FakeSession()

    factory = FakeServiceFactory(
        result=build_result()
    )

    event = FakeJobStatusEvent(
        customer_name=" ",
        technician_name="",
        eta=None,
    )

    integration = CommunicationIntegration(
        session_factory=lambda: session,  # type: ignore[arg-type]
        redis_client=object(),
        service_factory=factory,  # type: ignore[arg-type]
    )

    await integration.generate(
        event=event,
        recipient_type="customer",
        channel="sms",
        notification_type=(
            "technician_en_route"
        ),
    )

    assert factory.service is not None

    context = (
        factory.service.received_context
    )

    assert context is not None
    assert context.customer_name is None
    assert context.technician_name is None
    assert context.eta is None


@pytest.mark.anyio
async def test_invalid_job_status_is_rejected_safely(
) -> None:
    """
    Unsupported statuses do not reach CommunicationService.
    """

    factory = FakeServiceFactory(
        result=build_result()
    )

    integration = CommunicationIntegration(
        session_factory=FakeSession,  # type: ignore[arg-type]
        redis_client=object(),
        service_factory=factory,  # type: ignore[arg-type]
    )

    event = FakeJobStatusEvent(
        to_status="INVALID_STATUS"
    )

    with pytest.raises(
        CommunicationIntegrationError,
        match="valid communication context",
    ):
        await integration.generate(
            event=event,
            recipient_type="customer",
            channel="sms",
            notification_type=(
                "technician_en_route"
            ),
        )

    assert factory.service is None


@pytest.mark.anyio
async def test_unsupported_channel_is_rejected(
) -> None:
    """
    Unknown delivery channels are rejected before generation.
    """

    factory = FakeServiceFactory(
        result=build_result()
    )

    integration = CommunicationIntegration(
        session_factory=FakeSession,  # type: ignore[arg-type]
        redis_client=object(),
        service_factory=factory,  # type: ignore[arg-type]
    )

    with pytest.raises(
        CommunicationIntegrationError,
        match="channel is not supported",
    ):
        await integration.generate(
            event=FakeJobStatusEvent(),
            recipient_type="customer",
            channel="fax",
            notification_type=(
                "technician_en_route"
            ),
        )

    assert factory.service is None


@pytest.mark.anyio
async def test_service_failure_is_wrapped_and_session_closes(
) -> None:
    """
    Provider, guardrail, fallback, or database failures produce
    a safe integration error.
    """

    session = FakeSession()

    factory = FakeServiceFactory(
        result=build_result(),
        error=RuntimeError(
            "Sensitive internal provider details."
        ),
    )

    integration = CommunicationIntegration(
        session_factory=lambda: session,  # type: ignore[arg-type]
        redis_client=object(),
        service_factory=factory,  # type: ignore[arg-type]
    )

    with pytest.raises(
        CommunicationIntegrationError,
        match="Safe communication could not be generated",
    ) as captured:
        await integration.generate(
            event=FakeJobStatusEvent(),
            recipient_type="customer",
            channel="sms",
            notification_type=(
                "technician_en_route"
            ),
        )

    assert (
        "Sensitive internal provider details"
        not in str(
            captured.value
        )
    )

    assert session.closed is True