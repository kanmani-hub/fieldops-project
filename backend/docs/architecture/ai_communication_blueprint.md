# AI Communication Blueprint

## Purpose

This document defines the architecture of the AI Communication & Agent Platform used by FieldOps Commander.

It serves as the single source of truth for how AI agents interact with the backend, AI runtime, notification services, and external communication channels.

The architecture emphasizes:

- Clear separation of responsibilities
- Modular AI agents
- Schema-validated communication
- AI provider abstraction
- Reliable fallback mechanisms
- Scalability and maintainability

---

# System Architecture

```mermaid
flowchart TD

    A[FastAPI Routes]

    A --> B[Business Services]

    B --> C[AI Integration Layer]

    C --> D1[Planning Agent]
    C --> D2[Dispatch Agent]
    C --> D3[Monitoring Agent]
    C --> D4[Sentiment Agent]
    C --> D5[Communication Agent]
    C --> D6[Closure Agent]

    D1 --> E[AI Runtime]
    D2 --> E
    D3 --> E
    D4 --> E
    D5 --> E
    D6 --> E

    E --> F[Groq Llama 3.3 70B]

    E --> G[Jinja2 Template Engine (Fallback)]

    F --> H[Structured JSON Response]
    G --> H

    H --> I[Business Services]

    I --> J[Notification Service]

    J --> K[Email]
    J --> L[SMS]
    J --> M[Push Notification]
    J --> N[In-App Notification]
```

---

# Four Layer Architecture

## Layer 1 — FastAPI Routes

Responsibilities

- Receive API requests
- Validate authentication
- Validate request payloads
- Call Business Services
- Return API responses

Contains no business logic.

---

## Layer 2 — Business Services

Responsibilities

- Execute business workflows
- Load database entities
- Build AI Context objects
- Invoke AI Agents
- Persist database changes
- Trigger notifications

Examples

- PlanningService
- DispatchService
- MonitoringService
- NotificationService

Business Services own all workflow logic.

---

## Layer 3 — AI Integration Layer

Responsibilities

- Invoke AI Agents
- Build prompts
- Validate AI responses
- Handle AI provider failures
- Execute fallback templates when required

Components

- PlanningAgent
- DispatchAgent
- MonitoringAgent
- SentimentAgent
- CommunicationAgent
- ClosureAgent

---

## Layer 4 — AI Runtime

Responsibilities

- Execute prompts
- Call AI provider
- Parse JSON
- Validate responses
- Return structured objects

Components

- AIOrchestrator
- Groq Client
- Response Parser
- Prompt Loader
- Token Tracker

---

# Agent Responsibilities

| Agent | Responsibility | Database Updates |
|---------|---------------|------------------|
| Planning Agent | Recommend technician ranking | ❌ |
| Dispatch Agent | Recommend dispatch workflow | ❌ |
| Monitoring Agent | Monitor active jobs and recommend actions | ❌ |
| Sentiment Agent | Analyze customer sentiment | ❌ |
| Communication Agent | Generate customer communication | ❌ |
| Closure Agent | Generate job completion summaries | ❌ |

Every AI agent is read-only.

Business Services execute all database updates.

---

# Communication Patterns

## 1. Synchronous

Used when an immediate AI response is required.

Example

Customer creates a service request.

Flow

```
Client

↓

FastAPI

↓

Planning Service

↓

Planning Agent

↓

Groq

↓

Planning Decision

↓

API Response
```

---

## 2. Asynchronous

Used for long-running or background work.

Examples

- Customer notifications
- Closure email generation
- Push notifications

Flow

```
Business Service

↓

Redis

↓

Celery Worker

↓

Communication Agent

↓

Notification Service
```

---

## 3. Event Driven

Used when system events trigger AI workflows.

Examples

- Technician accepted job
- Technician rejected job
- GPS updated
- Job completed

Flow

```
Event

↓

Business Service

↓

AI Agent

↓

Business Decision

↓

Notification
```

---

# End-to-End Data Flow

```
Customer Request

↓

FastAPI Route

↓

Business Service

↓

Build Context

↓

AI Agent

↓

AI Runtime

↓

Groq Provider

↓

Validated JSON Response

↓

Business Service

↓

Database Update

↓

Notification Service

↓

Customer
```

---

# Communication Agent Fallback Strategy

The Communication Agent primarily generates messages using AI.

If AI generation fails because of:

- Provider timeout
- Rate limiting
- Invalid JSON response
- Temporary AI outage

the backend automatically falls back to predefined Jinja2 templates.

Flow

```
Business Service

↓

Communication Agent

↓

AI Runtime

↓

Groq

↓

AI Success
        │
        ▼
 Generated Message

OR

AI Failure
        │
        ▼
Jinja2 Template

↓

Rendered Message

↓

Notification Service

↓

Email / SMS / Push / In-App
```

This guarantees uninterrupted customer communication even when the AI provider is unavailable.

---

# Technology Stack

| Layer | Technology | Purpose |
|--------|------------|----------|
| API | FastAPI | REST API |
| ORM | SQLAlchemy | Database Access |
| Validation | Pydantic | Request & Response Validation |
| AI Runtime | Groq (Llama 3.3 70B) | AI Inference |
| Prompt Engine | Markdown Prompts | AI Instructions |
| Template Engine | Jinja2 | Fallback Communication Templates |
| Background Queue | Redis | Event Queue |
| Workers | Celery | Async Processing |
| Notifications | Notification Service | Deliver Customer Messages |

---

# Schema Validation

Every AI response must conform to its Pydantic schema before entering the business layer.

Examples

PlanningDecision

DispatchDecision

MonitoringDecision

SentimentDecision

CommunicationDecision

ClosureDecision

Invalid AI responses are rejected before business processing.

---

# Performance Targets

| Metric | Target |
|----------|---------|
| Concurrent AI Requests | 100 |
| AI Response (p95) | < 5 seconds |
| AI Availability | > 99% |
| JSON Validation Success | 100% |
| Communication Fallback Time | < 1 second |

---

# Design Principles

The architecture follows these principles:

- Separation of Concerns
- Single Responsibility Principle
- Dependency Injection
- Provider Independence
- Schema-First AI
- Read-Only AI Agents
- Business Logic in Services
- AI Response Validation
- Graceful Fallback with Jinja2
- Event-Driven Scalability

---

# Future Enhancements

The architecture supports future additions without modifying existing agents.

Possible future agents include:

- Billing Agent
- Inventory Agent
- Quality Assurance Agent
- Analytics Agent
- Scheduling Optimization Agent
- Predictive Maintenance Agent

These can be integrated through the AI Integration Layer while preserving the existing architecture.