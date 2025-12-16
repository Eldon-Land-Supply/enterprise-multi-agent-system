## A08 – Health & Recovery Agent (Azure Function / Service Bus)

### Summary

The Health & Recovery Agent monitors the health of the multi‑agent system and ensures that workflows complete successfully. It watches for failed messages or stuck workflow instances, requeues or reprocesses them when possible, and alerts operators of persistent issues. This agent improves system reliability by proactively detecting and addressing issues.

### Responsibilities & Tasks

**Responsibilities**

- Monitor Service Bus topics and queues for dead‑lettered or unprocessed messages.
- Scan workflow run logs and Dataverse tables for stuck or failed instances.
- Attempt to reprocess or replay messages to recover from transient failures.
- Notify operators via Teams or other channels when issues exceed thresholds.
- Publish health events for other agents to consume.

**Concrete tasks**

1. **Dead‑letter queue sweep** – Runs on a schedule (e.g. every 5 minutes). Reads messages from Service Bus dead‑letter queues (DLQs) for each topic/queue used by the multi‑agent system. Attempts to re‑queue the message or forwards it to the orchestrator (A02) for manual handling. Logs the outcome to Dataverse.
2. **Stuck workflow detection** – Periodically queries the `WorkflowRuns` table in Dataverse to identify workflows that have not progressed within a defined timeout. Creates a `workflow.failed` or `workflow.stuck` event and publishes it to the Service Bus topic. Optionally triggers A02 to restart or abort the run.
3. **Alerting** – Aggregates metrics such as the number of DLQ messages, failed workflows, or error rates. When thresholds are exceeded, sends a notification to a Teams channel or creates an incident ticket. Provides details and recommended remediation steps.
4. **Cleanup & maintenance** – Purges processed messages from queues and cleans up old logs. Archives health metrics to Log Analytics for long‑term analysis. Maintains the schedule and configuration via App Configuration.

### Integrations & Dependencies

| Component | Purpose | Direction | Connector/Protocol |
|-----------|--------|---------|--------------------|
| **Azure Service Bus** | Read from dead‑letter queues; publish `workflow.failed` and other health events | Read & write | Service Bus SDK |
| **Dataverse** | Read `WorkflowRuns` table for workflow statuses; write health logs | Read & write | Dataverse connector |
| **Power Automate/Logic App (A02)** | Notify orchestrator of failed workflows; invoke recovery actions | Call | HTTP |
| **Teams / Incident Management** | Send alerts and notifications to operators | Write | Teams connector or webhook |
| **Log Analytics / Application Insights** | Store health metrics and logs | Write | SDK |

### Triggers, Inputs & Outputs

**Triggers**: Time‑based schedule (e.g. every 5 minutes); Service Bus message arrival events (dead‑letter events); manual invocation for on‑demand scans.

**Inputs**: Service Bus queue/topic names; configuration settings for thresholds (e.g. max DLQ messages before alert); Dataverse table names; environment parameters from App Configuration.

**Outputs**: Requeued messages; entries in `HealthLogs` or similar Dataverse table; `workflow.failed` events published to Service Bus; alerts sent to Teams or incident platforms.

### Error Handling & Resilience

If reprocessing a message fails, the agent moves it to a poison queue and logs the failure. It always processes messages idempotently to avoid duplicates. The agent stores checkpoints of its progress in Dataverse or durable storage so it can resume after interruptions. All operations use retries with exponential backoff.

### Security & Access

Runs under a managed identity assigned the `Azure Service Bus Data Receiver` and `Data Sender` roles on relevant queues/topics, and appropriate roles on Dataverse (read/write to `WorkflowRuns` and `HealthLogs`). It has permission to post to Teams or incident webhooks. Secrets such as queue names or webhook URLs are stored in Key Vault and loaded via App Configuration. Logging is done via Azure Monitor and respects data classification policies.
