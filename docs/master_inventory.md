## Master Agent Inventory

This table enumerates all agents needed to implement the multi‑agent AI/automation system.  Each agent is given a short identifier, a descriptive name, its type, a succinct summary of its primary role, the trigger mechanism, its upstream dependencies and downstream consumers, and a rough criticality level.  Long narratives are avoided here to keep the table readable; refer to the per‑agent documents under `docs/agents/` for complete specifications.

| Agent ID | Agent Name | Type | Primary Role | Trigger | Upstream Dependencies | Downstream Consumers | Criticality |
|---------:|------------|------|-------------|--------|----------------------|---------------------|-----------|
| A01 | Cognitive Analysis & Generation | Azure OpenAI | Generate summaries and insights from unstructured text; provide human‑readable narratives | Scheduled & event | Dataverse data, ML predictions | Power Automate flows, Teams notifications | High |
| A02 | Workflow Orchestrator | Power Automate/Logic App | Coordinate the end‑to‑end workflow; integrate across services; manage conditions, approvals and retries | Scheduled & event | Dataverse change events, API calls | All downstream agents; external systems | High |
| A03 | Predictive Insights Provider | Azure Machine Learning | Train and serve predictive models; batch and real‑time scoring | Scheduled & API | Data pipelines, model registry | Dataverse (prediction results), orchestrator | High |
| A04 | Utility Function Agent | Azure Function | Perform custom logic such as data transformation, enrichment or third‑party API calls | Event or API | Orchestrator requests, external triggers | Orchestrator; storage | Medium |
| A05 | Long‑Running Workflow Agent | Azure Logic App | Handle stateful, multi‑day processes (approvals, waits) beyond a single Power Automate flow’s lifetime | Event | Orchestrator invocation | Orchestrator; Teams | Medium |
| A06 | Conversational Bot Agent | Teams/Outlook Bot | Provide a chat interface for end‑users; route user requests to the orchestrated workflows | Message | User messages | Orchestrator; Dataverse | Medium |
| A07 | Data Pipeline Agent | Microsoft Fabric / Azure Data Factory | Perform heavy ETL/ELT operations; prepare large datasets in OneLake for ML/OpenAI consumption | Scheduled & API | Source data stores | Dataverse; ML agent | Medium |

| A08 | Health & Recovery Agent | Azure Function / Service Bus | Monitor system health, reprocess failed messages, and alert operators; improve workflow reliability | Scheduled & event | Service Bus dead-letter queues; WorkflowRuns in Dataverse | Orchestrator; Teams notifications | High |
