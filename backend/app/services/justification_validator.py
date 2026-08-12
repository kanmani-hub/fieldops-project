import re
from fastapi import HTTPException
from fastapi.responses import JSONResponse

class JustificationValidationError(Exception):
    def __init__(self, message: str, current_length: int, min_length: int = 50, max_length: int = 500):
        self.message = message
        self.current_length = current_length
        self.min_length = min_length
        self.max_length = max_length

def validate_justification(justification: str):
    if not justification:
        raise JustificationValidationError(
            message="Justification is required for manual override (FR-025)",
            current_length=0
        )
    
    length = len(justification.strip())
    
    if length < 50:
        raise JustificationValidationError(
            message=f"Justification too short: {length} chars. Minimum: 50 characters. Need {50 - length} more characters.",
            current_length=length
        )
    
    if length > 500:
        raise JustificationValidationError(
            message=f"Justification too long: {length} chars. Maximum: 500 characters. Remove {length - 500} characters.",
            current_length=length
        )
    
    if not re.search(r'[a-zA-Z]{3,}', justification):
        raise JustificationValidationError(
            message="Justification must contain meaningful text.",
            current_length=length
        )
    
    return justification
