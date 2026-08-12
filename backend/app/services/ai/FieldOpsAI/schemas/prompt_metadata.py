from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.ai.FieldOpsAI.schemas.ai_task import AITask

class PromptType(str, Enum):
    SYSTEM = "SYSTEM"
    TASK = "TASK"

class PromptMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    prompt_type: PromptType
    task: Optional[AITask] = None
    enabled: bool = True
    description: Optional[str] = None

    @model_validator(mode="after")
    def validate_metadata(self) -> "PromptMetadata":
        import pathlib
        
        # Normalize key
        normalized_key = self.key.strip().upper()
        if not normalized_key:
            raise ValueError("key must not be blank")
        
        # Normalize relative_path
        normalized_path = self.relative_path.strip()
        if not normalized_path:
            raise ValueError("relative_path must not be blank")
            
        p = pathlib.Path(normalized_path)
        if p.is_absolute() or normalized_path.startswith("/") or normalized_path.startswith("\\"):
            raise ValueError("relative_path must be relative, never absolute")
        if ".." in p.parts:
            raise ValueError("relative_path must not contain traversal")
            
        # Create a new dict for frozen model mutation bypass or since we're in 'after' validation, returning a modified instance or using object.__setattr__
        # Pydantic v2: if frozen=True, we can't easily mutate self. We must use model_copy or dict creation. Wait, model_validator mode="after" returns self. If we need to mutate, we should use mode="before" for normalization.
        
        # Actually, let's just use object.__setattr__ since it's a frozen BaseModel.
        object.__setattr__(self, "key", normalized_key)
        object.__setattr__(self, "relative_path", normalized_path)
        
        if self.prompt_type == PromptType.SYSTEM and self.task is not None:
            raise ValueError("SYSTEM definitions must not require an AITask")
        if self.prompt_type == PromptType.TASK and self.task is None:
            raise ValueError("TASK definitions must contain an AITask")
        
        return self
