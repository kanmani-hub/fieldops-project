# FieldOps PII Sanitization

## Status

Implemented

## Purpose

The FieldOps PII sanitization system prevents real personally
identifiable information from leaving the FieldOps backend and
reaching Groq or another external AI provider.

The system replaces private values with placeholders before an
AI request is sent.

The original values are restored locally after the AI response
returns.

The placeholder mapping exists only in application memory for
the duration of one request.

It is never intentionally stored in:

- PostgreSQL
- Redis
- Application logs
- Audit logs
- External AI providers
- Prompt history
- Analytics systems

---

# Architecture

```text
Original backend context
        │
        ▼
PIISanitizer.sanitize()
        │
        ├── Structured field detection
        ├── Nested-object detection
        ├── Regex-based detection
        └── Local contextual name detection
        │
        ▼
Sanitized context
        │
        ▼
AIOrchestrator builds prompt
        │
        ▼
PIISanitizer.sanitize_prompt()
        │
        ├── Second free-text scan
        └── Validate no detectable PII remains
        │
   ┌────┴────┐
   │         │
 Safe     PII remains
   │         │
   ▼         ▼
Groq API   Block request
   │       Raise PIILeakageError
   ▼
AI response containing placeholders
        │
        ▼
PIISanitizer.restore_data()
        │
        ▼
Original permitted values restored locally
        │
        ▼
Placeholder map cleared