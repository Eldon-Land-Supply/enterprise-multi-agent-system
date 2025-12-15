## A02 – Workflow Orchestrator (Power Automate / Logic App Agent)

### Summary

The Workflow Orchestrator coordinates the entire multi‑agent process.  Implemented as a Power Automate cloud flow or an Azure Logic App, it listens for events, triggers the appropriate agents, handles branching logic and retries, and integrates with external systems.  It is the “glue” binding the cognitive, predictive and utility agents together.

### Responsibilities & Tasks

**Responsibilities**

- Start and sequence agent executions based on schedules or data events.
- Manage branching, conditional logic, approvals and loops within workflows.
- Integrate with Dataverse, Dynamics, SharePoint, Teams and other services via connectors.
- Handle errors, retries and compensating actions.

**Concrete tasks**

1. **Daily orchestrated run** – triggers at business start time (e.g. 08:00) to call A03 (Predictive Insights) and then A01 (Cognitive Analysis).  Inputs: None (time‑based trigger).  Outputs: Summaries and notifications created by downstream agents.
2. **Event‑driven flows** – listens on Dataverse table `Summaries` for new rows; when a new summary appears, triggers follow‑up tasks such as sending emails, updating records or notifying Teams channels.  Inputs: Dataverse change event payload.  Outputs: Notifications and record updates.
3. **Ad hoc requests** – exposes an HTTP trigger or logic app custom connector to accept requests from A06 (Bot) or other systems.  Parses the request, calls the appropriate agent (e.g. A01 or A04), and returns the result.  Inputs: JSON request.  Outputs: JSON response and optional notifications.
4. **Error handling and compensation** – monitors the status of each call; on failure, retries up to a configurable limit.  If a step ultimately fails, logs to Dataverse and sends an alert via Teams.  Implements compensating actions if needed (e.g. roll back record creations).

### Integrations & Dependencies

| Component | Purpose | Direction | Connector/Protocol |
|-----------|--------|---------|--------------------|
| **Dataverse** | Central state store; triggers flows on data changes; stores workflow run logs | Read & write | Dataverse connector |
| **Azure ML endpoint** | Invoke predictive models (A03) | Call | HTTP |
| **Azure OpenAI service** | Invoke cognitive analysis (A01) | Call | HTTP |
| **Azure Functions (A04)** | Execute custom logic or integrations | Call | HTTP trigger |
| **Logic Apps / Power Automate** | Built‑in connectors to Dynamics, SharePoint, Outlook, Teams, SAP etc. | Read & write | Connector framework |
| **Teams / Outlook** | Send notifications, approvals and messages | Write | Teams/Outlook connectors |

### Triggers, Inputs & Outputs

**Triggers**: Scheduled recurrences; Dataverse row changes; external HTTP requests; Teams messages (via connectors).  Each trigger starts a specific branch of the workflow.

**Inputs**: For scheduled flows, there are no external inputs.  Event‑driven flows consume Dataverse change payloads or HTTP request bodies.  All workflows refer to environment variables and connection references configured in the environment.

**Outputs**: Primarily calls to other agents or services.  Also writes new rows to Dataverse (e.g. workflow logs, approvals) and sends emails/Teams messages.

### Error Handling & Resilience

The orchestrator uses built‑in retry policies on each connector action (exponential backoff).  It groups related actions into scopes with separate error handling paths.  Failures are logged to Dataverse and to Azure Monitor.  Long‑running processes are split into child workflows or stateful Logic Apps (A05) to avoid timeouts.  All runs are idempotent; repeated triggers with the same correlation ID do not re‑execute completed steps.

### Security & Access

Runs under a service principal or managed identity with least‑privilege access to Dataverse, connectors and custom endpoints.  Each connection uses separate credentials to facilitate granular RBAC.  Sensitive data is protected via Purview classification and policy enforcement; the orchestrator does not expose secrets in logs or outputs.  Audit logs are captured via Dataverse and Log Analytics.
