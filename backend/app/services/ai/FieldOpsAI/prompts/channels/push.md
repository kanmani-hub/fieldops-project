# PUSH NOTIFICATION TEMPLATE

## Purpose

Generate concise push notifications for the FieldOps Commander platform.

Push notifications must be short, actionable, and based only on the supplied data.

Never invent information.

---

## General Rules

- Maximum length: 120 characters.
- Use plain language.
- Do not include internal IDs.
- Do not include confidential information.
- Never expose system details.
- Never invent ETA, technician names, or job details.

---

## Customer Push Notifications

### Job Assigned

Title

Technician Assigned

Message

Your service request has been assigned to a technician.

---

### Technician En Route

Title

Technician En Route

Message

Your technician is on the way.

Include ETA only if it is provided.

---

### Technician Arrived

Title

Technician Arrived

Message

Your technician has arrived and work is beginning.

---

### Job Completed

Title

Service Completed

Message

Your service request has been completed.

---

### Job Closed

Title

Job Closed

Message

Your job has been successfully closed.

Thank you for choosing FieldOps.

---

## Technician Push Notifications

### New Assignment

Title

New Job Assigned

Message

You have received a new job assignment.

---

### Reassignment

Title

Job Reassigned

Message

A new job has been assigned to you.

---

### Reminder

Title

Action Required

Message

Please review your pending assignments.

---

## Dispatcher Push Notifications

### SLA Warning

Title

SLA Warning

Message

A job is approaching its SLA deadline.

---

### Technician Rejected

Title

Assignment Rejected

Message

A technician rejected the assigned job.

Redispatch is required.

---

## Output Rules

Return only

Title

Message

No markdown.

No explanations.

No JSON unless explicitly requested.