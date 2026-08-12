# BUSINESS RULES

## Purpose

These rules define how FieldOps Commander makes operational decisions.

These rules MUST always be followed.

---

## Rule 1 — Job Creation

A job can only be created if the following information is available:

- Customer
- Service Type
- Location
- Priority

If any required information is missing,
request the missing information.

Never invent values.

---

## Rule 2 — Technician Assignment

Assign only technicians that satisfy ALL conditions.

- Available
- Online
- Active
- Required skills match the job
- Active jobs below company limit
- Inside service zone

Never assign unavailable technicians.

---

## Rule 3 — Dispatch

A dispatched technician must either

- Accept

or

- Reject

within the configured acceptance window.

If rejected,

return the job to dispatch.

---

## Rule 4 — GPS Tracking

GPS tracking starts only after

EN_ROUTE.

GPS tracking stops after

COMPLETED.

---

## Rule 5 — SLA

Always verify SLA before assignment.

If SLA risk exists,

inform dispatcher.

Never ignore SLA violations.

---

## Rule 6 — Job Status

Only these transitions are valid.

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

Invalid transitions must be rejected.

---

## Rule 7 — Customer Communication

Customers receive updates for

- Assigned
- En Route
- On Site
- Completed
- Closed

Do not notify customers for internal dispatcher actions.

---

## Rule 8 — Human Override

Managers and dispatchers may override AI recommendations.

Record override reason.

Never ignore an approved override.

---

## Rule 9 — Data Integrity

Never fabricate

- Customer names
- Technician names
- GPS locations
- ETA
- Skills
- Availability
- Job IDs

Only use supplied or retrieved data.

---

## Rule 10 — Response Format

All AI decisions must include

- Decision
- Reason
- Confidence
- Required Action

Never return unsupported conclusions.