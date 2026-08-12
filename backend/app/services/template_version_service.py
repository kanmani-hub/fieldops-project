from datetime import datetime, timezone
from typing import Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.models import NotificationTemplate, TemplateVersion

class TemplateVersionError(Exception):
    pass

class TemplateNotFoundError(TemplateVersionError):
    pass

class VersionNotFoundError(TemplateVersionError):
    pass

class ConflictError(TemplateVersionError):
    pass

class AuthorizationError(TemplateVersionError):
    pass

def normalize_snapshot(template):
    """
    Normalize template and version fields so snapshots
    can be compared consistently.
    """

    channel = getattr(
        template,
        "channel",
        None,
    )

    if channel == "portal":
        normalized_channel = "in_app"
    elif channel:
        normalized_channel = (
            channel.strip().lower()
        )
    else:
        normalized_channel = None

    variables = getattr(
        template,
        "variables",
        None,
    )

    return {
        "name": (
            template.name.strip()
            if getattr(
                template,
                "name",
                None,
            )
            else None
        ),
        "type": (
            template.type.strip().lower()
            if getattr(
                template,
                "type",
                None,
            )
            else None
        ),
        "channel": normalized_channel,
        "locale": (
            template.locale.strip().lower()
            if getattr(
                template,
                "locale",
                None,
            )
            else None
        ),
        "format": (
            template.format.strip().lower()
            if getattr(
                template,
                "format",
                None,
            )
            else None
        ),
        "agent_type": (
            template.agent_type.strip()
            if getattr(
                template,
                "agent_type",
                None,
            )
            else None
        ),
        "variables": (
            sorted(variables)
            if variables
            else []
        ),
        "title_template": (
            template.title_template.strip()
            if getattr(
                template,
                "title_template",
                None,
            )
            else None
        ),
        "body_template": (
            template.body_template.strip()
            if getattr(
                template,
                "body_template",
                None,
            )
            else None
        ),
        "is_active": bool(
            getattr(
                template,
                "is_active",
                False,
            )
        ),
    }

    return {
        "name": _normalize_string(template.name),
        "type": _normalize_string(template.type),
        "channel": _normalize_string(channel),
        "locale": _normalize_string(template.locale),
        "format": _normalize_string(template.format),
        "agent_type": _normalize_string(template.agent_type),
        "variables": variables,
        "title_template": _normalize_string(template.title_template),
        "body_template": _normalize_string(template.body_template),
        "is_active": bool(template.is_active)
    }

def create_initial_version(db: Session, template: NotificationTemplate, created_by: str) -> TemplateVersion:
    """
    Create the initial version (v1) snapshot from a newly created template.
    """
    existing = db.query(TemplateVersion).filter(TemplateVersion.template_id == template.id).first()
    if existing:
        return existing

    version = TemplateVersion(
        template_id=template.id,
        version_number=1,
        name=template.name,
        type=template.type,
        channel=template.channel,
        locale=template.locale,
        format=template.format,
        agent_type=template.agent_type,
        variables=template.variables,
        title_template=template.title_template,
        body_template=template.body_template,
        created_by=created_by,
        change_summary="Initial version",
        is_active=True,
        template_is_active=template.is_active,
        is_deleted=False
    )
    db.add(version)
    
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise ConflictError("Version conflict occurred.") from e
        
    return version

def update_version(
    db: Session,
    template: NotificationTemplate,
    change_summary: Optional[str],
    actor_id: str,
    tenant_id: str
) -> Optional[TemplateVersion]:
    """
    Create a subsequent version if there are changes.
    Returns the new version or None if it was a no-op.
    """
    locked_template = db.query(NotificationTemplate).with_for_update().filter(
        NotificationTemplate.id == template.id,
        NotificationTemplate.tenant_id == tenant_id,
        NotificationTemplate.is_deleted == False
    ).first()
    
    if not locked_template:
        raise TemplateNotFoundError("Template not found or deleted")

    active_version = db.query(TemplateVersion).filter(
        TemplateVersion.template_id == template.id,
        TemplateVersion.is_active == True,
        TemplateVersion.is_deleted == False
    ).first()

    latest = db.query(TemplateVersion).filter(
        TemplateVersion.template_id == template.id
    ).order_by(TemplateVersion.version_number.desc()).first()

    next_version_num = (latest.version_number + 1) if latest else 1

    if active_version:
        old_normalized = normalize_snapshot(active_version)
        new_normalized = normalize_snapshot(template)
        
        # Use template_is_active of active_version for comparison, falling back to old active status
        old_active_status = active_version.template_is_active if active_version.template_is_active is not None else old_normalized["is_active"]
        
        is_noop = (
            old_normalized["name"] == new_normalized["name"] and
            old_normalized["type"] == new_normalized["type"] and
            old_normalized["channel"] == new_normalized["channel"] and
            old_normalized["locale"] == new_normalized["locale"] and
            old_normalized["format"] == new_normalized["format"] and
            old_normalized["agent_type"] == new_normalized["agent_type"] and
            old_normalized["variables"] == new_normalized["variables"] and
            old_normalized["title_template"] == new_normalized["title_template"] and
            old_normalized["body_template"] == new_normalized["body_template"] and
            old_active_status == new_normalized["is_active"]
        )
        if is_noop:
            return None

    db.query(TemplateVersion).filter(
        TemplateVersion.template_id == template.id,
        TemplateVersion.is_active == True
    ).update({"is_active": False})

    if not change_summary:
        change_summary = f"Version {next_version_num}"
    
    if len(change_summary) > 500:
        change_summary = change_summary[:497] + "..."

    version = TemplateVersion(
        template_id=template.id,
        version_number=next_version_num,
        name=template.name,
        type=template.type,
        channel=template.channel,
        locale=template.locale,
        format=template.format,
        agent_type=template.agent_type,
        variables=template.variables,
        title_template=template.title_template,
        body_template=template.body_template,
        created_by=actor_id,
        change_summary=change_summary,
        is_active=True,
        template_is_active=template.is_active,
        is_deleted=False
    )

    db.add(version)
    locked_template.version = next_version_num
    
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise ConflictError("Version allocation conflict occurred.") from e
        
    return version

def get_versions(
    db: Session,
    template_id: int,
    tenant_id: str,
    limit: int = 50,
    offset: int = 0
):
    template = db.query(NotificationTemplate).filter(
        NotificationTemplate.id == template_id,
        NotificationTemplate.tenant_id == tenant_id,
        NotificationTemplate.is_deleted == False
    ).first()

    if not template:
        raise TemplateNotFoundError("Template not found")

    versions = db.query(TemplateVersion).filter(
        TemplateVersion.template_id == template_id,
        TemplateVersion.is_deleted == False
    ).order_by(TemplateVersion.version_number.desc()).offset(offset).limit(limit).all()

    return versions

def get_current_version(
    db: Session,
    template_id: int,
    tenant_id: str
) -> int:
    template = db.query(NotificationTemplate).filter(
        NotificationTemplate.id == template_id,
        NotificationTemplate.tenant_id == tenant_id,
        NotificationTemplate.is_deleted == False
    ).first()
    if not template:
        raise TemplateNotFoundError("Template not found")
    return template.version

def get_version(
    db: Session,
    template_id: int,
    version_number: int,
    tenant_id: str
) -> TemplateVersion:
    template = db.query(NotificationTemplate).filter(
        NotificationTemplate.id == template_id,
        NotificationTemplate.tenant_id == tenant_id,
        NotificationTemplate.is_deleted == False
    ).first()

    if not template:
        raise TemplateNotFoundError("Template not found")

    version = db.query(TemplateVersion).filter(
        TemplateVersion.template_id == template_id,
        TemplateVersion.version_number == version_number,
        TemplateVersion.is_deleted == False
    ).first()

    if not version:
        raise VersionNotFoundError("Version not found")

    return version

def restore_version(
    db: Session,
    template_id: int,
    version_number: int,
    actor_id: str,
    tenant_id: str,
    change_summary: Optional[str] = None
) -> dict:
    locked_template = db.query(NotificationTemplate).with_for_update().filter(
        NotificationTemplate.id == template_id,
        NotificationTemplate.tenant_id == tenant_id,
        NotificationTemplate.is_deleted == False
    ).first()

    if not locked_template:
        raise TemplateNotFoundError("Template not found")

    version_to_restore = db.query(TemplateVersion).filter(
        TemplateVersion.template_id == template_id,
        TemplateVersion.version_number == version_number,
        TemplateVersion.is_deleted == False
    ).first()

    if not version_to_restore:
        raise VersionNotFoundError("Requested version not found")

    # Find the currently active version to record previous version metadata
    current_active = db.query(TemplateVersion).filter(
        TemplateVersion.template_id == template_id,
        TemplateVersion.is_active == True,
        TemplateVersion.is_deleted == False
    ).first()
    
    previous_version_number = current_active.version_number if current_active else locked_template.version

    latest = db.query(TemplateVersion).filter(
        TemplateVersion.template_id == template_id
    ).order_by(TemplateVersion.version_number.desc()).first()

    next_version = (latest.version_number + 1) if latest else 1

    db.query(TemplateVersion).filter(
        TemplateVersion.template_id == template_id,
        TemplateVersion.is_active == True
    ).update({"is_active": False})

    if not change_summary:
        change_summary = f"Rollback to version {version_number}"
    elif len(change_summary) > 500:
        change_summary = change_summary[:497] + "..."

    restored = TemplateVersion(
        template_id=template_id,
        version_number=next_version,
        name=version_to_restore.name,
        type=version_to_restore.type,
        channel=version_to_restore.channel,
        locale=version_to_restore.locale,
        format=version_to_restore.format,
        agent_type=version_to_restore.agent_type,
        variables=version_to_restore.variables,
        title_template=version_to_restore.title_template,
        body_template=version_to_restore.body_template,
        created_by=actor_id,
        change_summary=change_summary,
        is_active=True,
        template_is_active=version_to_restore.template_is_active if version_to_restore.template_is_active is not None else version_to_restore.is_active,
        restored_from_version=version_number,
        is_deleted=False
    )
    db.add(restored)

    # Restore main template fields
    locked_template.version = next_version
    locked_template.name = restored.name
    locked_template.type = restored.type
    locked_template.channel = restored.channel
    locked_template.locale = restored.locale
    locked_template.format = restored.format
    locked_template.agent_type = restored.agent_type
    locked_template.variables = restored.variables
    locked_template.title_template = restored.title_template
    locked_template.body_template = restored.body_template
    locked_template.is_active = restored.template_is_active

    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise ConflictError("Version allocation conflict occurred during rollback.") from e

    return {
        "template_id": template_id,
        "previous_version": previous_version_number,
        "restored_version": version_number,
        "new_active_version": next_version,
        "restored_by": actor_id,
        "restored_at": datetime.now(timezone.utc),
    }

def compare_versions(
    db: Session,
    template_id: int,
    old_version: int,
    new_version: int,
    tenant_id: str
) -> dict:
    template = db.query(NotificationTemplate).filter(
        NotificationTemplate.id == template_id,
        NotificationTemplate.tenant_id == tenant_id,
        NotificationTemplate.is_deleted == False
    ).first()

    if not template:
        raise TemplateNotFoundError("Template not found")

    old = db.query(TemplateVersion).filter(
        TemplateVersion.template_id == template_id,
        TemplateVersion.version_number == old_version,
        TemplateVersion.is_deleted == False
    ).first()

    if not old:
        raise VersionNotFoundError(f"Version {old_version} not found")

    new = db.query(TemplateVersion).filter(
        TemplateVersion.template_id == template_id,
        TemplateVersion.version_number == new_version,
        TemplateVersion.is_deleted == False
    ).first()

    if not new:
        raise VersionNotFoundError(f"Version {new_version} not found")

    changes = {}
    
    # Compare fields
    fields_to_compare = [
        "name", "type", "channel", "locale", "format", 
        "agent_type", "variables", "title_template", "body_template",
        "template_is_active"
    ]
    
    for field in fields_to_compare:
        old_val = getattr(old, field)
        new_val = getattr(new, field)
        
        # Fallback for old templates without template_is_active explicitly populated
        if field == "template_is_active":
            if old_val is None: old_val = old.is_active
            if new_val is None: new_val = new.is_active

        if old_val != new_val:
            changes[field] = {
                "old": old_val,
                "new": new_val
            }

    return {
        "template_id": template_id,
        "old_version": old.version_number,
        "new_version": new.version_number,
        "changes": changes
    }

def delete_version(
    db: Session,
    template_id: int,
    version_number: int,
    actor_id: str,
    tenant_id: str
):
    template = db.query(NotificationTemplate).filter(
        NotificationTemplate.id == template_id,
        NotificationTemplate.tenant_id == tenant_id,
        NotificationTemplate.is_deleted == False
    ).first()

    if not template:
        raise TemplateNotFoundError("Template not found")

    version = db.query(TemplateVersion).filter(
        TemplateVersion.template_id == template_id,
        TemplateVersion.version_number == version_number,
        TemplateVersion.is_deleted == False
    ).first()

    if not version:
        raise VersionNotFoundError("Version not found")

    if version.is_active:
        raise ConflictError("Cannot delete active version")

    version.is_deleted = True
    version.deleted_at = datetime.now(timezone.utc)
    version.deleted_by = actor_id
    db.flush()

    return {"message": "Version deleted successfully"}

def soft_delete_template(
    db: Session,
    template_id: int,
    actor_id: str,
    tenant_id: str
):
    locked_template = db.query(NotificationTemplate).with_for_update().filter(
        NotificationTemplate.id == template_id,
        NotificationTemplate.tenant_id == tenant_id,
        NotificationTemplate.is_deleted == False
    ).first()

    if not locked_template:
        raise TemplateNotFoundError("Template not found")

    now = datetime.now(timezone.utc)

    locked_template.is_deleted = True
    locked_template.deleted_at = now
    locked_template.deleted_by = actor_id
    locked_template.is_active = False

    db.query(TemplateVersion).filter(
        TemplateVersion.template_id == template_id,
        TemplateVersion.is_deleted == False
    ).update({
        "is_deleted": True,
        "deleted_at": now,
        "deleted_by": actor_id,
        "is_active": False
    })
    
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise ConflictError("Conflict occurred during template soft deletion") from e