"""
response_parser.py

Purpose
-------
Convert raw LLM responses into validated Pydantic models.

The parser guarantees that only valid AI decisions
enter the FieldOps backend.
"""

import json
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError


# Generic Pydantic model type
T = TypeVar("T", bound=BaseModel)


class ResponseParser:
    """
    Parses AI responses into validated schema objects.
    """

    @staticmethod
    def parse(
        response: str,
        schema: Type[T],
    ) -> T:
        """
        Convert an LLM JSON response into a validated
        Pydantic model.

        Parameters
        ----------
        response
            Raw JSON string returned by the LLM.

        schema
            Target Pydantic schema.

        Returns
        -------
        T
            Validated Pydantic model.

        Raises
        ------
        ValueError
            If the AI returned invalid JSON.

        ValueError
            If the JSON does not match the expected schema.
        """

        # -------------------------------------------------
        # Step 1: Parse JSON
        # -------------------------------------------------

        try:
            data = json.loads(response)

        except json.JSONDecodeError as exc:
            raise ValueError(
                "AI returned invalid JSON.\n\n"
                f"Response:\n{response}\n\n"
                f"JSON Error: {exc}"
            ) from exc

        # -------------------------------------------------
        # Step 2: Validate Schema
        # -------------------------------------------------

        try:
            return schema.model_validate(data)

        except ValidationError as exc:
            raise ValueError(
                "AI response does not match the expected schema.\n\n"
                f"Schema: {schema.__name__}\n\n"
                f"Validation Error:\n{exc}\n\n"
                f"Response:\n{response}"
            ) from exc