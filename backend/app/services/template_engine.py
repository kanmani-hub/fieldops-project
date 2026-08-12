import os
import re
import time
import jwt
from typing import Any, Container
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from app.services.ai.FieldOpsAI.services.prompt_variable_injector import PromptVariableInjector, PromptVariableInjectionError
from app.services.ai.FieldOpsAI.services.managed_prompt_template_registry import ManagedPromptTemplateRegistry, RegistryServiceError
from app.services.ai.FieldOpsAI.schemas.prompt_variable import PromptVariableDeclaration
from ..logger import logger

class MessageTemplateEngineError(Exception): pass
class MessageTemplateLookupError(MessageTemplateEngineError): pass
class MessageTemplateRenderingError(MessageTemplateEngineError): pass
class UnsupportedTemplateFormatError(MessageTemplateEngineError): pass

class RenderedMessageResult(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    title: str | None
    body: str
    template_id: int | None
    template_version: int | None
    source: str
    template_format: str = "text"
    missing_optional_paths: frozenset[str] = Field(default_factory=frozenset)
    resolved_locale: str | None = None
    requested_status: str | None = None
    resolved_status: str | None = None

_shared_injector = PromptVariableInjector()

def infer_template_declarations(
    *,
    body: str,
    title: str | None = None,
) -> list[str]:
    try:
        return _shared_injector.infer_declarations(body=body, title=title)
    except PromptVariableInjectionError:
        raise MessageTemplateRenderingError("Template declaration inference failed.") from None


def sign_url(
    base_url: str,
    job_id: str,
    tech_id: str,
    action: str,
    expiry: int = 600,
) -> str:
    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise RuntimeError("Action URL signing is unavailable.")
    payload = {
        "job_id": str(job_id),
        "tech_id": str(tech_id),
        "action": action,
        "exp": int(time.time()) + expiry,
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}token={token}"

def get_action_urls(
    job_id: str,
    tech_id: str,
) -> dict[str, str]:
    base_api_url = os.getenv("BASE_API_URL")
    if not base_api_url:
        raise RuntimeError("Action URL base is unavailable.")
    base_api_url = base_api_url.rstrip("/")
    return {
        "accept": sign_url(f"{base_api_url}/jobs/{job_id}/accept", job_id, tech_id, "accept"),
        "reject": sign_url(f"{base_api_url}/jobs/{job_id}/reject", job_id, tech_id, "reject"),
        "reassign": sign_url(f"{base_api_url}/jobs/{job_id}/reassign", job_id, tech_id, "reassign"),
    }

def _enrich_context(context: dict[str, Any]) -> dict[str, Any]:
    render_context = context.copy()
    if "job" in render_context and "tech" in render_context:
        try:
            job_id = render_context["job"].get("id", render_context["job"].get("job_id"))
            tech_id = render_context["tech"].get("id", render_context["tech"].get("tech_id"))
            if job_id and tech_id:
                render_context["action_urls"] = get_action_urls(job_id, tech_id)
        except (AttributeError, RuntimeError):
            pass
    return render_context

def render_managed_template(
    *,
    db: Session,
    tenant_id: str,
    agent_type: str,
    channel: str,
    language: str,
    status: str,
    context: dict[str, Any],
    allowed_variable_paths: Container[str] | None = None,
) -> RenderedMessageResult:
    try:
        from app.services.ai.FieldOpsAI.schemas.prompt_template import normalize_template_status, UnsupportedTemplateStatusError, MessageTemplateStatus
        norm_res = normalize_template_status(status)
        if not isinstance(norm_res, MessageTemplateStatus):
            raise MessageTemplateLookupError("Unsupported message template status.")
        canon_status = norm_res.value
    except UnsupportedTemplateStatusError:
        raise MessageTemplateLookupError("Unsupported message template status.") from None

    try:
        registry = ManagedPromptTemplateRegistry(
            db=db,
            tenant_id=tenant_id,
            actor_id="system_renderer",
            redis_client=None
        )
        template_dto = registry.find(agent_type, channel, language, canon_status)
    except RegistryServiceError:
        raise MessageTemplateLookupError("Template lookup failed.") from None

    if not template_dto or template_dto.source == "builtin_default" or template_dto.id is None:
        raise MessageTemplateLookupError("Template lookup failed.") from None

    # Validate stored declarations against the allowlist if specified
    if allowed_variable_paths is not None:
        for var_decl in (template_dto.variables or []):
            var_name = var_decl["name"] if isinstance(var_decl, dict) else (getattr(var_decl, "name", None) or str(var_decl))
            if var_name not in allowed_variable_paths:
                raise MessageTemplateRenderingError("Template rendering failed.")

    render_context = _enrich_context(context)

    try:
        format_val = getattr(template_dto, "format", "text")
        if format_val not in ("text", "html"):
            raise UnsupportedTemplateFormatError("Template format is unsupported.")
        html = (format_val == "html")
        result = _shared_injector.render(
            body=template_dto.body,
            variables=template_dto.variables or [],
            context=render_context,
            title=template_dto.title,
            html=html
        )
    except PromptVariableInjectionError:
        raise MessageTemplateRenderingError("Template rendering failed.") from None

    # Extract the actual locale selected by the registry so callers
    # can report the resolved locale rather than the requested one.
    _resolved_locale: str | None = None
    if hasattr(template_dto, "language") and template_dto.language is not None:
        _lang = template_dto.language
        _resolved_locale = _lang.value if hasattr(_lang, "value") else str(_lang)

    return RenderedMessageResult(
        title=result.rendered_title,
        body=result.rendered_body,
        template_id=template_dto.id,
        template_version=template_dto.version,
        source=template_dto.source,
        template_format=format_val,
        missing_optional_paths=frozenset(result.missing_optional_paths),
        resolved_locale=_resolved_locale,
        requested_status=canon_status,
        resolved_status=canon_status,
    )

def render_template_source(
    *,
    body: str,
    variables: list[PromptVariableDeclaration],
    context: dict[str, Any],
    title: str | None = None,
    format: str = "text"
) -> RenderedMessageResult:
    render_context = _enrich_context(context)
    if format not in ("text", "html"):
        raise UnsupportedTemplateFormatError("Template format is unsupported.")
    try:
        html = (format == "html")
        result = _shared_injector.render(
            body=body,
            variables=variables,
            context=render_context,
            title=title,
            html=html
        )
    except PromptVariableInjectionError:
        raise MessageTemplateRenderingError("Template rendering failed.") from None

    return RenderedMessageResult(
        title=result.rendered_title,
        body=result.rendered_body,
        template_id=None,
        template_version=None,
        source="preview",
        template_format=format,
        missing_optional_paths=frozenset(result.missing_optional_paths),
        resolved_locale=None,
    )

def render_preview(title_template: str | None, body_template: str, context: dict[str, Any], variables: list | None = None, format: str = "text") -> dict:
    if variables is None:
        try:
            paths = infer_template_declarations(body=body_template, title=title_template)
            variables = [{"name": p, "required": True} for p in paths]
        except PromptVariableInjectionError:
            raise MessageTemplateRenderingError("Template declaration inference failed.") from None

    res = render_template_source(
        body=body_template,
        variables=variables,
        context=context,
        title=title_template,
        format=format
    )
    return {
        "title": res.title,
        "body": res.body
    }

def render_notification(
    db: Session, 
    template_type: str, 
    channel: str, 
    context: dict,
    locale: str = "en"
) -> dict:
    try:
        res = render_managed_template(
            db=db,
            tenant_id="**platform**",
            agent_type="CommsAgent",
            channel=channel,
            language=locale,
            status=template_type,
            context=context
        )
        return {
            "title": res.title,
            "body": res.body
        }
    except MessageTemplateEngineError as e:
        logger.error(f"Template rendering error for {template_type}/{channel}/{locale}: {type(e).__name__}")
        return {
            "title": "System Alert",
            "body": "A new assignment is available. Please open the app."
        }
