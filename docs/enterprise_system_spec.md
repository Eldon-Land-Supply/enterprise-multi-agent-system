# Enterprise Multi-Agent System Specification (Microsoft Ecosystem)

## Output Section 1 – Master Agent Inventory

| Agent ID | Agent Name | Agent Type | Primary Role | Environment(s) | Trigger Type | Upstream Dependencies | Downstream Consumers | Criticality |
|---------:|------------|-----------|-------------|----------------|--------------|-----------------------|----------------------|-------------|
| A01 | Cognitive Analysis & Generation Agent | Azure OpenAI | Generate grounded summaries, insights, and narrative content from enterprise data and ML outputs. | Dev / Test / Prod | Schedule (daily), Event (ad-hoc), API | Dataverse state, ML predictions (A04/A05), Data pipelines (A06) | Orchestrator (A02), Notification Agent (A10), Bot (A09) | High |
| A02 | Orchestration Flow Agent | Power Automate / Azure Logic Apps | Central coordinator sequencing parallel/serial flows, approvals, and downstream agent calls. | Dev / Test / Prod | Event (Dataverse), Schedule, API/Webhook | Dataverse events (A14), Bot inputs (A09), CI/CD (A11) | A01, A04, A05, A06, A07, A08, A10, external systems | High |
| A03 | ML Training Pipeline Agent | Azure ML Pipeline | Periodic retraining, validation, and promotion of models to registry. | Dev / Test / Prod | Schedule (nightly), CI/CD (A11) | Data pipeline outputs (A06), Feature store, Registry | A04, A05, Dataverse (A14), Orchestrator (A02) | High |
| A04 | ML Batch Scoring Agent | Azure ML Pipeline / Batch Endpoint | Produce batch predictions and risk scores, persist to Dataverse/OneLake. | Dev / Test / Prod | Schedule (nightly), Event (data-ready flag) | A03 models, Data pipeline (A06) | A01, A02, Dataverse (A14), Notifications (A10) | High |
| A05 | ML Real-Time Scoring Agent | Azure ML Managed Online Endpoint | Provide low-latency predictions for event-driven decisions. | Dev / Test / Prod | API (HTTPS), Event (orchestrator call) | A03 models, Feature store | A02, A01 | High |
| A06 | Data Pipeline Agent | Microsoft Fabric / Azure Data Factory | Ingest, cleanse, and publish curated datasets to OneLake/SQL for ML and OpenAI grounding. | Dev / Test / Prod | Schedule (hourly/daily), Event (file arrival) | Source systems, Purview policies (A13) | A03, A04, A01, Dataverse (A14) | High |
| A07 | Custom Enrichment Function Agent | Azure Functions | Perform custom transformations, enrichment, or third-party API calls invoked by orchestrator. | Dev / Test / Prod | HTTP trigger, Queue/Event Grid | A02 requests, External APIs | A02, Dataverse (A14) | Medium |
| A08 | Long-Running Workflow Agent | Azure Logic Apps (Stateful) | Handle multi-day approvals/escalations beyond standard flow timeouts; maintains durable state. | Dev / Test / Prod | Event (A02 call), Schedule (reminders) | A02, Dataverse (A14) | A02, A10, Teams users | Medium |
| A09 | Conversational Bot Agent | Teams/Outlook Bot / Copilot | Chat interface for users, routes intents to orchestrator; returns status and content. | Dev / Test / Prod | Message (Teams/Outlook), Adaptive Cards actions | Users, A01 outputs, Dataverse (A14) | A02, A10, Users | Medium |
| A10 | Notification & Approval Agent | Power Automate / Logic App | Delivers Teams/Outlook notifications, collects approvals, and writes decisions to Dataverse. | Dev / Test / Prod | Event (A02, Dataverse), Message actions | A02, A14 | A02, A01, A04, A08, Users | High |
| A11 | CI/CD & Release Agent | Azure DevOps Pipelines / GitHub Actions | Build, test, and promote all agents, prompts, and solutions across environments with gates. | Dev / Test / Prod | Schedule (nightly), Manual, PR trigger | Repos, IaC templates, Tests | All agents | High |
| A12 | Monitoring & Telemetry Agent | Azure Monitor / Log Analytics / App Insights | Collect metrics, logs, traces; drive alerts and health dashboards. | Dev / Test / Prod | Event (diagnostics), Schedule (health checks) | Agents emitting logs, A02 health pings | Ops teams, A02 auto-remediation | High |
| A13 | Governance & Guardrail Agent | Microsoft Purview + Azure Policy + Defender for Cloud | Enforce data classification, lineage, AI safety, and resource policy compliance. | Dev / Test / Prod | Continuous (policy evaluation), Event (scan), CI/CD | Data sources, Azure resources | All agents (policy enforcement, labels) | High |
| A14 | State & Event Broker Agent | Dataverse (with Event Grid) | Canonical state store for workflow instances, checkpoints, audit logs; emits change events. | Dev / Test / Prod | Event (row change), API | A02, A04, A06 inputs | A02 triggers, A01 grounding, A10 notifications, A12 audits | High |
| A15 | API & Integration Gateway Agent | Azure API Management | Secure, mediate, and version external/internal APIs used by orchestrator and functions. | Dev / Test / Prod | API calls, Event (quota/health) | A07, A02 | A02, External partners, A09 | Medium |
| A16 | Scheduler & Maintenance Agent | Azure DevOps Scheduled Jobs / Automation Account | Run weekly maintenance, smoke tests, and cleanup (temp data, stale runs). | Dev / Test / Prod | Schedule (weekly), Manual | IaC scripts, Resource inventory | A02, A03, A04, A12 reports | Medium |

## Output Section 2 – Per-Agent Deep Dives

### A01 – Cognitive Analysis & Generation Agent
1. **Summary**
   - Generates grounded summaries, insights, and narrative reports using Azure OpenAI with retrieval over curated data and ML outputs; supports daily briefs and ad-hoc reasoning to accelerate decision-making.
2. **Responsibilities & Tasks**
   - Generate daily briefs and risk summaries.
   - Provide ad-hoc reasoning for orchestrator requests.
   - Log outputs and prompts for audit.
   - **Tasks**
     1. Daily brief generation (06:00 CRON) using latest ML scores from Dataverse; outputs Dataverse rows and files; hand-off to A02/A10.
     2. Event-driven insight generation on new prediction records (Dataverse trigger); writes enriched insight to Dataverse; hand-off to A02.
     3. On-demand Q&A via HTTP from A02/A09; returns JSON with answer and citations; no persistence unless flagged.
3. **Integrations & Dependencies**
   - Azure OpenAI model deployment (gpt-4o/4o-mini) – inference (write/read via REST).
   - Azure Cognitive Search or Fabric OneLake query (optional) – retrieval grounding (read via REST/connector).
   - Dataverse – read ML outputs, write summaries (Dataverse connector/REST).
   - Azure Monitor/App Insights – log prompts/latency (write via SDK/REST).
4. **Triggers, Inputs & Outputs**
   - **Triggers:** CRON schedule; Dataverse row created/updated in Predictions table; HTTP POST from A02/A09.
   - **Inputs:** Dataverse tables `Predictions`, `DailyConfig`; retrieval index IDs; optional user query payload `{query, contextRefs}`.
   - **Outputs:** Dataverse `Insights` table columns: `InsightId`, `SourcePredictionId`, `SummaryText`, `RiskLevel`, `Citations`, `RunId`; JSON response to caller with `content`, `citations`, `runId`.
5. **Error Handling & Resilience**
   - Automatic retries on 429/5xx with exponential backoff; circuit breaker for model timeouts; writes failure record to Dataverse `AgentRuns` with status; idempotency via `RunId` and `SourcePredictionId`.
   - Resumes by re-reading pending `AgentRuns` with status `PendingRetry`.
6. **Security & Access**
   - Managed identity with role: `Cognitive Services OpenAI User`, Dataverse custom security role (read Predictions, write Insights), Storage Blob Data Reader if using retrieval files.
   - Private endpoint for OpenAI; VNET integration for Function/Logic App host; TLS enforced; Conditional Access for portal access.

### A02 – Orchestration Flow Agent
1. **Summary**
   - Central Logic App/Power Automate flow coordinating triggers, branching, approvals, and calls to other agents; maintains workflow instance IDs and state in Dataverse.
2. **Responsibilities & Tasks**
   - Listen to Dataverse changes and user/bot requests.
   - Route to parallel tasks (notifications, scoring, generation) with dependency-aware sequencing.
   - Handle retries, timeouts, and compensation.
   - **Tasks**
     1. Event-driven workflow start on new `WorkflowRequest` in Dataverse; orchestrates A06→A04/A05→A01 chain; writes checkpoints to `WorkflowState`.
     2. Parallel notification/approval fan-out via A10 after A01 completes; collects decisions and updates `Approvals` table.
     3. API endpoint for bots/external systems; validates payload, writes `WorkflowRequest`, triggers main orchestrator path.
3. **Integrations & Dependencies**
   - Dataverse triggers/connectors (read/write for state, approvals).
   - HTTP calls to A01, A04, A05, A07 (managed identity/APIM routes).
   - Start Fabric/ADF pipelines (A06) via REST/connector.
   - Teams/Outlook connectors via A10; Event Grid for callbacks.
4. **Triggers, Inputs & Outputs**
   - **Triggers:** Dataverse row add/update; HTTP Request trigger; recurrence for health checks (5 min).
   - **Inputs:** `WorkflowRequest` payload `{requestId, type, dataRefs, priority}`; approvals decisions; bot intents.
   - **Outputs:** Updates `WorkflowState`, `Approvals`, `AgentRuns`; invokes downstream APIs with `{runId, payload}`; emits Event Grid event `workflow.completed`.
5. **Error Handling & Resilience**
   - Built-in retries on actions; compensation steps to roll back Dataverse flags; dead-letter queue in Service Bus for failed payloads; resumable via `WorkflowState` checkpoints keyed by `runId`.
   - SLAs: critical path <5 min; uses concurrent control to prevent duplicate processing by checking `WorkflowState.status`.
6. **Security & Access**
   - Managed identity with Dataverse role (read/write core tables), Logic App contributor, ability to call APIM; IP restrictions via VNET integration; DLP policies in Power Platform to restrict connectors.

### A03 – ML Training Pipeline Agent
1. **Summary**
   - Azure ML pipeline performing feature extraction, training, evaluation, and model registration; promotes artifacts through MLOps stages.
2. **Responsibilities & Tasks**
   - Execute nightly training or drift-triggered retraining.
   - Validate metrics and register models with governance metadata.
   - **Tasks**
     1. Nightly run triggered by A11 schedule; pulls curated data from OneLake/SQL; trains model; logs metrics to ML Run History; registers model tagged with `dataVersion`.
     2. Drift detection job compares recent production data vs training baseline; if drift > threshold, enqueue retraining request in Dataverse `ModelDrift`; notify A02.
     3. Post-train validation invoking responsible AI checks; publish report link to Dataverse `ModelCards`.
3. **Integrations & Dependencies**
   - Fabric/ADF data sources (read); Azure ML workspace/compute; Model Registry (write); Dataverse for drift signals and model cards; Azure Monitor for metrics.
4. **Triggers, Inputs & Outputs**
   - **Triggers:** Azure DevOps pipeline schedule; manual run; drift event from A12.
   - **Inputs:** Datasets in OneLake/SQL; hyperparameter config in repo; `ModelDrift` entries.
   - **Outputs:** Registered model version; metrics JSON to Dataverse `ModelMetrics`; notification to A02; artifacts in Blob storage.
5. **Error Handling & Resilience**
   - Retry failed steps with Azure ML pipeline retry policy; checkpoint artifacts in Blob; on failure, create `AgentRuns` with status `Failed`; resume by rerunning from last successful step if pipelines support step caching.
6. **Security & Access**
   - Managed identity with `Contributor` on ML workspace, `Storage Blob Data Reader/Writer`, Dataverse role (write metrics/cards); VNET + private endpoints; Key Vault for secrets (if any).

### A04 – ML Batch Scoring Agent
1. **Summary**
   - Runs scheduled batch inference jobs writing predictions to Dataverse and OneLake for downstream summarization and workflows.
2. **Responsibilities & Tasks**
   - Batch score datasets nightly and on data-ready events.
   - Publish predictions with lineage and confidence.
   - **Tasks**
     1. Nightly batch scoring (02:00) using latest model from registry; writes to Dataverse `Predictions`; triggers A02 and A01.
     2. Event-driven scoring when A06 flags `DataReady` in Dataverse; limited scope run using delta data; writes to `Predictions` and signals A02.
3. **Integrations & Dependencies**
   - Azure ML batch endpoint or pipeline; Model Registry; Dataverse connector; OneLake/SQL data; Azure Monitor for job telemetry.
4. **Triggers, Inputs & Outputs**
   - **Triggers:** Schedule; Dataverse flag `DataReady=true`; API from A02.
   - **Inputs:** Input dataset path; model version; scoring config.
   - **Outputs:** Dataverse `Predictions` rows `{PredictionId, EntityId, Score, Confidence, ModelVersion, DataVersion}`; parquet outputs in OneLake; Event Grid event `predictions.completed`.
5. **Error Handling & Resilience**
   - Retry failed compute nodes; persist interim outputs to avoid reprocessing; idempotent writes by `PredictionId`; failures logged to `AgentRuns`.
6. **Security & Access**
   - Managed identity with ML execution role, Dataverse write permissions, Storage/OneLake access; private endpoints + VNET.

### A05 – ML Real-Time Scoring Agent
1. **Summary**
   - Managed online endpoint serving real-time predictions for orchestrator and bot requests; supports low-latency decisions.
2. **Responsibilities & Tasks**
   - Serve HTTPS scoring API via APIM.
   - Provide feature validation and schema enforcement.
   - **Tasks**
     1. Handle scoring requests from A02 (event-driven) with SLA <500ms p95; return prediction and confidence.
     2. Provide health probe endpoint; send metrics to A12.
3. **Integrations & Dependencies**
   - Azure ML online endpoint; API Management front-door; Dataverse optional logging; App Insights.
4. **Triggers, Inputs & Outputs**
   - **Triggers:** HTTPS POST via APIM; health probes schedule.
   - **Inputs:** JSON `{runId, features: {...}}` validated against schema.
   - **Outputs:** JSON `{prediction, confidence, modelVersion, requestId}`; optional log to Dataverse `OnlineScoringLogs`.
5. **Error Handling & Resilience**
   - Auto-scale replicas; request timeout 5s; circuit breaker on dependency failures; log to App Insights; retries from caller recommended.
6. **Security & Access**
   - Managed identity or OAuth2 with APIM; private endpoint; role `AzureML Online Endpoint Invocation`; WAF on APIM; rate limits per client.

### A06 – Data Pipeline Agent
1. **Summary**
   - Fabric/ADF pipeline ingesting, cleansing, and publishing curated data to OneLake/SQL; sets readiness flags for downstream ML and OpenAI agents.
2. **Responsibilities & Tasks**
   - Continuous/ scheduled ingestion and transformation.
   - Data quality validation and lineage capture.
   - **Tasks**
     1. Hourly incremental ingest from source systems; land in OneLake bronze; validate schema and quality.
     2. Transform to silver/gold tables; write metadata to Purview; update Dataverse `DataFeeds` with `DataReady` flag to trigger A04.
     3. Archive and partition management; emit Event Grid `data.pipeline.completed` to A02.
3. **Integrations & Dependencies**
   - Source connectors (SQL, SAP, SharePoint); OneLake/ADF; Dataverse; Purview; Event Grid.
4. **Triggers, Inputs & Outputs**
   - **Triggers:** Schedule; file-drop events; orchestrator HTTP start.
   - **Inputs:** Source tables/files; pipeline config in repo.
   - **Outputs:** OneLake tables; Dataverse `DataFeeds` entries `{FeedId, DataVersion, DataReady}`; lineage to Purview.
5. **Error Handling & Resilience**
   - Retry failed activities; store checkpoint of last watermark; dead-letter invalid records to Blob; alert via A12; resume using watermark.
6. **Security & Access**
   - Managed identity with source read permissions, OneLake/Storage Contributor, Dataverse role; private links to sources; Purview scanning permissions.

### A07 – Custom Enrichment Function Agent
1. **Summary**
   - Azure Function providing custom enrichment (e.g., external API calls, PII redaction) invoked by orchestrator or pipelines.
2. **Responsibilities & Tasks**
   - Process payloads reliably with idempotency keys.
   - **Tasks**
     1. HTTP-triggered enrichment from A02 with `payloadId`; returns enriched JSON; writes optional cache to Blob.
     2. Queue-triggered redaction for documents before OpenAI usage; outputs sanitized file to Storage and Dataverse reference.
3. **Integrations & Dependencies**
   - APIM (A15) for exposure; Blob Storage; Dataverse; external APIs.
4. **Triggers, Inputs & Outputs**
   - **Triggers:** HTTP; Storage Queue message.
   - **Inputs:** JSON `{payloadId, data}`; queue message with blob URL.
   - **Outputs:** JSON `{payloadId, enrichedData}`; sanitized file in Blob; Dataverse `Enrichment` row.
5. **Error Handling & Resilience**
   - Retries on transient errors; poison queue for failed messages; idempotency via `payloadId` cache; App Insights logs.
6. **Security & Access**
   - Managed identity; APIM subscription keys; Storage Blob Data Contributor; outbound via private endpoints; secrets in Key Vault.

### A08 – Long-Running Workflow Agent
1. **Summary**
   - Stateful Logic App managing multi-day approvals/escalations with waits and reminders; bridges orchestrations that exceed standard timeouts.
2. **Responsibilities & Tasks**
   - Hold durable timers and reminders; update Dataverse state.
   - **Tasks**
     1. Start on A02 call with `caseId`; create durable timer; send periodic reminders via A10; write status to `LongRunningCases`.
     2. Escalate to managers if SLA breached; notify A02 upon completion or timeout.
3. **Integrations & Dependencies**
   - Dataverse connector; Teams/Outlook via A10; Event Grid callback to A02.
4. **Triggers, Inputs & Outputs**
   - **Triggers:** HTTP from A02; recurrence for reminders.
   - **Inputs:** `caseId`, approver list, deadlines.
   - **Outputs:** Updates `LongRunningCases`; Event Grid `case.completed` or `case.escalated`.
5. **Error Handling & Resilience**
   - Built-in durable timers; retry actions; maintain state in Logic App storage; on restart, rehydrate from state.
6. **Security & Access**
   - Managed identity; Dataverse role; VNET integration; DLP policy.

### A09 – Conversational Bot Agent
1. **Summary**
   - Teams/Outlook bot or Copilot enabling natural language interaction; routes intents to A02 and returns status/content.
2. **Responsibilities & Tasks**
   - Intent recognition and routing; display Adaptive Cards; session tracking.
   - **Tasks**
     1. Handle user message; call A02 webhook with `intent`, `userId`; render response card.
     2. Collect approvals/feedback via card actions; post to Dataverse `Approvals` or `Feedback`.
     3. Allow users to query workflow status by `runId`; fetch from Dataverse and display.
3. **Integrations & Dependencies**
   - Bot Framework / Teams channel; APIM endpoint for A02; Dataverse for status; A01 for content on demand.
4. **Triggers, Inputs & Outputs**
   - **Triggers:** Teams/Outlook messages; Adaptive Card submit.
   - **Inputs:** User text; card payloads `{runId, action}`.
   - **Outputs:** Adaptive Cards with status/links; Dataverse updates.
5. **Error Handling & Resilience**
   - Graceful fallback messages; retry APIM calls; log to App Insights; idempotent card submissions via `runId` and `actionId`.
6. **Security & Access**
   - Azure AD app registration; Teams channel registration; uses OAuth SSO; scoped permissions to call APIM; Conditional Access for external access disabled.

### A10 – Notification & Approval Agent
1. **Summary**
   - Flow delivering notifications and collecting approvals via Teams/Outlook; writes decisions back to state store.
2. **Responsibilities & Tasks**
   - Send multi-channel alerts; capture approvals; escalate on timeout.
   - **Tasks**
     1. Trigger on A02/A04 events; send Teams message with Adaptive Card; await approval; update Dataverse `Approvals`.
     2. Send email fallback if Teams not delivered; track delivery status.
     3. Escalate after SLA breach; notify A02/A08.
3. **Integrations & Dependencies**
   - Teams connector; Outlook connector; Dataverse; Event Grid; App Insights.
4. **Triggers, Inputs & Outputs**
   - **Triggers:** Event Grid `workflow.notification`; Dataverse change; HTTP from A02.
   - **Inputs:** `runId`, `approvalType`, `recipients`, `message`, `links`.
   - **Outputs:** Dataverse `Approvals` entries; Event Grid `approval.completed`; user-facing messages.
5. **Error Handling & Resilience**
   - Retry sends; fallback channel; log failures; idempotent approval writes using composite key `runId+recipient`.
6. **Security & Access**
   - Managed identity with Graph delegated app permissions via connector; Dataverse write access; DLP enforced; secure Teams/Outlook connectors.

### A11 – CI/CD & Release Agent
1. **Summary**
   - Azure DevOps/GitHub pipelines handling build/test/deploy of all agent code, infrastructure templates, and Power Platform solutions with gates and approvals.
2. **Responsibilities & Tasks**
   - Standardize branching and release flow; run scheduled health builds.
   - **Tasks**
     1. PR-triggered build/test for Functions, ML code, Infrastructure as Code; publish artifacts.
     2. Multi-stage release Dev→Test→Prod deploying Logic Apps, Functions, ML models, Fabric pipelines, Power Platform solutions; approvals required.
     3. Nightly maintenance job (also A16) running smoke tests, dependency checks, and environment drift detection.
3. **Integrations & Dependencies**
   - Repos; Azure subscriptions; Power Platform service connections; ML registry; APIM; Key Vault; Service Connections with managed identities.
4. **Triggers, Inputs & Outputs**
   - **Triggers:** PRs; merges to main; schedules; manual run.
   - **Inputs:** Source code, ARM/Bicep/Terraform; solution packages.
   - **Outputs:** Deployed resources; release notes; artifacts; Dataverse `DeploymentLogs`.
5. **Error Handling & Resilience**
   - Pipeline retries; gated approvals; rollback using previous artifacts; immutable build artifacts for reproducibility.
6. **Security & Access**
   - Service principals/managed identities per stage; least-privilege roles (Contributor scoped to RG, Power Platform Deployment role); secrets in Key Vault; branch policies enforced.

### A12 – Monitoring & Telemetry Agent
1. **Summary**
   - Central monitoring using Azure Monitor, Log Analytics, and App Insights to track all agent health, KPIs, and alerts.
2. **Responsibilities & Tasks**
   - Collect logs/metrics; generate dashboards; emit alerts and health checks.
   - **Tasks**
     1. Aggregate diagnostics from all agents into Log Analytics; create workbooks for latency, success rates.
     2. Configure alerts (e.g., A05 latency >500ms, A04 failures >2) routing to Teams/Email (A10) and incident tool.
     3. Run synthetic health checks every 15 minutes hitting A02 health endpoint; write results to `HealthChecks` table.
3. **Integrations & Dependencies**
   - Log Analytics workspace; App Insights; Azure Monitor alerts; Dataverse for health logs; Teams/Email via A10.
4. **Triggers, Inputs & Outputs**
   - **Triggers:** Continuous ingestion; scheduled synthetic tests.
   - **Inputs:** Agent diagnostics; synthetic test results.
   - **Outputs:** Alerts; dashboards; Dataverse `HealthChecks` entries.
5. **Error Handling & Resilience**
   - Redundant alert rules; backup email channel; storage retention configured; ingestion failures alerted.
6. **Security & Access**
   - Managed identity with Monitoring Reader; workspace access; Action Group permissions; private link for ingestion where supported.

### A13 – Governance & Guardrail Agent
1. **Summary**
   - Enforces data classification, lineage, and policy compliance through Purview, Azure Policy, and Defender; provides AI safety guardrails.
2. **Responsibilities & Tasks**
   - Scan and classify data; apply labels; enforce allowed LLM usage and network/SKU policies.
   - **Tasks**
     1. Purview scans scheduled daily; update lineage for OneLake, Dataverse tables; raise alerts on sensitive data movement.
     2. Azure Policy assignments to enforce private endpoints, region restrictions, and approved SKUs for OpenAI/ML; non-compliant resources trigger remediation tasks.
     3. AI guardrail configuration (prompt filters, content safety) for A01 endpoints; export audit logs to Log Analytics.
3. **Integrations & Dependencies**
   - Purview account; Azure Policy; Defender for Cloud; OpenAI content filters; Dataverse labels.
4. **Triggers, Inputs & Outputs**
   - **Triggers:** Scheduled scans; policy evaluation events; CI/CD policy checks.
   - **Inputs:** Resource inventory; data sources; model endpoints.
   - **Outputs:** Classification labels; compliance reports; remediation tasks to A02/A16; audit logs.
5. **Error Handling & Resilience**
   - Re-scan retries; store lineage snapshots; policy non-compliance alerts; manual override workflow via A08.
6. **Security & Access**
   - Managed identity with Purview Data Curator/Reader roles; Policy Contributor (scope RG); Defender access; private connectivity to data sources.

### A14 – State & Event Broker Agent
1. **Summary**
   - Dataverse (with Event Grid) as canonical state store for workflows, predictions, insights, approvals, and audit trails; emits change events to orchestrate flows.
2. **Responsibilities & Tasks**
   - Persist all workflow state and logs; emit events for downstream triggers.
   - **Tasks**
     1. Maintain tables: `WorkflowRequest`, `WorkflowState`, `Predictions`, `Insights`, `Approvals`, `AgentRuns`, `HealthChecks`, `DataFeeds`, `ModelMetrics`, `ModelCards`.
     2. Event Grid integration on table changes to notify A02/A10/A04.
     3. Enforce data retention and auditing; expose APIs to agents via connectors.
3. **Integrations & Dependencies**
   - Power Platform connectors; Event Grid; Purview for classification; Backing Azure SQL for storage.
4. **Triggers, Inputs & Outputs**
   - **Triggers:** Row change events; API operations.
   - **Inputs:** Writes from all agents.
   - **Outputs:** Events to orchestrator; data to reports; audit exports to Log Analytics.
5. **Error Handling & Resilience**
   - Enable change tracking and optimistic concurrency; retries on transient errors; backup/restore policies; SLA-backed Dataverse availability.
6. **Security & Access**
   - Dataverse environment per stage; security roles per agent; field-level security for sensitive columns; DLP policies; conditional access; private endpoints (via VNET injection for connectors where applicable).

### A15 – API & Integration Gateway Agent
1. **Summary**
   - API Management provides secure, versioned front door for calling Functions, Logic Apps, and ML endpoints; handles quotas and authentication.
2. **Responsibilities & Tasks**
   - Publish APIs for A02→A07/A05; expose webhooks to partners; apply policies.
   - **Tasks**
     1. Deploy products for internal agents with managed identity validation and rate limits.
     2. Expose external partner webhook for data push; transform payload; route to A02 HTTP trigger.
     3. Collect API analytics; push to A12.
3. **Integrations & Dependencies**
   - Backend services: A05, A07, A02; Azure AD for OAuth; Log Analytics; Key Vault for certificates.
4. **Triggers, Inputs & Outputs**
   - **Triggers:** API calls.
   - **Inputs:** HTTP requests with JWT/Subscription key.
   - **Outputs:** Routed backend responses; logs to App Insights.
5. **Error Handling & Resilience**
   - Retry policies to backends; circuit breakers; caching for metadata; fallback response on outage; dead-letter to Service Bus for failed partner webhooks.
6. **Security & Access**
   - Managed identity; custom domains + TLS; WAF; IP allowlist for partners; OAuth2 with Entra; RBAC `API Management Service Contributor` for ops.

### A16 – Scheduler & Maintenance Agent
1. **Summary**
   - Scheduled jobs executing smoke tests, cleanup, and maintenance (e.g., purge temp data, rotate keys) to keep environments healthy.
2. **Responsibilities & Tasks**
   - Run weekly tasks and ad-hoc maintenance orchestrated via DevOps/Automation.
   - **Tasks**
     1. Weekly job to purge old `AgentRuns` and archives data per retention; executed via Runbook using managed identity.
     2. Run smoke tests hitting A02/A05/A01; publish results to `HealthChecks` and alert through A12.
     3. Key rotation and certificate renewal workflows with notifications.
3. **Integrations & Dependencies**
   - Azure Automation/DevOps pipelines; Dataverse; Key Vault; A12 for reporting.
4. **Triggers, Inputs & Outputs**
   - **Triggers:** Weekly schedule; manual run.
   - **Inputs:** Maintenance scripts; retention policies.
   - **Outputs:** Cleanup logs; health check reports; updated secrets.
5. **Error Handling & Resilience**
   - Retry failed runbooks; notifications on failure; idempotent cleanup using cutoff timestamps; runbook checkpoints.
6. **Security & Access**
   - Managed identity with least privilege (Dataverse maintenance role, Key Vault Crypto Officer for rotation); logging to Log Analytics; just-in-time elevation if needed.

## Output Section 3 – Installation & Configuration Framework

### Phase 0 – Prerequisites & Global Setup
1. Azure tenant with subscriptions for Dev/Test/Prod; Power Platform environments aligned to each; M365 tenant with Teams and Outlook enabled.
2. Create centralized Log Analytics workspace, Application Insights, and Azure Monitor action groups; create Purview account; create OneLake/Fabric workspace.
3. Establish naming/tagging standards (e.g., `org-prod-agent-a01`, tags: `env`, `owner`, `data_classification`).
4. Set up Git/Azure DevOps project with repos for: infra (Bicep/Terraform), Functions, ML code, Logic App definitions, Power Platform solutions, Fabric pipelines, prompts/configs.
5. Define branch policy (feature → develop → main) with PR validation builds; enable secret scanning.

### Phase 1 – Core Platform Resources
1. **Resource Groups**: Create per-environment RGs (e.g., `rg-mas-core-{env}`) for shared services; note dependencies for all agents.
2. **Dataverse (A14)**: Provision environments; create base tables listed in A14; enable change tracking and Event Grid; region close to other resources.
3. **Azure OpenAI (A01)**: Deploy OpenAI resource with models (gpt-4o, gpt-4o-mini); private endpoint; capacity planning for daily bursts.
4. **Azure ML (A03/A04/A05)**: Create workspace, compute clusters (training, batch), managed online endpoint; attach private endpoints; enable registry.
5. **Logic Apps / Power Automate (A02/A08/A10)**: Create Standard Logic App(s) with VNET integration; Power Platform DLP policies; solution-aware flows for portability.
6. **Azure Functions (A07)**: Create Function App (Consumption/Premium) with VNET integration, App Insights, Key Vault references.
7. **Fabric/ADF (A06)**: Create Fabric workspace or ADF instance; link to OneLake/Storage; enable managed VNET if available.
8. **API Management (A15)**: Deploy APIM (Developer in Dev, Premium in Prod) with VNET + custom domain; upload certs.
9. **Automation/DevOps (A11/A16)**: Set up Azure DevOps project or GitHub; create agent pools; create Automation Account for runbooks.
10. **Monitoring (A12)**: Connect all resources to Log Analytics; enable diagnostic settings for OpenAI, ML, Logic Apps, Functions, APIM, Dataverse.

### Phase 2 – Identity, Security & Compliance
1. Create managed identities/service principals per agent (A01–A16); map to Dataverse security roles; store in inventory.
2. Assign RBAC: OpenAI User (A01), Logic App/Function Contributor (A02/A07), Storage/OneLake roles (A03–A07), APIM API Caller (A02/A09), Monitoring Reader (A12), Policy Contributor (A13), etc.
3. Configure network security: VNET integration for Functions/Logic Apps; private endpoints for OpenAI, ML, Storage, Dataverse (via private link/virtual network gateways where applicable); NSGs restricted to necessary traffic.
4. Purview setup (A13): register data sources (OneLake, Dataverse, Storage); configure scans and classifications; enable label sync to M365 if required.
5. Azure Policy/Defender: assign policies enforcing region, private endpoints, SKU allowlist; enable Defender for Cloud for threat detection; configure AI content filters for OpenAI.
6. Logging & retention: set retention in Log Analytics; enable Diagnostic settings for Dataverse to send to Log Analytics and Storage; configure audit logs.

### Phase 3 – Agent Provisioning & Wiring
1. **A14 Dataverse**: Create tables listed; set primary keys, relationships; enable column security for sensitive fields; configure Event Grid publisher for change events.
2. **A06 Data Pipeline**: Build Fabric/ADF pipelines using repo templates; parameterize source connections; configure triggers (schedule + event); set output to OneLake and write `DataFeeds` table (`FeedId`, `DataVersion`, `DataReady`, `Timestamp`).
3. **A03 Training**: Define Azure ML pipeline YAML referencing compute; connect to OneLake datasets; set output registration to Registry; configure pipeline trigger from A11.
4. **A04 Batch Scoring**: Create ML batch pipeline; parameterize model version input; output to Dataverse via OData or via Function using managed identity; set schedule and Dataverse `DataReady` event trigger.
5. **A05 Online Scoring**: Deploy model as managed endpoint; front with APIM; define OpenAPI spec; configure auth (OAuth/Managed identity); set health probe.
6. **A01 OpenAI Agent**: Create Logic App/Function or orchestrated code using managed identity hitting Azure OpenAI; configure prompts and retrieval sources (search index or OneLake via SQL endpoint); store outputs in `Insights` table.
7. **A07 Function**: Deploy from repo; set app settings (APIM URL, Storage account, queue names); bind to queue trigger; ensure VNET.
8. **A02 Orchestrator**: Build Logic App/Power Automate flow using solution; triggers on `WorkflowRequest` Dataverse table and HTTP; call downstream agents via managed identity/APIM; log to `WorkflowState` and `AgentRuns`.
9. **A08 Long-Running Workflow**: Create stateful Logic App; HTTP trigger with `caseId`; configure durable timers; update Dataverse `LongRunningCases`; integrate with A10 for reminders.
10. **A10 Notification**: Create flow with Event Grid and Dataverse triggers; send Teams/Outlook Adaptive Cards; write approvals to `Approvals`; expose callback to A02.
11. **A09 Bot**: Register Bot Channel in Azure; configure Teams/Outlook channels; set messaging endpoint pointing to APIM → bot service; implement handler to call A02; deploy via DevOps pipeline.
12. **A15 APIM**: Import OpenAPI specs for A02, A05, A07; set policies (rate limit, JWT validation, rewrite); configure products/subscriptions per consumer.
13. **A12 Monitoring**: Configure diagnostic settings for each resource to Log Analytics; create workbooks and alerts; set action groups to A10 recipients.
14. **A13 Governance**: Apply Purview labels to Dataverse tables; configure OpenAI content filtering and prompt safety; deploy Azure Policy initiatives to resource groups.
15. **A11/A16 CI/CD & Maintenance**: Create pipelines for build/test/deploy; parameterize per-environment variables; create scheduled maintenance runbooks for cleanup, smoke tests; store scripts in repo.

_Example payloads_
- A02 → A01 request: `{ "runId": "<guid>", "predictionIds": ["123"], "mode": "daily" }`
- A01 → Dataverse `Insights`: columns `InsightId (GUID)`, `SourcePredictionId`, `SummaryText`, `RiskLevel`, `Citations (json)`, `RunId`, `CreatedOn`.
- A10 approval response to A02: `{ "runId": "<guid>", "approver": "user@org.com", "decision": "Approved", "comments": "Looks good" }`.

### Phase 4 – CI/CD & Release Management
1. Store infra templates, ML code, Functions, Logic App definitions, Power Platform solutions, and bot code in repos; enforce branching policies.
2. Build pipelines: lint/tests for Functions (e.g., `npm test`/`pytest`), ML unit tests, validation of Logic App JSON, PAC CLI checks for Power Platform solutions.
3. Release pipelines: multi-stage (Dev→Test→Prod) deploying via ARM/Bicep/Terraform for Azure resources; PAC CLI for Power Platform; Azure ML CLI for models/endpoints; Fabric/ADF deployment via API; APIM via DevOps task.
4. Approvals: require CAB/business approval before Prod; integrate security gates (SAST, dependency scanning); policy compliance checks from A13.
5. Artifact versioning: immutable builds stored in artifact feed; tag releases with semantic version; record deployment to `DeploymentLogs` in Dataverse.

### Phase 5 – Testing, Monitoring & Runbooks
1. **Testing**: Unit tests per codebase; integration tests hitting APIM to validate A02→A01/A05 paths; UAT via Power Platform test environment with sample data; load tests for A05.
2. **Monitoring**: Dashboards for SLA metrics (A05 latency, A04 success rate, A01 token usage); alerts routed via A10; weekly review of A12 reports.
3. **Runbooks**: Procedures to restart Logic App/Azure Function; re-run failed workflow by resetting `WorkflowState.status=Retry` and requeuing `WorkflowRequest`; escalation path: if A02 down → page on-call via A12; if A05 degraded → fail over to previous model version.

### Phase 6 – Future Extension Pattern
1. To add a new agent: append to inventory (Section 1), create deep-dive with same template (Section 2), define Dataverse tables/events it uses, and add connectors in A02/A15.
2. Introduce via feature flags in Dataverse config (`FeatureFlags` table) read by A02 to route traffic gradually; use shadow deployments behind APIM for new endpoints.
3. Extend CI/CD by adding pipeline stage and service connection; ensure monitoring rules added in A12 and policies in A13.
4. Run controlled pilot in Dev/Test with synthetic data before promoting to Prod; capture lineage in Purview and update runbooks accordingly.
