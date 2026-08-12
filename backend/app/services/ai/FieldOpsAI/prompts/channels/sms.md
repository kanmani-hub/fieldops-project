# SMS TEMPLATE

## Purpose

Generate short SMS messages for customers and technicians.

SMS messages must be concise, clear, and based only on the provided information.

Never invent information.

---

## General Rules

- Maximum length: 160 characters.
- Professional tone.
- No emojis.
- No markdown.
- No internal IDs.
- Never expose confidential information.
- Never invent ETA or technician details.

---

## Customer SMS

### Job Assigned

Your service request has been assigned to a technician.

---

### Technician En Route

Your technician is on the way.

Include ETA only if provided.

---

### Technician Arrived

Your technician has arrived and work is starting.

---

### Job Completed

Your service request has been completed.

Thank you for choosing FieldOps.

---

### Job Closed

Your service request has been closed.

Thank you for choosing FieldOps.

---

## Technician SMS

### New Assignment

You have received a new job assignment.

Please review it in the FieldOps application.

---

### Reassignment

A job has been reassigned to you.

Please review immediately.

---

## Dispatcher SMS

### SLA Warning

Attention:

A job is approaching its SLA deadline.

---

### Assignment Rejected

A technician rejected the assigned job.

Please reassign the job.

---

## Output Rules

Return only the completed SMS text.

Do not explain.

Do not include markdown.

Do not include JSON unless requested.