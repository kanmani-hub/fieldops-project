# DispatchAgent Migration Documentation

This document describes the final production-hardened design, contracts, validations, and lifecycle operations of the migrated AI `DispatchAgent` and `DispatchService`.

## Authoritative Schemas & Literals

All layers (schemas, prompt, service logic, and tests) utilize the exact same literals defined in the Pydantic schema:

* **Event Literals (`DispatchContext.event`)**:
  * `"TECHNICIAN_ACCEPTED"` (Literal)
  * `"TECHNICIAN_REJECTED"` (Literal)
  * `"TECHNICIAN_TIMEOUT"` (Literal)
* **Action Literals (`DispatchDecision.action`)**:
  * `"complete_assignment"` (Literal)
  * `"assign_next_candidate"` (Literal)
  * `"request_replanning"` (Literal)
  * `"manual_review"` (Literal)
* **Status Literals (`DispatchDecision.status`)**:
  * `"ACCEPTED"` (Literal)
  * `"REJECTED"` (Literal)
  * `"TIMEOUT"` (Literal)

## Hardened Validations & Protections

### 1. Unsupported-Action Rejection
The service validates that the recommended action is one of the four authoritative literals. Any other action triggers a `RuntimeError("Dispatch Agent returned an unsupported action.")` to prevent unrecognized workflows.

### 2. Manual-Review Rule
If the AI returns `"manual_review"` as the workflow action, it bypasses all event-to-status alignment rules to allow operator intervention on inconsistent context. The service executes zero database mutations and performs no database commits (no meaningless save calls).

### 3. Missing Technician & Mismatched Job Validation
For every candidate returned by `get_remaining_candidates()`:
- The service verifies that `r_assign.job_id` matches `job.id`. Mismatched jobs raise a `ValueError`.
- The service loads the technician. If the technician does not exist, the service raises `ValueError("A remaining assignment references an unavailable technician.")` to prevent silently ignoring candidates and skewing calculations.

### 4. Authoritative Tenant Verification
Both `Job` and `Technician` SQL models expose a `tenant_id` column. The service strictly requires a non-blank, non-empty `tenant_id` on:
- The current technician.
- All remaining candidate technicians.
These values must match `job.tenant_id` exactly, or a `ValueError` is raised (no `hasattr` or silent skipping).

### 5. Service-Level Timeout Behavior
If the execution exceeds its timeout limits:
1. A lifecycle timeout occurs.
2. The service raises a `RuntimeError("Dispatch Agent failed while generating a recommendation.")`.
3. The agent state is marked `ERROR` immediately following execution, and is cleared to `TERMINATED` when exiting the context block.
4. All database mutations and repository commits are skipped.

## Testing & Verification

- **Deterministic Timeout Coordination**: Blocking workers are coordinated via `threading.Event` and released via `try/finally`. Unbounded polling loops are replaced with `asyncio.wait_for` to prevent hanging processes.
- **Responsiveness Check**: Verified deterministically by executing separate async tasks concurrently while the main orchestrator is blocked.
- **Zero Side-Effects Assertions**: In all invalid contract paths, mocks are explicitly verified to ensure no mutation or persistence methods (`mark_accepted`, `mark_rejected`, `mark_timeout`, `promote_next_candidate`, `assign_technician`, `update_status`, `increment_jobs`, and `save`) are executed.

## Excluded Architectures
This migration does **not** implement:
- Registry integration
- Persistent Agent State
- Communication Bus integration
- Health Monitoring FastAPI lifespan/heartbeat routes
