import hashlib
import json
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from app.services.ai.FieldOpsAI.repositories.prompt_template_repository import (
    PromptTemplateRepository, 
    RepositoryError,
    TemplateConflictError,
    TemplateNotFoundError
)
from app.services.ai.FieldOpsAI.schemas.prompt_template import (
    PromptTemplateCreate,
    PromptTemplateUpdate,
    PromptTemplateResponse,
    PromptTemplateLookupResponse,
    AgentType,
    PromptChannel,
    PromptLanguage,
    _validate_jinja_variables,
    normalize_template_status,
    UnsupportedTemplateStatusError,
    STATUS_LOOKUP_CANDIDATES,
    MessageTemplateStatus,
)
from app.services.ai.FieldOpsAI.services.prompt_locale_service import (
    normalize_locale,
    locale_candidates
)
from pydantic import ValidationError

from app.services.template_version_service import (
    ConflictError as VersionConflictError,
    TemplateNotFoundError as VersionTemplateNotFoundError,
    create_initial_version,
    soft_delete_template,
    update_version,
)

class RegistryServiceError(Exception):
    pass

class ConflictError(RegistryServiceError):
    pass

class NotFoundError(RegistryServiceError):
    pass

class TemplateValidationServiceError(
    RegistryServiceError
):
    """
    Raised when prompt-template validation fails.
    """

    pass
class ManagedPromptTemplateRegistry:

    def __init__(self, db: Session, tenant_id: str, actor_id: str, redis_client: Any, cache_ttl_seconds: int = 60):
        self.CACHE_TTL_SECONDS = 60
        if not tenant_id or not str(tenant_id).strip():
            raise ValueError("Invalid tenant_id")
        if not actor_id or not str(actor_id).strip():
            raise ValueError("Invalid actor_id")
        if cache_ttl_seconds != self.CACHE_TTL_SECONDS:
            raise ValueError("Prompt cache TTL must be 60 seconds.")
        self.db = db
        self.tenant_id = str(tenant_id).strip()
        self.actor_id = str(actor_id).strip()
        self.redis_client = redis_client
        self.cache_ttl_seconds = cache_ttl_seconds
        self.repo = PromptTemplateRepository(db, self.tenant_id)

    def _hash_tenant(self, tenant: str) -> str:
        return hashlib.sha256(tenant.encode('utf-8')).hexdigest()

    def _get_generation(self, tenant_hash: str) -> int:
        try:
            val = self.redis_client.get(f"prompt_gen:{tenant_hash}")
            return int(val) if val else 0
        except Exception:
            return 0

    def _increment_generation(self, tenant_hash: str) -> None:
        try:
            self.redis_client.incr(f"prompt_gen:{tenant_hash}")
        except Exception:
            pass

    def _invalidate_cache(self) -> None:
        tenant_hash = self._hash_tenant(
            self.tenant_id
        )

        self._increment_generation(
            tenant_hash
        )
    def _build_cache_key(self, prefix: str, **kwargs) -> str:
        tenant_hash = self._hash_tenant(self.tenant_id)
        platform_hash = self._hash_tenant("**platform**")
        t_gen = self._get_generation(tenant_hash)
        p_gen = self._get_generation(platform_hash)
        parts = [f"{k}:{v}" for k, v in sorted(kwargs.items())]
        return f"{prefix}:{tenant_hash}:{t_gen}:{platform_hash}:{p_gen}:" + ":".join(parts)

    def _read_from_cache(
        self,
        key: str,
    ) -> Optional[Dict[str, Any]]:
        try:
            cached = self.redis_client.get(key)

            if not cached:
                return None

            return json.loads(cached)

        except (
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ):
            self._delete_cache_key(key)
            return None

        except Exception:
            return None
    def _write_to_cache(self, key: str, data: Dict[str, Any]) -> None:
        try:
            self.redis_client.setex(key, self.cache_ttl_seconds, json.dumps(data))
        except Exception:
            pass

    def validate_variables(self, body: str, variables: List[str], title: Optional[str] = None) -> bool:
        try:
            _validate_jinja_variables(body, variables, title)
            return True
        except ValueError:
            return False

    def create(self, payload: PromptTemplateCreate) -> PromptTemplateResponse:
        # Check for conflicts using natural key identity only.
        # Clients must not be able to choose a version number.
        existing = self.repo.list_templates(
            agent_type=payload.agent_type,
            channel=payload.channel,
            language=payload.language,
            status=payload.status,
            is_active=True
        )
        if existing:
            raise ConflictError("Active template with same attributes already exists.")

        lang_str = payload.language if isinstance(payload.language, str) else payload.language.value
        if lang_str != "en":
            en_templates = self.repo.list_templates(
                agent_type=payload.agent_type,
                channel=payload.channel,
                language="en",
                status=payload.status,
                is_active=True
            )
            if not en_templates:
                raise TemplateValidationServiceError("Tenant-scoped canonical English template is required before creating translations.")
            if en_templates:
                en_template = en_templates[0]
                from app.models import NotificationTemplate
                temp = NotificationTemplate(
                    locale=lang_str,
                    variables=payload.variables or [],
                    body_template=payload.body,
                    title_template=payload.title
                )
                from app.services.ai.FieldOpsAI.services.prompt_locale_service import validate_translation_parity
                issues = validate_translation_parity(en_template, temp)
                if issues:
                    raise TemplateValidationServiceError("Variable contract does not match the canonical English template.")

        try:
            create_data = payload.model_dump(mode="json")
            # Server always assigns version=1 for a new live template.
            # Clients must not be able to control the version number.
            create_data["version"] = 1
            model = self.repo.create(create_data)
            create_initial_version(self.db, model, self.actor_id)
            self.db.commit()
            self._invalidate_cache()
            return self._to_response(model)
        except TemplateConflictError:
            self.db.rollback()
            raise ConflictError("Active template with same attributes and version already exists.")
        except RepositoryError:
            self.db.rollback()
            raise RegistryServiceError("Failed to create template")

    def get(self, template_id: int) -> PromptTemplateResponse:
        cache_key = self._build_cache_key("prompt_get", id=template_id)
        cached = self._read_from_cache(cache_key)
        if cached:
            try:
                return PromptTemplateResponse.model_validate(
                    cached
                )
            except (
                ValidationError,
                ValueError,
                TypeError,
            ):
                self._delete_cache_key(
                    cache_key
                )

        try:
            model = self.repo.get_by_id(template_id)
            if not model:
                raise NotFoundError("Template not found")
            
            resp = self._to_response(model)
            if resp.is_active:
                self._write_to_cache(
                    cache_key,
                    resp.model_dump(mode="json"),
                )
            return resp
        except TemplateNotFoundError:
            raise NotFoundError("Template not found")
        except RepositoryError:
            raise RegistryServiceError("Database error")

    def update(self, template_id: int, payload: PromptTemplateUpdate) -> PromptTemplateResponse:
        try:
            model = self.repo.get_by_id(template_id)
            if not model:
                raise NotFoundError("Template not found")
            
            # Merge logic
            update_dict = payload.model_dump(
                exclude_unset=True,
                mode="json",
            )

            current_data = {
                "name": model.name,
                "agent_type": model.agent_type,
                "channel": "portal" if model.channel == "in_app" else model.channel,
                "language": model.locale,
                "status": model.type,
                "body": model.body_template,
                "title": model.title_template,
                "variables": model.variables,
                "is_active": model.is_active,
                "format": model.format,
            }

            merged_data = {
                **current_data,
                    **update_dict,
            }

            PromptTemplateCreate.model_validate(
                merged_data
            )
            
            if merged_data["language"] != "en":
                en_templates = self.repo.list_templates(
                    agent_type=merged_data["agent_type"],
                    channel=merged_data["channel"],
                    language="en",
                    status=merged_data["status"],
                    is_active=True
                )
                if not en_templates:
                    raise TemplateValidationServiceError("Tenant-scoped canonical English template is required before creating translations.")
                if en_templates:
                    en_template = en_templates[0]
                    from app.models import NotificationTemplate
                    temp = NotificationTemplate(
                        locale=merged_data["language"],
                        variables=merged_data.get("variables") or [],
                        body_template=merged_data.get("body"),
                        title_template=merged_data.get("title")
                    )
                    from app.services.ai.FieldOpsAI.services.prompt_locale_service import validate_translation_parity
                    issues = validate_translation_parity(en_template, temp)
                    if issues:
                        raise TemplateValidationServiceError("Variable contract does not match the canonical English template.")
            
            updated_model = self.repo.update(template_id, update_dict)
            update_version(self.db, updated_model, getattr(payload, 'change_summary', None), self.actor_id, self.tenant_id)
            self.db.commit()
                
            self._invalidate_cache()
            return self._to_response(updated_model)
        except TemplateNotFoundError:
            self.db.rollback()
            raise NotFoundError("Template not found during update")
        except TemplateConflictError:
            self.db.rollback()
            raise ConflictError("Active template with same attributes and version already exists.")
        except ValidationError:
            self.db.rollback()
            raise TemplateValidationServiceError(
                "Template validation failed."
            ) from None
        except RepositoryError:
            self.db.rollback()
            raise RegistryServiceError("Database error")

    def delete(
        self,
        template_id: int,
    ) -> None:
        try:
            soft_delete_template(
                db=self.db,
                template_id=template_id,
                actor_id=self.actor_id,
                tenant_id=self.tenant_id,
            )

            self.db.commit()

            # Cache invalidation must happen only after
            # the database commit succeeds.
            self._invalidate_cache()

        except VersionTemplateNotFoundError:
            self.db.rollback()

            raise NotFoundError(
                "Template not found"
            ) from None

        except VersionConflictError:
            self.db.rollback()

            raise ConflictError(
                "Template deletion conflict"
            ) from None

        except Exception:
            self.db.rollback()

            raise RegistryServiceError(
                "Failed to delete template"
            ) from None

    def list(self, **kwargs) -> List[PromptTemplateResponse]:
        limit = kwargs.pop('limit', 100)
        offset = kwargs.pop('offset', 0)
        try:
            models = self.repo.list_templates(limit=limit, offset=offset, **kwargs)
            return [self._to_response(m) for m in models]
        except RepositoryError:
            raise RegistryServiceError("Database error")

    def find(self, agent_type: str, channel: str, language: str, status: str) -> PromptTemplateLookupResponse:
        from app.services.ai.FieldOpsAI.services.prompt_locale_service import InvalidLocaleError
        try:
            norm_language = normalize_locale(language)
        except InvalidLocaleError:
            raise TemplateValidationServiceError("Invalid locale")

        try:
            norm_status_enum = normalize_template_status(status, allow_default=True)
            norm_status = norm_status_enum.value if hasattr(norm_status_enum, "value") else str(norm_status_enum)
        except UnsupportedTemplateStatusError:
            raise TemplateValidationServiceError("Unsupported template status")

        cache_key = self._build_cache_key("prompt_find", agent_type=agent_type, channel=channel, language=norm_language, status=norm_status)
        cached = self._read_from_cache(cache_key)
        if cached:
            try:
                return PromptTemplateLookupResponse.model_validate(
                    cached
                )
            except (
                ValidationError,
                ValueError,
                TypeError,
            ):
                self._delete_cache_key(
                    cache_key
                )

        try:
            cands = locale_candidates(norm_language)
            if isinstance(norm_status_enum, MessageTemplateStatus) and norm_status_enum in STATUS_LOOKUP_CANDIDATES:
                status_candidates = STATUS_LOOKUP_CANDIDATES[norm_status_enum] + ("default",)
            else:
                status_candidates = ("default",)

            candidates = self.repo.find_active_candidates(agent_type, channel, cands, status_candidates)
            
            match = None
            rules = []
            for t_id in (self.tenant_id, "**platform**"):
                for lang_cand in cands:
                    for stat_cand in status_candidates:
                        rules.append((t_id, lang_cand, stat_cand))

            for t_id, lang, stat in rules:
                for c in candidates:
                    if c.tenant_id == t_id and c.locale == lang and c.type == stat:
                        match = c
                        break
                if match:
                    break

            if match:
                resp = self._to_lookup_response(match)
                self._write_to_cache(cache_key, resp.model_dump(mode='json'))
                return resp

            return PromptTemplateLookupResponse(
                id=None,
                name="builtin_default",
                agent_type=agent_type,
                channel=channel,
                status=norm_status,
                body="builtin_default",
                title="builtin_default",
                variables=[],
                language=language,
                version=None,
                format="text",
                source="builtin_default",
                is_active=True
            )

        except RepositoryError:
            raise RegistryServiceError("Database error")

    def _to_response(self, model: Any) -> PromptTemplateResponse:
        return PromptTemplateResponse(
            id=model.id,
            name=model.name,
            agent_type=AgentType(model.agent_type),
            channel=PromptChannel(model.channel),
            language=model.locale,
            status=model.type,
            body=model.body_template,
            format=model.format,
            title=model.title_template,
            variables=model.variables,
            version=model.version,
            is_active=model.is_active
        )
        
    def _to_lookup_response(self, model: Any) -> PromptTemplateLookupResponse:
        source = "platform" if model.tenant_id == "**platform**" else "tenant"
        return PromptTemplateLookupResponse(
            id=model.id,
            name=model.name,
            agent_type=AgentType(model.agent_type),
            channel=PromptChannel(model.channel),
            language=model.locale,
            status=model.type,
            body=model.body_template,
            format=model.format,
            title=model.title_template,
            variables=model.variables,
            version=model.version,
            is_active=model.is_active,
            source=source
        )
    def _delete_cache_key(
        self,
        key: str,
    ) -> None:
        try:
            self.redis_client.delete(key)
        except Exception:
            pass
