from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import NotificationTemplate
from app.services.ai.FieldOpsAI.schemas.prompt_locale import (
    PromptLanguage,
    TemplateTranslationCompleteness,
    TranslationCompletenessIssue,
    TranslationCompletenessResult,
)
from app.services.ai.FieldOpsAI.services.prompt_variable_injector import (
    PromptVariableInjectionError,
    PromptVariableInjector,
)

class InvalidLocaleError(ValueError):
    pass


def normalize_locale(requested_locale: str) -> str:
    """
    Normalizes a locale string to a standard format (e.g. es-MX, en, ta).
    Rejects unsupported languages or badly formatted strings.
    """
    if not isinstance(requested_locale, str):
        raise InvalidLocaleError("Locale must be a string")

    stripped = requested_locale.strip()
    if not stripped:
        raise InvalidLocaleError("Locale cannot be blank")

    if len(stripped) > 10:
        raise InvalidLocaleError("Locale is too long")

    # Accept formats like en, en-US, en_US, en-us
    match = re.fullmatch(r"([a-zA-Z]{2,3})(?:[-_]([a-zA-Z0-9]{2,3}))?", stripped)
    if not match:
        raise InvalidLocaleError("Invalid locale format")

    base_lang = match.group(1).lower()
    
    # Check if the base language is supported
    try:
        PromptLanguage(base_lang)
    except ValueError:
        raise InvalidLocaleError(f"Unsupported base language: {base_lang}")

    region = match.group(2)
    if region:
        return f"{base_lang}-{region.upper()}"
    return base_lang


def locale_candidates(requested_locale: str) -> tuple[str, ...]:
    """
    Returns a deterministic fallback chain for a requested locale.
    E.g. es-MX -> ("es-MX", "es", "en")
    """
    normalized = normalize_locale(requested_locale)
    
    candidates: list[str] = [normalized]
    
    if "-" in normalized:
        base_lang = normalized.split("-")[0]
        candidates.append(base_lang)
        
    if "en" not in candidates:
        candidates.append("en")
        
    return tuple(dict.fromkeys(candidates))


def _get_shape(val: Any) -> Any:
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "boolean"
    if isinstance(val, int):
        return "integer"
    if isinstance(val, float):
        return "number"
    if isinstance(val, str):
        return "string"
    if isinstance(val, list):
        return ["list", [_get_shape(x) for x in val]]
    if isinstance(val, dict):
        return ["object", {k: _get_shape(v) for k, v in sorted(val.items())}]
    return type(val).__name__

def _normalize_declaration(var: str | dict | Any) -> dict:
    if isinstance(var, str):
        return {"name": var, "required": True, "has_default": False, "type": None}

    if hasattr(var, "model_dump"):
        var = var.model_dump()
    elif hasattr(var, "dict") and callable(getattr(var, "dict")):
        var = var.dict()
    elif not isinstance(var, dict) and hasattr(var, "name"):
        var_name = getattr(var, "name")
        var_req = getattr(var, "required", True)
        var_def = getattr(var, "default", None)
        return {
            "name": var_name,
            "required": var_req,
            "has_default": var_def is not None,
            "type": _get_shape(var_def) if var_def is not None else None,
        }

    return {
        "name": var.get("name"),
        "required": var.get("required", True),
        "has_default": "default" in var and var.get("default") is not None,
        "type": _get_shape(var.get("default")) if "default" in var and var.get("default") is not None else None
    }

def validate_translation_parity(en_template: NotificationTemplate, lang_template: NotificationTemplate) -> list[TranslationCompletenessIssue]:
    issues: list[
        TranslationCompletenessIssue
    ] = []

    lang = PromptLanguage(
        lang_template.locale
    )

    injector = PromptVariableInjector()

    # First validate the canonical English template.
    try:
        injector.validate(
            body=en_template.body_template,
            variables=(
                en_template.variables
                or []
            ),
            title=en_template.title_template,
        )

    except PromptVariableInjectionError:
        issues.append(
            TranslationCompletenessIssue(
                language=PromptLanguage.en,
                issue_code=(
                    "TEMPLATE_VALIDATION_FAILED"
                ),
                details=(
                    "Canonical English template "
                    "validation failed."
                ),
            )
        )

        return issues

    # Then validate the translated template.
    try:
        injector.validate(
            body=lang_template.body_template,
            variables=(
                lang_template.variables
                or []
            ),
            title=lang_template.title_template,
        )

    except PromptVariableInjectionError:
        issues.append(
            TranslationCompletenessIssue(
                language=lang,
                issue_code=(
                    "TEMPLATE_VALIDATION_FAILED"
                ),
                details=(
                    "Translated template "
                    "validation failed."
                ),
            )
        )

        return issues

    en_vars = en_template.variables or []
    lang_vars = lang_template.variables or []
    
    en_dict = { _normalize_declaration(v)["name"]: _normalize_declaration(v) for v in en_vars }
    lang_dict = { _normalize_declaration(v)["name"]: _normalize_declaration(v) for v in lang_vars }
    
    missing_vars = set(en_dict.keys()) - set(lang_dict.keys())
    extra_vars = set(lang_dict.keys()) - set(en_dict.keys())
    
    if missing_vars:
        issues.append(TranslationCompletenessIssue(
            language=lang,
            issue_code="VARIABLE_PATH_MISMATCH",
            details=f"Missing variables: {', '.join(missing_vars)}"
        ))
    if extra_vars:
        issues.append(TranslationCompletenessIssue(
            language=lang,
            issue_code="VARIABLE_PATH_MISMATCH",
            details=f"Extra variables: {', '.join(extra_vars)}"
        ))
        
    for name, en_v in en_dict.items():
        lang_v = lang_dict.get(name)
        if not lang_v:
            continue
            
        if en_v["required"] != lang_v["required"]:
            issues.append(TranslationCompletenessIssue(
                language=lang,
                issue_code="REQUIRED_FLAG_MISMATCH",
                details=f"Required flag mismatch for {name}"
            ))
            
        if en_v["has_default"] != lang_v["has_default"]:
            issues.append(TranslationCompletenessIssue(
                language=lang,
                issue_code="DEFAULT_PRESENCE_MISMATCH",
                details=f"Default presence mismatch for {name}"
            ))
            
        if en_v["has_default"] and lang_v["has_default"] and en_v["type"] != lang_v["type"]:
            issues.append(TranslationCompletenessIssue(
                language=lang,
                issue_code="DEFAULT_TYPE_MISMATCH",
                details=f"Default type mismatch for {name}"
            ))
            
    # Also check Jinja paths

    try:
        en_paths = set(injector.infer_declarations(body=en_template.body_template, title=en_template.title_template))
    except PromptVariableInjectionError:
        issues.append(TranslationCompletenessIssue(
            language=PromptLanguage.en,
            issue_code="TEMPLATE_VALIDATION_FAILED",
            details="Canonical English template Jinja syntax is invalid"
        ))
        return issues
        
    try:
        lang_paths = set(injector.infer_declarations(body=lang_template.body_template, title=lang_template.title_template))
    except PromptVariableInjectionError:
        issues.append(TranslationCompletenessIssue(
            language=lang,
            issue_code="TEMPLATE_VALIDATION_FAILED",
            details="Target template Jinja syntax is invalid"
        ))
        return issues
        
    if en_paths != lang_paths:
        issues.append(TranslationCompletenessIssue(
            language=lang,
            issue_code="VARIABLE_PATH_MISMATCH",
            details="Jinja referenced variable paths mismatch"
        ))
        
    return issues


def validate_translation_completeness(
    db: Session,
    tenant_id: str,
    limit: int = 100,
    offset: int = 0,
    agent_type: str | None = None,
    channel: str | None = None,
    status: str | None = None,
) -> TranslationCompletenessResult:
    """
    Validates that notification templates have corresponding translations.
    """
    query = db.query(
        NotificationTemplate.agent_type,
        NotificationTemplate.channel,
        NotificationTemplate.type,
    ).filter(
        NotificationTemplate.tenant_id == tenant_id,
        NotificationTemplate.is_active == True,
        NotificationTemplate.is_deleted == False,
    )
    
    if agent_type:
        query = query.filter(NotificationTemplate.agent_type == agent_type)
    if channel:
        db_channel = "in_app" if channel == "portal" else channel
        query = query.filter(NotificationTemplate.channel == db_channel)
    if status:
        query = query.filter(NotificationTemplate.type == status)
        
    families = query.distinct().order_by(
        NotificationTemplate.agent_type,
        NotificationTemplate.channel,
        NotificationTemplate.type,
    ).all()
    
    all_results: list[TemplateTranslationCompleteness] = []
    complete_count = 0
    incomplete_count = 0
    
    for f_agent_type, f_channel, f_status in families:
        templates = db.query(NotificationTemplate).filter(
            NotificationTemplate.tenant_id == tenant_id,
            NotificationTemplate.agent_type == f_agent_type,
            NotificationTemplate.channel == f_channel,
            NotificationTemplate.type == f_status,
            NotificationTemplate.is_active == True,
            NotificationTemplate.is_deleted == False,
        ).order_by(NotificationTemplate.version.desc(), NotificationTemplate.id.desc()).all()
        
        lang_templates: dict[PromptLanguage, NotificationTemplate] = {}
        for t in templates:
            try:
                if t.locale in PromptLanguage.__members__:
                    lang = PromptLanguage(t.locale)
                    if lang not in lang_templates:
                        lang_templates[lang] = t
            except ValueError:
                pass
                
        en_template = lang_templates.get(PromptLanguage.en)
        
        # Determine base languages exactly ordered
        fixed_order = [PromptLanguage.en, PromptLanguage.es, PromptLanguage.ta, PromptLanguage.hi]
        available = [l for l in fixed_order if l in lang_templates]
        missing = [l for l in fixed_order if l not in lang_templates]
        invalid = []
        issues = []
        template_ids = {l: t.id for l, t in lang_templates.items()}
        versions = {l: t.version for l, t in lang_templates.items()}
        
        if not en_template:
            issues.append(
                TranslationCompletenessIssue(
                    language=PromptLanguage.en,
                    issue_code="MISSING_ENGLISH_BASE",
                    details="Base English template is missing."
                )
            )
            invalid = available.copy()
        else:
            for lang in fixed_order:
                if lang == PromptLanguage.en or lang not in lang_templates:
                    continue
                t = lang_templates[lang]
                parity_issues = validate_translation_parity(en_template, t)
                if parity_issues:
                    issues.extend(parity_issues)
                    invalid.append(lang)
                    
        is_complete = len(missing) == 0 and len(invalid) == 0
        
        if is_complete:
            complete_count += 1
        else:
            incomplete_count += 1
            
        api_channel = "portal" if f_channel == "in_app" else f_channel
            
        all_results.append(
            TemplateTranslationCompleteness(
                agent_type=f_agent_type,
                channel=api_channel,
                status=f_status,
                available_languages=available,
                missing_languages=missing,
                invalid_languages=invalid,
                issues=issues,
                is_complete=is_complete,
                template_ids=template_ids,
                versions=versions
            )
        )
        
    return TranslationCompletenessResult(
        items=all_results[offset:offset+limit],
        total_families=len(families),
        complete_families=complete_count,
        incomplete_families=incomplete_count,
    )

def test_invalid_english_undeclared_variable():
    english = NotificationTemplate(
        locale="en",
        body_template=(
            "Hello {{ customer_name }}"
        ),
        title_template=None,
        variables=[],
    )

    spanish = NotificationTemplate(
        locale="es",
        body_template=(
            "Hola {{ customer_name }}"
        ),
        title_template=None,
        variables=[
            "customer_name",
        ],
    )

    issues = validate_translation_parity(
        english,
        spanish,
    )

    assert any(
        issue.language == PromptLanguage.en
        and issue.issue_code
        == "TEMPLATE_VALIDATION_FAILED"
        for issue in issues
    )

def test_invalid_english_unused_declaration():
    english = NotificationTemplate(
        locale="en",
        body_template="Hello",
        title_template=None,
        variables=[
            "unused_variable",
        ],
    )

    spanish = NotificationTemplate(
        locale="es",
        body_template="Hola",
        title_template=None,
        variables=[],
    )

    issues = validate_translation_parity(
        english,
        spanish,
    )

    assert any(
        issue.language == PromptLanguage.en
        and issue.issue_code
        == "TEMPLATE_VALIDATION_FAILED"
        for issue in issues
    )

def test_invalid_translation_undeclared_variable():
    english = NotificationTemplate(
        locale="en",
        body_template=(
            "Hello {{ customer_name }}"
        ),
        title_template=None,
        variables=[
            "customer_name",
        ],
    )

    spanish = NotificationTemplate(
        locale="es",
        body_template=(
            "Hola {{ customer_name }}"
        ),
        title_template=None,
        variables=[],
    )

    issues = validate_translation_parity(
        english,
        spanish,
        
    )

    assert any(
        issue.language == PromptLanguage.es
        and issue.issue_code
        == "TEMPLATE_VALIDATION_FAILED"
        for issue in issues
    )


def test_invalid_translation_unused_declaration():
    english = NotificationTemplate(
        locale="en",
        body_template="Service update",
        title_template=None,
        variables=[],
    )

    spanish = NotificationTemplate(
        locale="es",
        body_template="Actualización del servicio",
        title_template=None,
        variables=[
            "unused_variable",
        ],
    )

    issues = validate_translation_parity(
        english,
        spanish,
    )

    assert any(
        issue.language == PromptLanguage.es
        and issue.issue_code
        == "TEMPLATE_VALIDATION_FAILED"
        for issue in issues
    )