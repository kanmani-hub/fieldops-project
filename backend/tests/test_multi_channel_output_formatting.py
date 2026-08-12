import pytest
from email import message_from_string
import json
from app.services.ai.FieldOpsAI.schemas.communication import (
    CommunicationDecision,
    SMSMessageOutput,
    EmailMessageOutput,
    PushMessageOutput,
    PortalMessageOutput,
    MessageAction,
    output_text_for_validation,
)
from app.services.ai.FieldOpsAI.services.message_output_formatter import MessageOutputFormatter

def test_message_output_formatter_text_normalization():
    # Test SMS (text normalization)
    out = MessageOutputFormatter.format(
        channel="SMS",
        rendered_title=None,
        rendered_body="  Hello \n \n World  ",
        template_format="text"
    )
    assert isinstance(out, SMSMessageOutput)
    assert out.text == "Hello World"

    # Test EMAIL HTML plain-text pairing
    out_email = MessageOutputFormatter.format(
        channel="EMAIL",
        rendered_title="Subject",
        rendered_body="<p>Hello <b>World</b></p>",
        template_format="html"
    )
    assert isinstance(out_email, EmailMessageOutput)
    assert out_email.subject == "Subject"
    assert out_email.html_body is not None
    assert "Hello" in out_email.text_body
    assert "World" in out_email.text_body

def test_message_output_formatter_portal_title_fallback():
    # Test PORTAL title fallback
    out = MessageOutputFormatter.format(
        channel="PORTAL",
        rendered_title=None,
        rendered_body="Some body",
        template_format="text"
    )
    assert isinstance(out, PortalMessageOutput)
    assert out.title == "FieldOps Update"
    assert out.body == "Some body"

def test_communication_decision_compatibility():
    # Test that CommunicationDecision still provides legacy properties
    dec = CommunicationDecision(
        channel="EMAIL",
        output=EmailMessageOutput(subject="Subj", text_body="text", html_body="html"),
        tone="PROFESSIONAL",
        confidence=1.0
    )
    assert dec.subject == "Subj"
    assert dec.message == "html"
    assert dec.title is None

    dec_sms = CommunicationDecision(
        channel="SMS",
        output=SMSMessageOutput(text="hello"),
        tone="PROFESSIONAL",
        confidence=1.0
    )
    assert dec_sms.message == "hello"
    assert dec_sms.title is None
    assert dec_sms.subject is None

def test_output_text_for_validation():
    # Test SMS
    assert output_text_for_validation(SMSMessageOutput(text="msg")) == "msg"
    # Test EMAIL
    assert output_text_for_validation(EmailMessageOutput(subject="subj", text_body="txt")) == "subj\ntxt"
    # Test PUSH
    assert output_text_for_validation(PushMessageOutput(title="tit", body="bdy")) == "tit\nbdy"
    # Test PORTAL
    assert output_text_for_validation(PortalMessageOutput(title="tit", body="bdy")) == "tit\nbdy"

def test_sms_truncates_to_160():
    body = "A" * 200

    out = MessageOutputFormatter.format(
        channel="SMS",
        rendered_title=None,
        rendered_body=body,
        template_format="text",
    )

    assert isinstance(out, SMSMessageOutput)
    assert len(out.text) == 160
    assert out.text.endswith("...")

def test_push_title_truncates_to_50():
    out = MessageOutputFormatter.format(
        channel="PUSH",
        rendered_title="T" * 80,
        rendered_body="Body",
        template_format="text",
    )

    assert isinstance(out, PushMessageOutput)
    assert len(out.title) == 50
    assert out.title.endswith("...")

def test_push_body_truncates_to_200():
    out = MessageOutputFormatter.format(
        channel="PUSH",
        rendered_title="Title",
        rendered_body="B" * 250,
        template_format="text",
    )

    assert isinstance(out, PushMessageOutput)
    assert len(out.body) == 200
    assert out.body.endswith("...")

def test_email_html_inline_css():
    html = """
    <html>
        <head>
            <style>
                p { color:red; }
            </style>
        </head>
        <body>
            <p>Hello Customer</p>
        </body>
    </html>
    """

    out = MessageOutputFormatter.format(
        channel="EMAIL",
        rendered_title="Welcome",
        rendered_body=html,
        template_format="html",
    )

    assert isinstance(out, EmailMessageOutput)
    assert "style=" in out.html_body
    assert "Hello Customer" in out.text_body
    mime_message = message_from_string(out.mime_message)
    assert mime_message.is_multipart()
    assert [part.get_content_type() for part in mime_message.walk() if not part.is_multipart()] == [
        "text/plain", "text/html"
    ]


def test_push_formats_actions():
    out = MessageOutputFormatter.format(
        channel="PUSH",
        rendered_title="Job update",
        rendered_body="Your technician is on the way.",
        template_format="text",
        actions=[{"label": "View job", "action": "/jobs/123"}],
    )

    assert out.actions[0].label == "View job"
    assert out.actions[0].action == "/jobs/123"


def test_portal_produces_json_payload():
    out = MessageOutputFormatter.format(
        channel="PORTAL",
        rendered_title="Job update",
        rendered_body="Your technician is on the way.",
        template_format="text",
        actions=[{"label": "View job", "action": "/jobs/123"}],
    )

    payload = json.loads(out.model_dump_json())
    assert payload["payload"] == {
        "title": "Job update",
        "body": "Your technician is on the way.",
        "content_format": "text",
        "actions": [{"label": "View job", "action": "/jobs/123"}],
    }

import pytest
from app.services.ai.FieldOpsAI.services.message_output_formatter import (
    MessageOutputFormatter,
    UnsupportedOutputChannelError,
)

def test_invalid_channel():
    with pytest.raises(UnsupportedOutputChannelError):
        MessageOutputFormatter.format(
            channel="WHATSAPP",
            rendered_title=None,
            rendered_body="Hello",
            template_format="text",
        )

from app.services.ai.FieldOpsAI.services.message_output_formatter import (
    UnsupportedChannelFormatError,
)

def test_invalid_template_format():
    with pytest.raises(UnsupportedChannelFormatError):
        MessageOutputFormatter.format(
            channel="SMS",
            rendered_title=None,
            rendered_body="Hello",
            template_format="markdown",
        )

from app.services.ai.FieldOpsAI.services.message_output_formatter import (
    InvalidFormattedContentError,
)

def test_sms_blank_body():
    with pytest.raises(InvalidFormattedContentError):
        MessageOutputFormatter.format(
            channel="SMS",
            rendered_title=None,
            rendered_body="    ",
            template_format="text",
        )

from app.services.ai.FieldOpsAI.services.message_output_formatter import (
    MissingOutputTitleError,
)

def test_email_requires_subject():
    with pytest.raises(MissingOutputTitleError):
        MessageOutputFormatter.format(
            channel="EMAIL",
            rendered_title=None,
            rendered_body="Hello",
            template_format="text",
        )

def test_push_requires_title():
    with pytest.raises(MissingOutputTitleError):
        MessageOutputFormatter.format(
            channel="PUSH",
            rendered_title=None,
            rendered_body="Body",
            template_format="text",
        )

def test_push_blank_body():
    with pytest.raises(InvalidFormattedContentError):
        MessageOutputFormatter.format(
            channel="PUSH",
            rendered_title="Title",
            rendered_body="",
            template_format="text",
        )

def test_portal_invalid_format():
    with pytest.raises(UnsupportedChannelFormatError):
        MessageOutputFormatter.format(
            channel="PORTAL",
            rendered_title="Title",
            rendered_body="Body",
            template_format="html",
        )

def test_null_byte():
    with pytest.raises(InvalidFormattedContentError):
        MessageOutputFormatter.format(
            channel="SMS",
            rendered_title=None,
            rendered_body="Hello\x00World",
            template_format="text",
        )


def test_rejects_non_string_body():
    with pytest.raises(InvalidFormattedContentError):
        MessageOutputFormatter.format(
            channel="SMS",
            rendered_title=None,
            rendered_body=123,  # type: ignore[arg-type]
            template_format="text",
        )


def test_in_app_uses_existing_portal_contract():
    out = MessageOutputFormatter.format(
        channel="IN_APP",
        rendered_title=" Update\r\nnow ",
        rendered_body="Line one\r\n\r\n\r\nLine two",
        template_format="text",
    )

    assert isinstance(out, PortalMessageOutput)
    assert out.title == "Update now"
    assert out.body == "Line one\n\nLine two"


@pytest.mark.parametrize(
    ("channel", "title", "body"),
    [
        ("EMAIL", "   ", "Body"),
        ("PUSH", "Title", "Body"),
    ],
)
def test_rejects_blank_required_title_or_invalid_sms_format(channel, title, body):
    if channel == "EMAIL":
        with pytest.raises(MissingOutputTitleError):
            MessageOutputFormatter.format(
                channel=channel,
                rendered_title=title,
                rendered_body=body,
                template_format="text",
            )
    else:
        with pytest.raises(UnsupportedChannelFormatError):
            MessageOutputFormatter.format(
                channel="SMS",
                rendered_title=None,
                rendered_body=body,
                template_format="html",
            )


def test_email_text_format_and_blank_body_rejection():
    out = MessageOutputFormatter.format(
        channel="EMAIL",
        rendered_title="Subject",
        rendered_body="First line\r\n\r\n\r\nSecond line",
        template_format="text",
    )
    assert out.html_body is None
    assert out.text_body == "First line\n\nSecond line"

    with pytest.raises(InvalidFormattedContentError):
        MessageOutputFormatter.format(
            channel="EMAIL",
            rendered_title="Subject",
            rendered_body="   ",
            template_format="text",
        )


@pytest.mark.parametrize(
    "actions",
    [
        [MessageAction(label=" Open\njob ", action=" /jobs/123 ")],
        [{"label": "", "action": "/jobs/123"}],
        [{"label": "Open job"}],
    ],
)
def test_action_normalization_and_validation(actions):
    if len(actions) == 1 and isinstance(actions[0], MessageAction):
        out = MessageOutputFormatter.format(
            channel="PUSH",
            rendered_title="Update",
            rendered_body="Body",
            template_format="text",
            actions=actions,
        )
        assert out.actions == (MessageAction(label="Open job", action="/jobs/123"),)
        return

    with pytest.raises(InvalidFormattedContentError):
        MessageOutputFormatter.format(
            channel="PUSH",
            rendered_title="Update",
            rendered_body="Body",
            template_format="text",
            actions=actions,
        )


@pytest.mark.parametrize("channel", ["PUSH", "PORTAL"])
def test_rejects_invalid_format_or_blank_body(channel):
    with pytest.raises(UnsupportedChannelFormatError):
        MessageOutputFormatter.format(
            channel=channel,
            rendered_title="Title",
            rendered_body="Body",
            template_format="html",
        )

    with pytest.raises(InvalidFormattedContentError):
        MessageOutputFormatter.format(
            channel=channel,
            rendered_title="Title",
            rendered_body="   ",
            template_format="text",
        )
