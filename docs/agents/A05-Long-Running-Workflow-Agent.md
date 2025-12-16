## A05 – Long‑Running Workflow Agent (Stateful Logic App)

### Summary

Some business processes span hours or days, require waiting for approvals or external events, or involve multiple pauses and resumptions.  The Long‑Running Workflow Agent is an Azure Logic App configured in stateful mode to orchestrate these extended scenarios.  It complements the main orchestrator by persisting state across sessions and handling complex human‑in‑the‑loop steps.

### Responsibilities & Tasks

**Responsibilities**

- Manage long‑duration workflows that cannot be completed within a single Power Automate run.
- Pause execution while waiting for approvals, external files, or scheduled delays.
- Persist context and resume reliably after waits, retries or service restarts.
- Communicate status updates back to the main orchestrator and to end‑users.

**Concrete tasks**

1. **Approval workflows** – receive a request from A02 to initiate an approval.  Create an approval record in Dataverse, send a Teams adaptive card to the approver, wait for the response, and then notify A02 of the outcome.  Persist the state during the wait.
2. **Multi‑stage approvals** – chain multiple approvals (e.g. peer review followed by manager approval).  Handle rejection at any stage.  Input: Request details from A02.  Output: Final decision written to Dataverse and callback to A02.
3. **Delayed actions** – schedule follow‑up actions (e.g. send reminder emails three days after an event if no response).  Use built‑in delay connectors.  Persist schedule tokens for resumption.
4. **Complex condition handling** – evaluate a combination of signals (e.g. multiple document uploads plus a manual confirmation) before proceeding.  Store partial completions and resume when all conditions are satisfied.

### Integrations & Dependencies

| Component | Purpose | Direction | Connector/Protocol |
|-----------|--------|---------|--------------------|
| **Power Automate/Logic App (A02)** | Invokes long‑running process and receives completion callbacks | Trigger & callback | HTTP / Dataverse |
| **Dataverse** | Stores workflow state, approval records and logs | Read & write | Dataverse connector |
| **Teams / Outlook** | Sends approval cards and reminders; receives responses | Write & read | Teams adaptive card connector |
| **Delay / Schedule connectors** | Implement time‑based waits | Internal | Logic App connectors |

### Triggers, Inputs & Outputs

**Triggers**: HTTP requests from A02; recurrence triggers for scheduled follow‑ups; incoming responses from Teams adaptive cards.

**Inputs**: Approval request details (approver, context); parameters for delays; conditions to monitor.  Persisted in Dataverse so the Logic App can resume at any time.

**Outputs**: Writes approval decisions and workflow status back to Dataverse; sends notifications to A02 and to users via Teams/Outlook; triggers subsequent actions if needed.

### Error Handling & Resilience

Stateful Logic Apps automatically handle retries and persist the execution history.  If an approval times out or is declined, the workflow logs the outcome and calls a compensating branch.  Execution state is stored in durable storage ensuring that service restarts do not lose progress.  All wait times and timeouts are configurable.

### Security & Access

The Logic App runs under a managed identity with limited permissions to Dataverse and messaging connectors.  It does not store sensitive data in outputs; instead, it retrieves the necessary context at each step from Dataverse.  Approvals use secure adaptive cards that require user authentication.  All actions are logged for auditing.
