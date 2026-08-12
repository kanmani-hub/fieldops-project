import logging
from typing import List, Optional, Tuple, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from app.models import NotificationTemplate

class RepositoryError(Exception):
    """Base exception for repository errors."""
    pass

class TemplateConflictError(RepositoryError):
    pass

class TemplateNotFoundError(RepositoryError):
    pass

class TemplateValidationError(RepositoryError):
    pass

class PromptTemplateRepository:
    def __init__(self, db: Session, tenant_id: str):
        self.db = db
        self.tenant_id = tenant_id

    def create(self, template_data: Dict[str, Any]) -> NotificationTemplate:
        template_data = dict(template_data)
        try:
            # Ensure in_app is used for DB instead of portal
            if template_data.get("channel") == "portal":
                template_data["channel"] = "in_app"

            template_data["tenant_id"] = self.tenant_id

            template = NotificationTemplate(
                name=template_data["name"],
                type=template_data["status"],
                channel=template_data["channel"],
                locale=template_data["language"],
                format=template_data.get("format", "text"),
                title_template=template_data.get("title"),
                body_template=template_data["body"],
                variables=template_data.get("variables", []),
                version=template_data.get("version", 1),
                is_active=template_data.get("is_active", True),
                tenant_id=template_data["tenant_id"],
                agent_type=template_data["agent_type"]
            )
            self.db.add(template)
            self.db.flush()
            return template
        except IntegrityError:
            raise TemplateConflictError("Template already exists and conflicts with uniqueness constraints.") from None
        except SQLAlchemyError as e:
            raise RepositoryError("Failed to create template.") from None

    def get_by_id(self, template_id: int) -> Optional[NotificationTemplate]:
        try:
            return self.db.query(NotificationTemplate).filter(
                NotificationTemplate.id == template_id,
                NotificationTemplate.tenant_id == self.tenant_id,
                NotificationTemplate.is_deleted == False
            ).first()
        except SQLAlchemyError:
            raise RepositoryError("Failed to retrieve template.") from None

    def update(self, template_id: int, update_data: Dict[str, Any]) -> Optional[NotificationTemplate]:
        try:
            template = self.get_by_id(template_id)
            if not template:
                raise TemplateNotFoundError("Template not found.")
            if "agent_type" in update_data:
                template.agent_type = update_data["agent_type"]
            if "status" in update_data:
                template.type = update_data["status"]
            if "channel" in update_data:
                template.channel = "in_app" if update_data["channel"] == "portal" else update_data["channel"]
            if "language" in update_data:
                template.locale = update_data["language"]
            if "name" in update_data:
                template.name = update_data["name"]
            if "body" in update_data:
                template.body_template = update_data["body"]
            if "title" in update_data:
                template.title_template = update_data["title"]
            if "variables" in update_data:
                template.variables = update_data["variables"]
            if "is_active" in update_data:
                template.is_active = update_data["is_active"]
            if "format" in update_data:
                template.format = update_data["format"]
                
            self.db.flush()
            return template
        except IntegrityError:
            raise TemplateConflictError("Template update conflicts with uniqueness constraints.") from None
        except SQLAlchemyError:
            raise RepositoryError("Failed to update template.") from None

    def deactivate(self, template_id: int) -> bool:
        try:
            template = self.get_by_id(template_id)
            if not template:
                raise TemplateNotFoundError("Template not found.")
            template.is_active = False
            self.db.flush()
            return True
        except SQLAlchemyError:
            raise RepositoryError("Failed to deactivate template.") from None

    def list_templates(
        self,
        agent_type: Optional[str] = None,
        channel: Optional[str] = None,
        language: Optional[str] = None,
        status: Optional[str] = None,
        is_active: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[NotificationTemplate]:
        try:
            query = self.db.query(NotificationTemplate).filter(
                NotificationTemplate.tenant_id == self.tenant_id,
                NotificationTemplate.is_deleted == False
            )
            
            if agent_type:
                query = query.filter(NotificationTemplate.agent_type == agent_type)
            if channel:
                db_channel = "in_app" if channel == "portal" else channel
                query = query.filter(NotificationTemplate.channel == db_channel)
            if language:
                query = query.filter(NotificationTemplate.locale == language)
            if status:
                query = query.filter(NotificationTemplate.type == status)
            if is_active is not None:
                query = query.filter(NotificationTemplate.is_active == is_active)

            # Deterministic newest-first ordering
            query = query.order_by(NotificationTemplate.version.desc(), NotificationTemplate.id.desc())
            
            return query.offset(offset).limit(limit).all()
        except SQLAlchemyError:
            raise RepositoryError("Failed to list templates.") from None

    def find_active_candidates(
        self,
        agent_type: str,
        channel: str,
        locales: Tuple[str, ...],
        status: str | Tuple[str, ...] | List[str]
    ) -> List[NotificationTemplate]:
        """
        Returns active candidates for both tenant and platform fallback matching criteria.
        Ordered to support deterministic fallback resolution by the service.
        """
        try:
            db_channel = "in_app" if channel == "portal" else channel
            if isinstance(status, str):
                status_list = [status]
            else:
                status_list = list(status)
            if "default" not in status_list:
                status_list.append("default")
            
            candidates = self.db.query(NotificationTemplate).filter(
                NotificationTemplate.tenant_id.in_([self.tenant_id, "**platform**"]),
                NotificationTemplate.agent_type == agent_type,
                NotificationTemplate.channel == db_channel,
                NotificationTemplate.is_active == True,
                NotificationTemplate.is_deleted == False,
                NotificationTemplate.locale.in_(locales),
                NotificationTemplate.type.in_(status_list)
            ).order_by(NotificationTemplate.version.desc(), NotificationTemplate.id.desc()).all()
            
            return candidates
        except SQLAlchemyError:
            raise RepositoryError("Failed to find active templates.") from None
