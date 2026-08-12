# JOB LIFECYCLE

## Purpose

This document defines every valid job state inside the
FieldOps Commander platform.

The AI MUST NEVER invent additional states.

The AI MUST NEVER skip required states.

---

## Complete Lifecycle

DRAFT

↓

CREATED

↓

ASSIGNED

↓

ACCEPTED

↓

EN_ROUTE

↓

ON_SITE

↓

COMPLETED

↓

CLOSED

---

## State Definitions

### DRAFT

A job has been started but not submitted.

Allowed Next State

CREATED

---

### CREATED

The job exists.

No technician has been assigned.

Allowed Next State

ASSIGNED

---

### ASSIGNED

A technician has been selected.

Waiting for technician response.

Allowed Next States

ACCEPTED

REJECTED

---

### REJECTED

The technician rejected the assignment.

Dispatcher or Planning Agent must assign another technician.

Allowed Next State

ASSIGNED

---

### ACCEPTED

The technician accepted the assignment.

Allowed Next State

EN_ROUTE

---

### EN_ROUTE

The technician is travelling to the customer.

GPS tracking begins.

ETA monitoring begins.

Allowed Next State

ON_SITE

---

### ON_SITE

The technician has arrived.

Work has started.

Allowed Next State

COMPLETED

---

### COMPLETED

The technician has finished the work.

Customer confirmation may still be pending.

Allowed Next State

CLOSED

---

### CLOSED

Final state.

Job is archived.

Notifications complete.

Billing may begin.

No further transitions are allowed.

---

# Invalid Transitions

The AI must reject transitions such as:

CREATED → COMPLETED

CREATED → CLOSED

ASSIGNED → CLOSED

EN_ROUTE → CREATED

COMPLETED → ASSIGNED

CLOSED → EN_ROUTE

Any undefined transition is invalid.

---

# GPS Rules

GPS tracking starts only in

EN_ROUTE

GPS tracking stops after

COMPLETED

---

# Notification Rules

Customer notifications are allowed during

ASSIGNED

EN_ROUTE

ON_SITE

COMPLETED

CLOSED

Internal dispatcher actions should not trigger customer notifications unless explicitly configured.

---

# AI Responsibilities

Planning Agent

• Creates jobs
• Assigns technicians

Dispatch Agent

• Tracks technician acceptance
• Tracks technician movement
• Handles reassignment

Comms Agent

• Sends customer notifications
• Sends technician notifications

Closure Agent

• Verifies completion
• Closes the job
• Generates final summary