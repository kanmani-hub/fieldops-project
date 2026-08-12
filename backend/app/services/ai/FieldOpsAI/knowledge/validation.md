# VALIDATION RULES

## Purpose

This document defines the validation rules that every AI agent must follow
before making a decision.

The AI must validate all required information before creating,
assigning, dispatching, or closing a job.

Never bypass validation.

---

# Job Creation Validation

Before a job can be created, verify the following fields are present:

- Customer ID
- Customer Name
- Service Type
- Priority
- Service Address

If any required field is missing:

DO NOT create the job.

Instead, return the missing fields.

---

# Technician Assignment Validation

Before assigning a technician, verify:

- Technician exists
- Technician status is AVAILABLE
- Technician is ONLINE
- Technician has required skills
- Technician is inside the service zone
- Technician active jobs are below the configured limit

If any check fails:

Reject the assignment.

---

# Dispatch Validation

Before dispatching a technician:

Verify

- Job status is ASSIGNED
- Technician accepted the assignment
- Technician has not already started another conflicting job

If validation fails:

Do not dispatch.

---

# GPS Validation

GPS updates must include:

- Technician ID
- Latitude
- Longitude
- Timestamp

Reject GPS updates that contain

- Missing coordinates
- Invalid coordinates
- Missing timestamp

---

# Status Transition Validation

Only allow valid lifecycle transitions.

Examples

CREATED → ASSIGNED

ASSIGNED → ACCEPTED

ACCEPTED → EN_ROUTE

EN_ROUTE → ON_SITE

ON_SITE → COMPLETED

COMPLETED → CLOSED

Reject all other transitions.

---

# SLA Validation

Before assignment:

Calculate estimated arrival time.

If ETA exceeds SLA target:

Flag the job as

SLA_RISK

Do not ignore SLA violations.

---

# Customer Communication Validation

Before sending notifications:

Verify

- Customer exists
- Communication channel is enabled
- Contact details are available

If validation fails:

Do not send notification.

---

# Job Closure Validation

Before closing a job:

Verify

- Technician marked job as COMPLETED
- Required completion notes exist
- Required photos (if applicable) exist
- Customer confirmation has been received (if required)

If validation fails:

Do not close the job.

---

# AI Decision Validation

Every AI recommendation must include

- Decision
- Reason
- Confidence Score

Never return unsupported recommendations.

---

# Data Integrity

Never invent

- Customers
- Technicians
- Job IDs
- GPS locations
- ETA
- SLA values

Only use supplied or retrieved information.

If information is unavailable, state that it is unavailable.