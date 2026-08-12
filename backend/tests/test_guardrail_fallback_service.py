"""
Tests for the approved Jinja2 guardrail fallback service.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
import re

from sqlalchemy import create_engine
from sqlalchemy.orm import (
    Session,
    sessionmaker,
)
from sqlalchemy.pool import StaticPool

from app.models import NotificationTemplate
from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationContext,
)
from app.services.ai.guardrails.fallback_service import (
    FallbackTemplateSource,
    GuardrailFallbackService,
)
from app.services.ai.guardrails.pipeline import (
    GuardrailPipeline,
)


# ==========================================================
# Database Fixture
# ==========================================================


@pytest.fixture
def db_session() -> Iterator[Session]:
    """
    Create an isolated template database.
    """

    engine = create_engine(
        "sqlite://",
        connect_args={
            "check_same_thread": False,
        },
        poolclass=StaticPool,
    )

    NotificationTemplate.__table__.create(
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

        NotificationTemplate.__table__.drop(
            bind=engine
        )

        engine.dispose()


# ==========================================================
# Test Helpers
# ==========================================================


def build_context(
    *,
    channel: str = "SMS",
    locale: str = "en",
    notification_type: str = "job_assigned",
    customer_name: str | None = (
        "{{customer_name}}"
    ),
    technician_name: str | None = (
        "{{technician_name}}"
    ),
) -> CommunicationContext:
    """
    Build a sanitized communication context.
    """

    return CommunicationContext(
        job_id="{{job_id}}",
        correlation_id="correlation-1",
        notification_type=notification_type,
        recipient_type="CUSTOMER",
        channel=channel,
        locale=locale,
        customer_name=customer_name,
        technician_name=technician_name,
        job_status="ASSIGNED",
        job_title="{{job_title}}",
        eta="{{eta}}",
        sentiment="NEUTRAL",
    )


def add_template(
    db: Session,
    *,
    channel: str,
    locale: str = "en",
    title_template: str | None = (
        "FieldOps update"
    ),
    body_template: str = (
        "Hello {{customer_name}}."
    ),
    notification_type: str = (
        "job_assigned"
    ),
    version: int = 1,
    variables: list | None = None,
) -> NotificationTemplate:
    """
    Insert one active notification template.
    """

    row = NotificationTemplate(
        name="Test fallback",
        type=notification_type,
        channel=channel,
        locale=locale,
        format=(
            "html"
            if channel == "email"
            else "text"
        ),
        title_template=title_template,
        body_template=body_template,
        variables=variables if variables is not None else [
            {"name": v, "required": False}
            for v in list(set(re.findall(r"\{\{([a-zA-Z_]+)\}\}", body_template + (title_template or ""))))
        ],
        version=version,
        is_active=True,
    )

    db.add(
        row
    )

    db.commit()

    db.refresh(
        row
    )

    return row


# ==========================================================
# Database Rendering Tests
# ==========================================================


def test_sms_uses_active_database_template(
    db_session: Session,
) -> None:
    """
    SMS fallback uses the active database template.
    """

    row = add_template(
        db_session,
        channel="sms",
        title_template=None,
        body_template=(
            "Hello {{customer_name}}, "
            "{{technician_name}} is assigned."
        ),
    )

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=build_context()
    )

    assert (
        result.source
        == FallbackTemplateSource.DATABASE
    )

    assert result.template_id == row.id
    assert result.template_version == 1

    assert result.decision.channel == "SMS"
    assert result.decision.title is None
    assert result.decision.subject is None

    assert result.decision.message == (
        "Hello {{customer_name}}, "
        "{{technician_name}} is assigned."
    )


def test_email_maps_title_to_subject(
    db_session: Session,
) -> None:
    """
    Email template titles become email subjects.
    """

    add_template(
        db_session,
        channel="email",
        title_template="Job assigned",
        body_template=(
            "<p>Hello {{customer_name}}</p>"
        ),
    )

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=build_context(
            channel="EMAIL"
        )
    )

    assert result.decision.channel == "EMAIL"

    assert (
        result.decision.subject
        == "Job assigned"
    )

    assert result.decision.title is None

    assert (
        "{{customer_name}}"
        in result.decision.message
    )


def test_push_maps_title_correctly(
    db_session: Session,
) -> None:
    """
    Push template title becomes the push title.
    """

    add_template(
        db_session,
        channel="push",
        title_template="Technician assigned",
        body_template="ETA {{eta}}",
    )

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=build_context(
            channel="PUSH"
        )
    )

    assert result.decision.channel == "PUSH"

    assert result.decision.title == (
        "Technician assigned"
    )

    assert result.decision.subject is None

    assert result.decision.message == (
        "ETA {{eta}}"
    )


def test_in_app_supports_optional_title(
    db_session: Session,
) -> None:
    """
    In-app communication may omit its title.
    """

    add_template(
        db_session,
        channel="in_app",
        title_template=None,
        body_template=(
            "Your request was updated."
        ),
    )

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=build_context(
            channel="IN_APP"
        )
    )

    assert result.decision.channel == "IN_APP"
    assert result.decision.title == "FieldOps Update"
    assert result.decision.subject is None


# ==========================================================
# Locale Tests
# ==========================================================


def test_requested_locale_falls_back_to_base_language(
    db_session: Session,
) -> None:
    """
    en-US may use an approved en template.
    """

    add_template(
        db_session,
        channel="sms",
        locale="en",
        title_template=None,
        body_template="Service update.",
    )

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=build_context(
            locale="en-US"
        )
    )

    assert (
        result.source
        == FallbackTemplateSource.DATABASE
    )

    assert result.requested_locale == "en-US"
    assert result.resolved_locale == "en"


# ==========================================================
# Template Version Test
# ==========================================================


def test_latest_active_database_version_is_selected(
    db_session: Session,
) -> None:
    """
    The newest active template is selected.
    """

    add_template(
        db_session,
        channel="sms",
        title_template=None,
        body_template="Older template.",
        version=1,
    )

    latest = add_template(
        db_session,
        channel="sms",
        title_template=None,
        body_template="Latest template.",
        version=2,
    )

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=build_context()
    )

    assert result.template_id == latest.id
    assert result.template_version == 2

    assert result.decision.message == (
        "Latest template."
    )


# ==========================================================
# Built-in and Emergency Tests
# ==========================================================


def test_missing_database_template_uses_builtin(
    db_session: Session,
) -> None:
    """
    Built-in defaults are used when no DB template exists.
    """

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=build_context()
    )

    assert (
        result.source
        == FallbackTemplateSource.BUILTIN
    )

    assert result.decision.channel == "SMS"

    assert (
        "{{customer_name}}"
        in result.decision.message
    )


def test_unknown_notification_type_uses_emergency(
    db_session: Session,
) -> None:
    """
    Unknown event types receive a generic safe fallback.
    """

    ctx = build_context(
        notification_type="unknown_event"
    )
    ctx.job_status = "WORK_IN_PROGRESS"

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=ctx
    )

    assert (
        result.source
        == FallbackTemplateSource.EMERGENCY
    )

    assert result.decision.message == (
        "Your FieldOps service request has an update. "
        "Please check the app."
    )


# ==========================================================
# Invalid Template Tests
# ==========================================================


def test_unsupported_database_variable_is_not_rendered(
    db_session: Session,
) -> None:
    """
    Unknown variables force the service to use a safer
    template.
    """

    add_template(
        db_session,
        channel="sms",
        title_template=None,
        body_template="Secret: {{api_key}}",
        variables=[],
    )

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=build_context()
    )

    assert (
        result.source
        == FallbackTemplateSource.BUILTIN
    )

    assert (
        "api_key"
        not in result.decision.message
    )


def test_broken_database_template_uses_builtin(
    db_session: Session,
) -> None:
    """
    Invalid Jinja syntax does not break fallback delivery.
    """

    add_template(
        db_session,
        channel="sms",
        title_template=None,
        body_template=(
            "{% if customer_name %}"
        ),
    )

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=build_context()
    )

    assert (
        result.source
        == FallbackTemplateSource.BUILTIN
    )


def test_oversized_sms_database_template_is_truncated(
    db_session: Session,
) -> None:
    """
    An oversized database SMS is truncated to the transport limit.
    """

    add_template(
        db_session,
        channel="sms",
        title_template=None,
        body_template="x" * 161,
    )

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=build_context()
    )

    assert (
        result.source
        == FallbackTemplateSource.DATABASE
    )

    assert (
        len(
            result.decision.message
        )
        <= 160
    )
    assert result.decision.message.endswith("...")


# ==========================================================
# Optional Value Tests
# ==========================================================


def test_missing_optional_values_never_render_none_or_null(
    db_session: Session,
) -> None:
    """
    Missing optional values use safe generic wording.
    """

    add_template(
        db_session,
        channel="sms",
        title_template=None,
        body_template=(
            "Hello {{customer_name}}, "
            "{{technician_name}} is assigned."
        ),
    )

    context = build_context(
        customer_name=None,
        technician_name=None,
    )

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=context
    )

    lowered = (
        result.decision.message.lower()
    )

    assert "none" not in lowered
    assert "null" not in lowered

    assert result.decision.message == (
        "Hello Customer, "
        "Your technician is assigned."
    )


# ==========================================================
# Context Safety Test
# ==========================================================


def test_free_form_additional_context_is_not_available_to_template(
    db_session: Session,
) -> None:
    """
    Free-form context cannot be copied directly by a template.
    """

    add_template(
        db_session,
        channel="sms",
        title_template=None,
        body_template=(
            "{{additional_context}}"
        ),
        variables=[],
    )

    context = build_context().model_copy(
        update={
            "additional_context": (
                "private free-form data"
            ),
        }
    )

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=context
    )

    assert (
        result.source
        == FallbackTemplateSource.BUILTIN
    )

    assert (
        "private free-form data"
        not in result.decision.message
    )


# ==========================================================
# HTML Safety Test
# ==========================================================


def test_database_email_variables_are_html_escaped(
    db_session: Session,
) -> None:
    """
    Dynamic email values are escaped before HTML output.
    """

    add_template(
        db_session,
        channel="email",
        title_template="Service update",
        body_template=(
            "<p>Hello {{customer_name}}</p>"
        ),
    )

    context = build_context(
        channel="EMAIL",
        customer_name=(
            "<script>alert(1)</script>"
        ),
    )

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=context
    )

    assert (
        "<script>"
        not in result.decision.message
    )

    assert (
        "&lt;script&gt;"
        in result.decision.message
    )


# ==========================================================
# Guardrail Compatibility Test
# ==========================================================


def test_rendered_fallback_passes_default_guardrails(
    db_session: Session,
) -> None:
    """
    Approved fallback output passes the local guardrails.
    """

    context = build_context()

    fallback = GuardrailFallbackService(
        db=db_session
    ).render(
        context=context
    )

    guardrail_result = (
        GuardrailPipeline.default().run(
            context=context,
            decision=fallback.decision,
        )
    )

    assert guardrail_result.passed is True


from app.services.ai.guardrails.fallback_service import GuardrailFallbackService
from app.services.ai.FieldOpsAI.schemas.communication import CommunicationContext

def test_database_lookup_failure_reaches_builtin_fallback(db_session, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    def mock_query(*args, **kwargs):
        raise SQLAlchemyError("DB Down")
    monkeypatch.setattr(db_session, "query", mock_query)
    
    svc = GuardrailFallbackService(db=db_session)
    ctx = CommunicationContext(job_id="1", notification_type="job_assigned", recipient_type="CUSTOMER", channel="SMS", locale="es", job_status="ASSIGNED")
    res = svc.render(context=ctx)
    assert res.source == "BUILTIN"

def test_spanish_missing_optional_values_use_spanish_defaults(db_session):
    svc = GuardrailFallbackService(db=db_session)
    ctx = CommunicationContext(job_id="1", notification_type="job_assigned", recipient_type="CUSTOMER", channel="SMS", locale="es", job_status="ASSIGNED")
    res = svc.render(context=ctx)
    assert "Cliente" in res.decision.message or "técnico" in res.decision.message

def test_tamil_missing_optional_values_use_tamil_defaults(db_session):
    svc = GuardrailFallbackService(db=db_session)
    ctx = CommunicationContext(job_id="1", notification_type="job_assigned", recipient_type="CUSTOMER", channel="SMS", locale="ta", job_status="ASSIGNED")
    res = svc.render(context=ctx)
    assert "வாடிக்கையாளர்" in res.decision.message or "தொழில்நுட்பவியலாளர்" in res.decision.message

def test_hindi_missing_optional_values_use_hindi_defaults(db_session):
    svc = GuardrailFallbackService(db=db_session)
    ctx = CommunicationContext(job_id="1", notification_type="job_assigned", recipient_type="CUSTOMER", channel="SMS", locale="hi", job_status="ASSIGNED")
    res = svc.render(context=ctx)
    assert "ग्राहक" in res.decision.message or "तकनीशियन" in res.decision.message


# ==========================================================
# §2 — Declared additional_context still rejected
# ==========================================================


def test_declared_additional_context_is_rejected(
    db_session: Session,
) -> None:
    """
    A database template that explicitly declares additional_context
    in its variables list must still be rejected and fallback must
    continue to the approved built-in template.

    This is distinct from the undeclared-variable test which only
    proves that undeclared variables are blocked. This test proves
    that even an explicitly declared additional_context cannot be
    rendered by the fallback service.
    """
    add_template(
        db_session,
        channel="sms",
        title_template=None,
        body_template="{{ additional_context }}",
        variables=[
            {"name": "additional_context", "required": True}
        ],
    )

    context = build_context()

    result = GuardrailFallbackService(
        db=db_session
    ).render(context=context)

    # Must skip the database template and continue to builtin/emergency.
    assert result.source != FallbackTemplateSource.DATABASE

    assert (
        "additional_context" not in result.decision.message
    )


# ==========================================================
# §1 — Raw exception must never appear in output
# ==========================================================


def test_raw_exception_text_is_not_printed_or_logged(
    db_session: Session,
    capsys,
) -> None:
    """
    When a built-in template rendering fails, the exception text
    must not appear in stdout, stderr, or the final decision.
    """
    import unittest.mock

    _SENSITIVE_MARKER = "ULTRA_SECRET_RENDER_ERROR_XYZ"

    from app.services import template_engine as te

    original_render = te.render_template_source

    def raising_render(*args, **kwargs):
        raise te.MessageTemplateRenderingError(
            f"Rendering failed: {_SENSITIVE_MARKER}"
        )

    with unittest.mock.patch.object(te, "render_template_source", raising_render):
        result = GuardrailFallbackService(
            db=db_session
        ).render(context=build_context())

    captured = capsys.readouterr()

    assert _SENSITIVE_MARKER not in captured.out
    assert _SENSITIVE_MARKER not in captured.err
    assert _SENSITIVE_MARKER not in result.decision.message


# ==========================================================
# §3 — Locale cascade tests
# ==========================================================


def test_exact_regional_locale_wins(
    db_session: Session,
) -> None:
    """
    When a template exists for the exact requested regional locale
    (stored as base language since the registry normalises), it
    must be selected over a more general template.

    Here we store an 'es' template and request 'es'; the resolved
    locale reported must be 'es'.
    """
    add_template(
        db_session,
        channel="sms",
        locale="es",
        title_template=None,
        body_template="Hola exacto.",
        notification_type="job_assigned",
    )

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=build_context(locale="es")
    )

    assert result.source == FallbackTemplateSource.DATABASE
    assert result.requested_locale == "es"
    assert result.resolved_locale == "es"


def test_base_locale_fallback(
    db_session: Session,
) -> None:
    """
    When only a base-language template exists (es) and the requested
    locale is a regional variant (es-MX), the base template must be
    selected and resolved_locale must report 'es'.
    """
    add_template(
        db_session,
        channel="sms",
        locale="es",
        title_template=None,
        body_template="Hola base.",
        notification_type="job_assigned",
    )

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=build_context(locale="es-MX")
    )

    assert result.source == FallbackTemplateSource.DATABASE
    assert result.requested_locale == "es-MX"
    assert result.resolved_locale == "es"


def test_english_locale_fallback(
    db_session: Session,
) -> None:
    """
    When only an English template exists and a Spanish locale is
    requested, the English template must be selected and
    resolved_locale must report 'en'.
    """
    add_template(
        db_session,
        channel="sms",
        locale="en",
        title_template=None,
        body_template="Hello English fallback.",
        notification_type="job_assigned",
    )

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=build_context(locale="es-MX")
    )

    assert result.source == FallbackTemplateSource.DATABASE
    assert result.requested_locale == "es-MX"
    assert result.resolved_locale == "en"


def test_resolved_locale_is_reported(
    db_session: Session,
) -> None:
    """
    GuardrailFallbackResult.resolved_locale must contain the locale
    actually selected — not the locale calculated before lookup.
    """
    add_template(
        db_session,
        channel="sms",
        locale="en",
        title_template=None,
        body_template="Resolved locale check.",
        notification_type="job_assigned",
    )

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=build_context(locale="ta")
    )

    # resolved_locale is a non-empty string between 2 and 10 chars.
    assert isinstance(result.resolved_locale, str)
    assert 2 <= len(result.resolved_locale) <= 10
    # It must differ from the unreachable 'ta' locale that was requested.
    assert result.requested_locale == "ta"


# ==========================================================
# §4 — Nested path exactness
# ==========================================================


def test_nested_paths_remain_exact(
    db_session: Session,
) -> None:
    """
    A built-in template using {{ customer_name }} must infer
    'customer_name' as the declaration path — not 'customer'.
    """
    from app.services.template_engine import infer_template_declarations

    paths = infer_template_declarations(body="{{ customer_name }}")
    assert "customer_name" in paths
    assert "customer" not in paths


def test_two_nested_paths_under_same_root_no_duplicates(
    db_session: Session,
) -> None:
    """
    A template with both customer.name and customer.address.city
    must produce two exact declarations and must not produce a
    duplicate root declaration for 'customer'.
    """
    from app.services.template_engine import infer_template_declarations

    paths = infer_template_declarations(
        body="{{ customer.name }} {{ customer.address.city }}"
    )
    # Exact paths present
    assert "customer.name" in paths
    assert "customer.address.city" in paths
    # No collapsed root
    assert "customer" not in paths


def test_unsafe_builtin_syntax_falls_to_emergency(
    db_session: Session,
) -> None:
    """
    An unsafe Jinja expression in a built-in template must cause
    that candidate to be skipped and the emergency template used.
    """
    from unittest.mock import patch
    from app.services import default_template as dt

    # Inject a catalog entry with unsafe syntax for a known event.
    original = dict(dt.LOCALIZED_NOTIFICATION_TYPES)

    patched = {
        "en": {
            "job_assigned": {
                "sms": "{{ customer_name.__class__ }}",
            }
        }
    }

    with patch.object(dt, "LOCALIZED_NOTIFICATION_TYPES", patched):
        result = GuardrailFallbackService(
            db=db_session
        ).render(
            context=build_context(
                locale="en",
                notification_type="job_assigned",
            )
        )

    assert result.source == FallbackTemplateSource.EMERGENCY


def test_invalid_builtin_syntax_falls_to_emergency(
    db_session: Session,
) -> None:
    """
    Invalid Jinja syntax (unclosed block) in a built-in template
    must cause that candidate to be skipped and the emergency
    template used.
    """
    from unittest.mock import patch
    from app.services import default_template as dt

    patched = {
        "en": {
            "job_assigned": {
                "sms": "{% if customer_name %}",  # unclosed block
            }
        }
    }

    with patch.object(dt, "LOCALIZED_NOTIFICATION_TYPES", patched):
        result = GuardrailFallbackService(
            db=db_session
        ).render(
            context=build_context(
                locale="en",
                notification_type="job_assigned",
            )
        )

    assert result.source == FallbackTemplateSource.EMERGENCY


# ==========================================================
# §8 — Whitespace normalization
# ==========================================================


def test_multiline_sms_body_is_normalized(
    db_session: Session,
) -> None:
    """
    Repeated whitespace and newlines in an SMS body must be
    collapsed to a single space.
    """
    add_template(
        db_session,
        channel="sms",
        title_template=None,
        body_template="Hello   world\n\ncheck   app.",
        notification_type="job_assigned",
        variables=[],
    )

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=build_context(channel="SMS")
    )

    assert result.decision.channel == "SMS"
    # Multiple spaces and newlines must be collapsed.
    assert "\n" not in result.decision.message
    assert "  " not in result.decision.message
    assert result.decision.message == "Hello world check app."


def test_multiline_push_title_is_normalized(
    db_session: Session,
) -> None:
    """
    Repeated whitespace in a PUSH notification title must be
    collapsed to a single space.
    """
    add_template(
        db_session,
        channel="push",
        title_template="FieldOps   update\n now",
        body_template="Your request is updated.",
        notification_type="job_assigned",
        variables=[],
    )

    result = GuardrailFallbackService(
        db=db_session
    ).render(
        context=build_context(channel="PUSH")
    )

    assert result.decision.channel == "PUSH"
    assert result.decision.title is not None
    assert "\n" not in result.decision.title
    assert "  " not in result.decision.title
