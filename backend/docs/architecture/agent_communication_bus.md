# Agent Communication Bus (Story 1.6)

This document describes the design and implementation of the asynchronous in-process Agent Communication Bus (`AgentBus`) for the FieldOps Commander AI platform.

The `AgentBus` is responsible for transporting validated messages between AI agents. It does not store live agent instances, execute them, or handle persistence.

## Architecture & Responsibilities

### Separation of Concerns
- **AgentRegistry**: Stores agent definitions and factories.
- **AgentPool**: Stores live agent instances.
- **AgentLifecycle**: Manages agent execution flow (setup, run, teardown).
- **AgentStateManager**: Handles agent runtime state snapshots and persistence.
- **AgentBus**: Transports validated messages between subscribers and publishers.

### Core Delivery Semantics
1. **In-Process Pub/Sub**: Message transport is in-process only. It does not use Redis, Kafka, Celery, or database persistence.
2. **Tenant Isolation**: Message routing is strictly bounded within a single tenant. The tenant is determined by `message.sender.tenant_id`. Subscriptions for different tenants are isolated.
3. **No Retry**: Message delivery is attempt-once. If a handler fails or times out, the failure is recorded in the delivery result, but no automatic retries are performed.
4. **Message Copying**: Each handler receives an isolated `deep=True` copy of the message envelope. This prevents side-effects or state pollution if one handler mutates the payload or metadata.

---

## Routing and Delivery Modes

The bus matches a subscription to a message using the following rule:
```text
subscription.tenant_id == message.sender.tenant_id
AND
subscription.topic == message.topic
AND
(
    message.recipient is None
    OR subscription.subscriber == message.recipient
)
```

### Broadcast Routing
- Triggered when `message.recipient` is `None`.
- The message is routed to all subscribers sharing the same `tenant_id` and `topic`, including those that have `subscriber=None` and those with an exact `AgentAddress`.

### Targeted Routing
- Triggered when `message.recipient` matches a specific `AgentAddress`.
- The message is routed only to subscriptions where `subscription.subscriber` exactly equals `message.recipient`.
- Subscriptions with `subscriber=None` or different `AgentAddress` instances are excluded.

---

## Handler Execution & Thread Safety

### Lock Scope
An `asyncio.Lock` guards the subscription lookup, subscription mutations (`subscribe`, `unsubscribe`), and tenant clearing.
- **Critical Rule**: The lock is released **before** any message handler is called or awaited.
- Handlers run concurrently outside the lock, preventing slow handlers from blocking subscription registration or other publishes.

### Timeout Policy
- The bus applies an independent timeout (configured via `handler_timeout_seconds` on the bus, maximum 30 seconds) to each handler invocation.
- **Async Handlers**: A timed-out async handler is cancelled normally.
- **Sync Handlers**: Sync handlers run in separate threads via `asyncio.to_thread` to prevent blocking the event-loop thread. When a sync handler times out, the bus stops waiting for it and records a `HANDLER_TIMEOUT` error. However, the underlying worker thread cannot be forcibly aborted and will continue running in the background until it completes.
- **Idempotency Recommendation**: Because timed-out sync handlers cannot be immediately stopped, they should be designed to be idempotent to prevent duplicate actions or inconsistent state if they finish later.

### Failure Isolation
- If a handler throws an exception, the exception is caught, logged, and isolated.
- The failure of one handler does not abort or affect other matching handlers.
- A non-None, non-awaitable return value from any handler also triggers a failure (`HANDLER_FAILED`).
- Safe default error codes (`HANDLER_TIMEOUT` and `HANDLER_FAILED`) are returned in the `PublishResult`. Raw exception texts and stack traces are never exposed in PublishResult failures.

---

## Privacy and Logging Guidelines

To maintain metadata privacy and prevent PII leaks:
1. **Forbidden Key Rejection**: Payloads, metadata, and error details are scanned recursively for case-insensitive sensitive keys:
   - Credentials/Auth: `api_key`, `secret`, `password`, `token`, `auth_token`, `authorization`
   - Prompt context: `prompt`, `system_prompt`, `provider_response`, `raw_response`
   - Customer details: `customer_name`, `customer_address`, `customer_email`, `customer_phone`, `phone_number`
   - Location data: `gps`, `latitude`, `longitude`, `coordinates`
2. **Safe Logging**: The bus log messages contain only metadata (IDs, topics, sender/recipient addresses). They never include the message payload, metadata, raw exception strings, or stack traces.
3. **Delivery Failures**: `DeliveryFailure` contains only `safe_message` and `error_code`, preventing raw stack traces or internal exception messages from leaking to callers.

---

## Existing Message Envelope Compatibility

The `AgentBus` preserves backward compatibility with the existing message classes:
- `CommandMessage` (default topic: `agent.command`)
- `QueryMessage` (default topic: `agent.query`)
- `EventMessage` (default topic: `agent.event`)
- `ResponseMessage` (default topic: `agent.response`)
- `ErrorMessage` (default topic: `agent.error`)

The original serialization methods `to_json()`, `to_dict()`, and `from_dict()` remain fully operational and unmodified.

---

## Future Integrations

## Future Integrations

### Redis Adapter

A future Redis Pub/Sub adapter can implement the same `AgentBus`
interface to support multi-process routing.

The adapter will serialize and deserialize validated
`MessageEnvelope` instances while preserving:

- Tenant isolation
- Topic routing
- Exact-address targeted delivery
- Correlation IDs
- Privacy-safe message contracts

Redis integration is not part of Story 1.6.

### Health Monitoring — Story 1.7

Health Monitoring is a separate concern and is not implemented in
Story 1.6.

A future health monitor may read operational metrics such as:

- Current subscription count
- Subscriber count by tenant
- Handler delivery success rate
- Handler failure rate
- Handler timeout rate

The health monitor must not access message payloads or metadata.