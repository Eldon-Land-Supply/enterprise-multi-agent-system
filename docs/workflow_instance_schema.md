# Workflow Instance Schema

This document defines a standardized data schema for tracking workflow instances and steps across the multi‑agent system. Each entry in the workflow log should follow this schema to ensure reliable correlation, idempotency and auditing.

## Fields

| Field | Type | Description |
|------|------|-------------|
| **workflow_instance_id** | string (GUID) | Unique identifier for each end-to-end workflow instance. Generated when a new workflow begins. |
| **step_id** | string | Identifier for the current step within the workflow (e.g. "A01", "A02", "A03"). |
| **agent_id** | string | Identifier of the agent executing the step (e.g. "A01" for Cognitive Analysis & Generation). |
| **correlation_id** | string (GUID) | Identifier used to correlate all messages and logs across agents for a single workflow instance. Each workflow instance gets one correlation ID. |
| **idempotency_key** | string | Optional key used by idempotent steps to ensure the same work is not processed twice if a message is retried. |
| **status** | string (enum) | Current status of the step or workflow instance. Allowed values: `Pending`, `InProgress`, `Completed`, `Failed`, `Cancelled`. |
| **timestamp_started** | datetime | Time the step or workflow instance started. |
| **timestamp_completed** | datetime | Time the step or workflow instance completed (if applicable). |
| **parent_instance_id** | string (GUID) | For child workflows or sub‑processes, this links back to the parent workflow instance. Null for top‑level workflows. |
| **payload_reference** | string/URL | Pointer to the location of large payloads or artifacts related to the step (e.g. a blob in OneLake). |
| **policy_outcome** | string | Result of any policy checks or guardrails applied to the step (e.g. "allowed", "redacted", "blocked"). |

## Usage

- Each time a new workflow is initiated, the orchestrator generates a new `workflow_instance_id` and `correlation_id` and logs an entry with `status = Pending`.
- As agents process steps, they log their progress (e.g. `InProgress` → `Completed` or `Failed`) using the same `workflow_instance_id` and `correlation_id`.
- Agents should include the `idempotency_key` on events sent to Service Bus to allow safe retries.
- For long‑running sub‑processes (A05), use `parent_instance_id` to associate child workflow runs with the original instance.
- Store these records in Dataverse tables (e.g. `WorkflowRuns` and `WorkflowSteps`) and stream logs to Azure Monitor for observability.
- Use the `correlation_id` to join logs across Service Bus, Logic Apps, Functions, ML runs, and OpenAI requests.

This schema provides a consistent way to trace, audit and recover workflows across the distributed agent ecosystem.
