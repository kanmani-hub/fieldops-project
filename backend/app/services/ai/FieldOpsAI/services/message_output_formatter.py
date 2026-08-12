"""
message_output_formatter.py

Story 8.3 Canonical Message Output Formatter.
Converts safe rendered Jinja2 templates into strict channel-specific immutable output.
"""

from __future__ import annotations

import re
import html2text
from premailer import transform
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from collections.abc import Mapping, Sequence
from typing import Any

from app.services.ai.FieldOpsAI.schemas.communication import (
    FormattedCommunicationOutput,
    SMSMessageOutput,
    EmailMessageOutput,
    MessageAction,
    PushMessageOutput,
    PortalMessageOutput,
)


class MessageOutputFormattingError(ValueError):
    """Base error for formatting failures."""


class UnsupportedOutputChannelError(MessageOutputFormattingError):
    """Raised when the channel is unsupported."""


class UnsupportedChannelFormatError(MessageOutputFormattingError):
    """Raised when the format is incompatible with the channel."""


class MissingOutputTitleError(MessageOutputFormattingError):
    """Raised when a title/subject is required but missing."""


class InvalidFormattedContentError(MessageOutputFormattingError):
    """Raised when the content is blank or contains prohibited characters."""


class MessageOutputFormatter:
    """
    Strict, purely functional formatter for communication channels.
    """

    MAX_SMS_LENGTH = 160
    MAX_PUSH_TITLE_LENGTH = 50
    MAX_PUSH_BODY_LENGTH = 200

    @classmethod
    def format(
        cls,
        *,
        channel: str,
        rendered_title: str | None,
        rendered_body: str,
        template_format: str,
        actions: Sequence[MessageAction | Mapping[str, str]] | None = None,
    ) -> FormattedCommunicationOutput:
        """
        Convert safely rendered content into strict channel output.
        """
        # Validate inputs
        if not isinstance(rendered_body, str):
            raise InvalidFormattedContentError("Rendered body must be a string.")
            
        if "\x00" in rendered_body or (rendered_title and "\x00" in rendered_title):
            raise InvalidFormattedContentError("Null bytes are prohibited.")
            
        normalized_channel = channel.upper()
        if normalized_channel == "IN_APP":
            normalized_channel = "PORTAL"
            
        if normalized_channel not in ("SMS", "EMAIL", "PUSH", "PORTAL"):
            raise UnsupportedOutputChannelError(f"Channel {channel} is not supported.")
            
        normalized_format = template_format.lower()
        if normalized_format not in ("text", "html"):
            raise UnsupportedChannelFormatError(f"Format {template_format} is not supported.")
            
        # Dispatch to specific formatters
        if normalized_channel == "SMS":
            return SMSFormatter.format(rendered_body, normalized_format)
        if normalized_channel == "EMAIL":
            return EmailFormatter.format(rendered_title, rendered_body, normalized_format)
        if normalized_channel == "PUSH":
            return PushFormatter.format(
                rendered_title, rendered_body, normalized_format, actions
            )
        return PortalFormatter.format(
            rendered_title, rendered_body, normalized_format, actions
        )

    @classmethod
    def _normalize_whitespace(
        cls,
        text: str,
        collapse_lines: bool = False,
    ) -> str:
        """
        Safely normalize text spacing.
        """
        # Normalize CR/LF
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        
        if collapse_lines:
            # Replace all newlines and tabs with spaces
            text = text.replace("\n", " ").replace("\t", " ")
            # Collapse multiple spaces
            text = re.sub(r" +", " ", text)
        else:
            # Collapse spaces and tabs on the same line, but preserve newlines
            text = re.sub(r"[ \t]+", " ", text)
            # Collapse multiple empty lines to a single empty line
            text = re.sub(r"\n{3,}", "\n\n", text)
            
        return text.strip()

    @classmethod
    def _truncate(cls, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."


    @classmethod
    def _validate_subject_or_title(
        cls,
        title: str | None,
        channel: str,
        require: bool = True,
    ) -> str | None:
        if require and not title:
            raise MissingOutputTitleError(f"{channel} requires a title/subject.")
        if not title:
            return None
            
        # Check for CR/LF injection
        if "\n" in title or "\r" in title:
            # Normalize it out rather than just failing, but for headers it's safer to just reject or replace
            pass
            
        normalized = cls._normalize_whitespace(title, collapse_lines=True)
        if require and not normalized:
            raise MissingOutputTitleError(f"{channel} requires a non-blank title/subject.")
            
        return normalized

class SMSFormatter:
    """Format rendered templates for the SMS transport."""

    @staticmethod
    def format(body: str, format_type: str) -> SMSMessageOutput:
        if format_type != "text":
            raise UnsupportedChannelFormatError("SMS requires text format.")
        text = MessageOutputFormatter._truncate(
            MessageOutputFormatter._normalize_whitespace(body, collapse_lines=True),
            MessageOutputFormatter.MAX_SMS_LENGTH,
        )
        if not text:
            raise InvalidFormattedContentError("SMS text cannot be blank.")
        return SMSMessageOutput(text=text)


class EmailFormatter:
    """Format rendered templates as multipart email content."""

    @staticmethod
    def format(title: str | None, body: str, format_type: str) -> EmailMessageOutput:
        subject = MessageOutputFormatter._validate_subject_or_title(title, "EMAIL")
        if format_type == "html":
            html_body = transform(body)
            converter = html2text.HTML2Text()
            converter.ignore_links = False
            converter.ignore_images = True
            converter.body_width = 0
            text_body = MessageOutputFormatter._normalize_whitespace(
                converter.handle(html_body), collapse_lines=False
            )
        else:
            html_body = None
            text_body = MessageOutputFormatter._normalize_whitespace(body, collapse_lines=False)
        if not text_body:
            raise InvalidFormattedContentError("Email body cannot be blank.")
        message = MIMEMultipart("alternative")
        message["Subject"] = subject
        message.attach(MIMEText(text_body, "plain", "utf-8"))
        if html_body is not None:
            message.attach(MIMEText(html_body, "html", "utf-8"))
        return EmailMessageOutput(
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            mime_message=message.as_string(),
        )


def _format_actions(
    actions: Sequence[MessageAction | Mapping[str, str]] | None,
) -> tuple[MessageAction, ...]:
    formatted: list[MessageAction] = []
    for action in actions or ():
        if isinstance(action, MessageAction):
            raw_label, raw_action = action.label, action.action
        else:
            raw_label, raw_action = action.get("label"), action.get("action")
        if not isinstance(raw_label, str) or not isinstance(raw_action, str):
            raise InvalidFormattedContentError("Actions require string label and action values.")
        label = MessageOutputFormatter._normalize_whitespace(raw_label, collapse_lines=True)
        action_value = MessageOutputFormatter._normalize_whitespace(raw_action, collapse_lines=True)
        if not label or not action_value:
            raise InvalidFormattedContentError("Actions cannot be blank.")
        formatted.append(MessageAction(label=label, action=action_value))
    return tuple(formatted)


class PushFormatter:
    """Format compact push notifications and their optional actions."""

    @staticmethod
    def format(
        title: str | None,
        body: str,
        format_type: str,
        actions: Sequence[MessageAction | Mapping[str, str]] | None = None,
    ) -> PushMessageOutput:
        if format_type != "text":
            raise UnsupportedChannelFormatError("PUSH requires text format.")
        valid_title = MessageOutputFormatter._truncate(
            MessageOutputFormatter._validate_subject_or_title(title, "PUSH"),
            MessageOutputFormatter.MAX_PUSH_TITLE_LENGTH,
        )
        valid_body = MessageOutputFormatter._truncate(
            MessageOutputFormatter._normalize_whitespace(body, collapse_lines=False),
            MessageOutputFormatter.MAX_PUSH_BODY_LENGTH,
        )
        if not valid_body:
            raise InvalidFormattedContentError("PUSH body cannot be blank.")
        return PushMessageOutput(
            title=valid_title, body=valid_body, actions=_format_actions(actions)
        )


class PortalFormatter:
    """Format structured portal notifications without changing in-app delivery fields."""

    @staticmethod
    def format(
        title: str | None,
        body: str,
        format_type: str,
        actions: Sequence[MessageAction | Mapping[str, str]] | None = None,
    ) -> PortalMessageOutput:
        if format_type != "text":
            raise UnsupportedChannelFormatError("PORTAL currently supports text format only.")
        valid_body = MessageOutputFormatter._normalize_whitespace(body, collapse_lines=False)
        if not valid_body:
            raise InvalidFormattedContentError("PORTAL body cannot be blank.")
        valid_title = MessageOutputFormatter._validate_subject_or_title(title, "PORTAL", require=False)
        portal_title = valid_title or "FieldOps Update"
        formatted_actions = _format_actions(actions)
        payload = {
            "title": portal_title,
            "body": valid_body,
            "content_format": "text",
            "actions": [action.model_dump() for action in formatted_actions],
        }
        return PortalMessageOutput(
            title=portal_title,
            body=valid_body,
            content_format="text",
            actions=formatted_actions,
            payload=payload,
        )
