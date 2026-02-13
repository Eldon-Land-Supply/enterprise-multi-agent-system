# Architecture Notes

These notes describe how the session manager, API client and agents collaborate inside the enterprise multi-agent system. The goal is to clarify runtime responsibilities, expected message shapes and the hand-offs between components so contributors can extend the platform without guessing how requests are routed.

## Core Components

- **Session Manager**
  - Maintains session state (user context, correlation IDs, replayable history) across channels such as Teams and Outlook.
  - Normalises inbound events into a canonical envelope (`sessionId`, `actor`, `intent`, `payload`, `trace`), applies access controls and selects the next agent based on orchestration rules.
  - Persists checkpoints (e.g., Dataverse or durable storage) and publishes telemetry for each hop so long-running or multi-step conversations can be resumed.
- **API Client**
  - Provides a single entry point for calling downstream agent endpoints (Logic App, Azure Function, ML endpoint, or external SaaS API).
  - Handles cross-cutting concerns: retry policy, circuit breaking, request signing (managed identity or OAuth), schema validation and consistent error envelopes.
  - Emits metrics and structured logs so the orchestrator can attribute latency or failures to the correct dependency.
- **Agents**
  - Implement bounded responsibilities (analysis, orchestration, predictions, utilities, long-running workflows, conversational UX or data movement).
  - Expose predictable interfaces (HTTP/REST or webhook triggers). Each agent declares its contract in the inventory and its deep-dive doc under `docs/agents/`.
  - Return normalized responses that include `status`, `payload`, optional `actions` (e.g., follow-up agent to call) and `observability` fields for tracing.

## Interaction Flows

### Channel-to-Agent Round Trip
```mermaid
sequenceDiagram
    participant User
    participant Channel as Teams/Outlook Bot
    participant SessionManager
    participant APIClient
    participant Agent as Agent Endpoint

    User->>Channel: Message or event
    Channel->>SessionManager: Canonical request (sessionId, actor, payload)
    SessionManager->>SessionManager: Resolve session state and policy checks
    SessionManager->>APIClient: Invoke target agent with enriched payload
    APIClient->>Agent: HTTPS POST with auth headers
    Agent-->>APIClient: Structured response (status, payload, actions)
    APIClient-->>SessionManager: Parsed body + telemetry
    SessionManager-->>Channel: Rendered reply or next-step instruction
    Channel-->>User: Response
```

### Orchestrated Multi-Agent Path
1. **Trigger** – Session manager receives an event (schedule, webhook or chat message) and resolves routing metadata.
2. **Primary call** – The API client invokes the orchestrator agent (e.g., A02). A02 determines downstream dependencies (A01, A03, A04, A07) based on policy and intent.
3. **Chained calls** – For each downstream agent, the session manager:
   - Applies idempotency keys and correlation IDs.
   - Invokes the agent via the API client and captures telemetry.
   - Interprets `actions` returned by the agent to queue subsequent calls.
4. **Aggregation** – Results are merged into the session state (Dataverse records, blob storage artifacts, or in-memory state for the current run).
5. **User notification** – The conversational agent (A06) or workflow surfaces final outputs and any approvals required.
6. **Recovery** – On error, the session manager replays from the last checkpoint, and the API client enforces retry/backoff policies.

## Extension Guide

### Add a New Agent
1. **Define the contract**
   - Add an entry to `docs/master_inventory.md` with a unique ID and summary.
   - Draft a deep-dive spec under `docs/agents/` describing triggers, request/response schemas, security and fallback paths.
2. **Expose an endpoint**
   - Implement the agent as an HTTP-accessible endpoint (Logic App standard connector, Azure Function, ML endpoint, or webhook).
   - Return the normalized response structure (`status`, `payload`, optional `actions`, `observability`).
3. **Register with the session manager**
   - Update routing tables or configuration so the session manager can map intents to the new agent ID.
   - Configure identity (managed identity or app registration) and secrets in the API client so calls are authenticated.
4. **Test the agent**
   - Unit test the agent logic locally (e.g., `npm test`, `pytest`, or Logic App test harness) focusing on schema compliance and error handling.
   - Contract test the endpoint using `curl` or Postman with representative payloads to ensure the response envelope matches platform expectations.
   - Run an integration flow via the session manager sandbox, verifying telemetry and state checkpoints are written.

### Add a New Endpoint for an Existing Agent
1. **Motivation** – Identify the new capability (e.g., extra utility function or data source) and ensure the agent’s deep-dive spec is updated.
2. **Implement the endpoint** – Deploy the new function/Logic App/API route with proper authentication and a normalized response.
3. **Wire into routing** – Add endpoint metadata to the API client configuration and extend the session manager’s routing or action handlers.
4. **Validate** –
   - Smoke test with direct HTTP calls (200/4xx/5xx handling).
   - Run a session-level integration test to confirm the new endpoint is invoked and its telemetry appears in dashboards.
   - Update regression tests so orchestrated flows cover the new path.

## Testing Strategy Cheat Sheet
- **Unit** – Fast, local tests for agent business logic and schema validation.
- **Contract** – HTTP-level validation of request/response shapes, idempotency keys, and auth headers.
- **Integration** – End-to-end runs through the session manager, verifying chained calls, state persistence and user-facing outputs.
- **Observability checks** – Confirm logs, metrics and traces are emitted with correlation IDs so runbooks can diagnose issues quickly.
# System Interaction Flows

This document visualizes the runtime pathways that connect external entry points to the agents defined in this repository.  Use these diagrams to align implementation details (connectors, identities, retries, and observability) with the agent specifications.

## Webhook to Agent Execution (Sequence Diagram)

```mermaid
sequenceDiagram
    autonumber
    participant External as External Publisher
    participant Ingress as Webhook Ingress (API Management + Function)
    participant Queue as Durable Queue (Service Bus)
    participant Orchestrator as A02 Workflow Orchestrator
    participant Cognitive as A01 Cognitive Analysis
    participant Utility as A04 Utility Function
    participant Dataverse as Dataverse/OneLake
    participant Alerts as Monitoring & Teams

    External->>Ingress: POST event payload (JSON)
    Ingress->>Ingress: Validate signature & schema
    Ingress-->>External: 202 Accepted
    Ingress->>Queue: Enqueue message with correlation ID
    Queue-->>Orchestrator: Triggered run (peek-lock)
    Orchestrator->>Orchestrator: Branch by event type
    alt Content processing
        Orchestrator->>Cognitive: Invoke analysis (HTTP w/ backoff)
        Cognitive-->>Orchestrator: Summaries, insights
    else Utility action
        Orchestrator->>Utility: Call function for enrichment
        Utility-->>Orchestrator: Enriched payload
    end
    Orchestrator->>Dataverse: Persist results & run log
    Orchestrator-->>Queue: Complete message lock
    Orchestrator->>Alerts: Emit success/latency metrics
    Alerts-->>External: Optional callback/notification
```

### Notes
- API Management or an Azure Function implements request validation (signatures, replay protection) and normalizes headers before writing to Service Bus.
- The orchestrator runs under a managed identity to pull from the queue and call downstream agents.
- Results are stored in Dataverse/OneLake with correlation IDs for tracing back to the original webhook call.

## Request Queue and Backoff Path (Flowchart)

```mermaid
flowchart TD
    A[Webhook accepted] --> B[Service Bus enqueue]
    B --> C{Queue length > threshold?}
    C -- Yes --> D[Scale out orchestrator worker]
    C -- No --> E[Standard consumer]
    D --> F[Peek-lock next message]
    E --> F
    F --> G{Processing succeeded?}
    G -- Yes --> H[Complete lock & emit success metric]
    G -- No --> I[Retry with exponential backoff]
    I --> J{Max retries reached?}
    J -- No --> F
    J -- Yes --> K[Move to dead-letter queue + alert]
    K --> L[Create incident in Monitoring/Teams]
```

### Notes
- Exponential backoff parameters align with connector defaults (e.g., 4 retries, max 15 minutes delay) and should mirror the orchestrator’s configuration.
- Dead-letter messages must keep the original payload, correlation ID, and last error for deterministic replay.

## Caching Path for Reused Inputs

```mermaid
flowchart LR
    Start[Orchestrator receives request] --> Check{Cache lookup by hash of payload + model version}
    Check -- Hit --> Return[Return cached response + metadata]
    Check -- Miss --> Call[Invoke downstream agent]
    Call --> Store[Persist response in cache with TTL and tags]
    Store --> Return
```

### Notes
- Use Azure Cache for Redis or a Dataverse table keyed by payload hash and model version to avoid serving stale responses when models change.
- Cache entries store provenance (agent version, timestamp, correlation ID) to support audits.

## Alerting and Observability Signals

```mermaid
flowchart TD
    M[Orchestrator run completes] --> N{Outcome}
    N -- Success --> O[Emit metric: latency, throughput, cache hit rate]
    N -- Transient failure --> P[Retry scheduled]
    P --> Q[Log structured error + correlation ID]
    N -- Fatal failure --> R[Write to dead-letter queue]
    R --> S[Send Teams/Email alert]
    Q --> S
    O --> T[Dashboard/Workbook]
    S --> U[On-call triage SOP]
```

### Notes
- Alerts should include tenant/environment, agent ID, correlation ID, and last error to make triage actionable.
- Dashboards aggregate queue depth, cache hit rate, success/failure counts, and latency percentiles per agent.
- Use Azure Monitor action groups to route alerts to Teams channels and incident systems.
