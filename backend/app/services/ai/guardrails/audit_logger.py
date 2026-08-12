"""
audit_logger.py

Immutable database audit logging for FieldOps AI guardrail
violations.

Responsibilities
----------------
- Convert GuardrailPipelineResult violations into database rows
- Create one audit row per violation
- Store only audit-safe metadata
- Fingerprint prompts and outputs using keyed HMAC-SHA256
- Flush records inside the caller's existing transaction

The logger does not:

- Commit or roll back the transaction
- Store raw prompts
- Store raw generated messages
- Store detected PII or prohibited phrases
- Render fallback templates
- Send notifications
"""

from __future__ import annotations

import hashlib
import hmac
import json

from typing import Any, Final

from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import AIGuardrailViolation
from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationContext,
    CommunicationDecision,
)
from app.services.ai.guardrails.contracts import (
    GuardrailPipelineResult,
)


class GuardrailAuditError(RuntimeError):
    """
    Raised when a required guardrail audit record cannot be
    validated or persisted.
    """


class GuardrailAuditLogger:
    """
    Persist immutable audit-safe guardrail violation records.
    """

    AGENT_NAME_MAX_LENGTH: Final[int] = 100

    # ------------------------------------------------------

    def __init__(
        self,
        *,
        db: Session,
        fingerprint_key: str | bytes,
    ) -> None:
        """
        Initialize the audit logger.

        Parameters
        ----------
        db
            Existing SQLAlchemy transaction/session.

        fingerprint_key
            Secret server-side key used to create HMAC-SHA256
            prompt and output fingerprints.

            This should later come from a protected environment
            variable or secrets manager.
        """

        if isinstance(
            fingerprint_key,
            str,
        ):
            normalized_key = fingerprint_key.encode(
                "utf-8"
            )
        else:
            normalized_key = fingerprint_key

        if not normalized_key:
            raise ValueError(
                "fingerprint_key must not be empty."
            )

        self._db = db
        self._fingerprint_key = normalized_key

    # ------------------------------------------------------

    def record_pipeline_result(
        self,
        *,
        tenant_id: str,
        context: CommunicationContext,
        decision: CommunicationDecision,
        result: GuardrailPipelineResult,
        fallback_triggered: bool,
        agent_name: str = "communication_agent",
        prompt_payload: Any | None = None,
    ) -> tuple[AIGuardrailViolation, ...]:
        """
        Persist one immutable row for every pipeline violation.

        Parameters
        ----------
        tenant_id
            Tenant owning the communication request.

        context
            Sanitized CommunicationContext used for generation.

        decision
            AI CommunicationDecision before placeholder
            restoration.

        result
            Final guardrail pipeline result.

        fallback_triggered
            True only after a safe Jinja2 fallback has been
            selected successfully.

        agent_name
            Stable name of the agent that generated the output.

        prompt_payload
            Sanitized complete prompt or provider payload.

            When omitted, the sanitized context is fingerprinted.

            The payload itself is never stored.

        Returns
        -------
        tuple[AIGuardrailViolation, ...]
            Newly created database model objects.

            An ALLOW result returns an empty tuple.
        """

        normalized_tenant_id = tenant_id.strip()
        normalized_agent_name = agent_name.strip()

        if not normalized_tenant_id:
            raise ValueError(
                "tenant_id must not be empty."
            )

        if not normalized_agent_name:
            raise ValueError(
                "agent_name must not be empty."
            )

        if (
            len(normalized_agent_name)
            > self.AGENT_NAME_MAX_LENGTH
        ):
            raise ValueError(
                "agent_name exceeds the supported length."
            )

        if result.passed:
            if fallback_triggered:
                raise ValueError(
                    "An ALLOW result cannot report that "
                    "fallback was triggered."
                )

            return ()

        violation_pairs = tuple(
            (
                check,
                violation,
            )
            for check in result.checks
            for violation in check.violations
        )

        if (
            len(violation_pairs)
            != len(result.violations)
        ):
            raise GuardrailAuditError(
                "Pipeline violation data is inconsistent."
            )

        effective_prompt_payload = (
            context
            if prompt_payload is None
            else prompt_payload
        )

        prompt_hash = self.fingerprint_payload(
            effective_prompt_payload
        )

        output_hash = self.fingerprint_payload(
            decision
        )

        records: list[
            AIGuardrailViolation
        ] = []

        for check, violation in violation_pairs:
            record = AIGuardrailViolation(
                tenant_id=normalized_tenant_id,
                correlation_id=context.correlation_id,
                job_id=str(context.job_id),
                agent_name=normalized_agent_name,
                notification_type=(
                    context.notification_type
                ),
                channel=context.channel,
                checker_name=check.checker_name,
                violation_code=violation.code,
                category=violation.category.value,
                severity=violation.severity.value,
                affected_field=violation.field,
                safe_message=violation.message,
                safe_metadata=dict(
                    violation.safe_metadata
                ),
                pipeline_decision=(
                    result.decision.value
                ),
                fallback_triggered=(
                    fallback_triggered
                ),
                prompt_hash=prompt_hash,
                output_hash=output_hash,
                checker_latency_ms=(
                    check.latency_ms
                ),
                total_latency_ms=(
                    result.total_latency_ms
                ),
            )

            records.append(
                record
            )

        try:
            self._db.add_all(
                records
            )

            # Flush writes inside the caller's transaction.
            # The caller still owns commit and rollback.
            self._db.flush()

        except SQLAlchemyError as exc:
            raise GuardrailAuditError(
                "Guardrail audit records could not be "
                "persisted."
            ) from exc

        return tuple(
            records
        )

    # ------------------------------------------------------

    def fingerprint_payload(
        self,
        payload: Any,
    ) -> str:
        """
        Create a deterministic keyed HMAC-SHA256 fingerprint.

        Equivalent structured payloads produce the same
        fingerprint even when dictionary key order differs.

        The original payload is never returned or stored.
        """

        canonical_payload = self._canonical_json(
            payload
        )

        return hmac.new(
            self._fingerprint_key,
            canonical_payload.encode(
                "utf-8"
            ),
            hashlib.sha256,
        ).hexdigest()

    # ------------------------------------------------------

    @staticmethod
    def _canonical_json(
        payload: Any,
    ) -> str:
        """
        Convert a payload into deterministic JSON.

        Pydantic models are converted using JSON-compatible model
        output before serialization.
        """

        if isinstance(
            payload,
            BaseModel,
        ):
            serializable_payload = payload.model_dump(
                mode="json"
            )
        else:
            serializable_payload = payload

        try:
            return json.dumps(
                serializable_payload,
                sort_keys=True,
                separators=(
                    ",",
                    ":",
                ),
                ensure_ascii=False,
                default=str,
            )

        except (
            TypeError,
            ValueError,
        ) as exc:
            raise GuardrailAuditError(
                "Audit payload could not be fingerprinted."
            ) from exc