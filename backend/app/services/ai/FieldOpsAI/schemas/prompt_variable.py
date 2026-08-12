from typing import Any, Union
import math
from pydantic import BaseModel, ConfigDict, Field, field_validator

class PromptVariableDefinition(BaseModel):
    """
    Structured definition of a single template variable.
    """
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    name: str = Field(
        ...,
        min_length=1,
    )
    
    required: bool = True
    
    default: Any | None = None

    @field_validator("default")
    @classmethod
    def validate_default(cls, value: Any) -> Any:
        def check_json(v: Any, depth: int = 0) -> Any:
            if depth > 10:
                raise ValueError("Default value exceeds maximum nesting depth")
            if v is None:
                return v
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    raise ValueError("NaN and Infinity are not allowed")
                return v
            if isinstance(v, str):
                if len(v) > 5000:
                    raise ValueError("Default string too long")
                return v
            if type(v) is list:
                if len(v) > 100:
                    raise ValueError("Default list too large")
                return [check_json(item, depth + 1) for item in v]
            if type(v) is dict:
                if len(v) > 100:
                    raise ValueError("Default dict too large")
                for k in v.keys():
                    if type(k) is not str:
                        raise ValueError("Default dict keys must be strings")
                return {k: check_json(val, depth + 1) for k, val in v.items()}
            raise ValueError(f"Type {type(v).__name__} is not JSON-compatible")

        return check_json(value)

PromptVariableDeclaration = Union[str, PromptVariableDefinition]
