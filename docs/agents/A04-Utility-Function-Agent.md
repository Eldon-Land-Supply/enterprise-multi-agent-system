## A04 – Utility Function Agent (Azure Function)

### Summary

The Utility Function Agent is implemented as one or more Azure Functions.  It handles bespoke logic that does not neatly fit into the other agents, such as data transformation, enrichment, communication with third‑party APIs and other custom operations.  Functions are stateless and event‑driven; they scale on demand and return results to the orchestrator.

### Responsibilities & Tasks

**Responsibilities**

- Perform custom data processing or transformations beyond what is easily expressed in a Logic App.
- Integrate with external services where no native Power Automate connector exists (e.g. proprietary APIs).
- Serve as a lightweight API endpoint for ad‑hoc operations.
- Provide reusable microservices that other agents can call via HTTP.

**Concrete tasks**

1. **Data transformation** – triggered by A02 when raw data needs to be cleaned or reshaped before being passed to A01 or A03.  Inputs: JSON payload.  Outputs: Cleaned JSON returned to caller.
2. **External API integration** – acts as a proxy for calling an external service (e.g. a CRM API) that requires custom authentication or logic.  Inputs: Parameters from A02; outputs: API response returned to A02 and optionally logged in Dataverse.
3. **Formatting outputs** – converts generated text or data into PDF/CSV/Excel formats for download or emailing.  Inputs: Data record IDs; outputs: Binary file stored in Blob Storage and download link sent back.
4. **Utility callbacks** – can be triggered via HTTP by other systems or agents for miscellaneous utilities (e.g. generating a GUID, fetching a secret from Key Vault).  Always returns JSON responses.

### Integrations & Dependencies

| Component | Purpose | Direction | Connector/Protocol |
|-----------|--------|---------|--------------------|
| **Power Automate/Logic App (A02)** | Calls the function endpoints; receives responses | Trigger & callback | HTTP |
| **Azure Blob Storage** | Stores generated files such as PDFs or CSVs | Write | Azure Storage SDK |
| **External APIs** | Performs HTTP calls to third‑party services on behalf of workflows | Read & write | REST with custom auth |
| **Key Vault** | Retrieves secrets or connection strings securely | Read | Key Vault SDK |

### Triggers, Inputs & Outputs

**Triggers**: HTTP triggers invoked by A02; queue or Event Grid triggers if connected to asynchronous workflows.

**Inputs**: JSON payloads containing data to process or parameters for external API calls.  For file generation tasks, IDs of the data records to be exported.

**Outputs**: JSON responses including processed data, external API responses or file download URLs.  Error responses include error codes and messages.

### Error Handling & Resilience

Functions implement try/catch logic and return structured error objects.  Transient errors result in retries if invoked via a queue trigger; HTTP calls return appropriate HTTP status codes.  Azure Functions are stateless; any persistent data is stored in Blob Storage or Dataverse.  Monitoring is handled via Application Insights.

### Security & Access

Each function runs under a managed identity with access only to the resources it needs (e.g. Blob Storage, Key Vault).  HTTP triggers require AAD authentication or function keys; only authorized callers (such as the orchestrator) can invoke them.  Outbound calls to external APIs use stored credentials retrieved from Key Vault.  Audit logs are emitted to Azure Monitor.
