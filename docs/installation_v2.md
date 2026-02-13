# Installation & Configuration Framework (Version 2)

This installation guide describes how to set up the **improved architecture (Version 2)** for the enterprise multi‑agent system.  It extends the original installation by adding steps for the new messaging layer (Azure Service Bus), API Management, centralised configuration/secrets management, and the Health & Recovery Agent.  The phases from the original guide remain, but several steps have been expanded or added.  For context on the changes, refer to `architecture_v2.md`.

## Phase 0 – Prerequisites & Global Setup

The prerequisites remain unchanged from the original guide:

- Ensure your Azure subscription has quotas for OpenAI, Machine Learning, Logic Apps, Service Bus, API Management and Storage.
- Provision a Power Platform environment (Dataverse) and a Microsoft 365 tenant with Teams and Outlook enabled.
- Set up central logging and monitoring (Log Analytics workspace + Azure Monitor), a Purview account for governance, and a OneLake/Fabric workspace if you will use Microsoft Fabric for pipelines.
- Establish naming conventions, tagging standards and version control as in the original guide.

## Phase 1 – Core Platform Resources (Version 2)

Provision the following resources **for each environment (Dev, Test, Prod)**.  Steps marked **(new)** highlight additions introduced by the improved architecture.

1. **Resource groups** – create resource groups to contain all Azure resources for that environment.
2. **Dataverse** – provision a Dataverse environment and create base tables (`Summaries`, `Predictions`, `WorkflowRuns`, `Approvals`, etc.).  Dataverse remains the system of record for workflow state and audit logs.
3. **Azure OpenAI** – deploy the desired model (e.g. GPT‑4) and note the deployment name and endpoint URL.
4. **Azure ML Workspace** – deploy an ML workspace, compute clusters and data stores pointing to OneLake and Dataverse.
5. **Logic Apps Standard & Power Automate** – create Logic Apps Standard as the **control plane** for orchestration and Power Automate for human workflows (approvals, notifications).  Configure VNET integration and private endpoints for Logic Apps.  Create connection references to Dataverse, Service Bus, API Management and other services.
6. **Azure Functions** – create a Function App (consumption plan or App Service plan) with separate deployment slots for Dev/Test/Prod.  This hosts custom utility functions and the Health & Recovery Agent.
7. **Service Bus (Message Broker) – new** 
   - Create a **Service Bus namespace** and define topics such as `workflow.started`, `data.prep.completed`, `ml.batch.completed`, `llm.summary.completed` and `workflow.failed`.
   - Enable sessions and dead‑lettering on each topic/subscription.  Assign **send** and **listen** rights to the appropriate agent identities (A02 orchestrator listens to all topics; other agents publish/subscribe as needed).
8. **API Management – new**
   - Provision an **Azure API Management** instance (Developer or Consumption tier for Dev/Test; Premium for Prod if private networking is required).
   - Import Azure Function endpoints, ML inference endpoints and any custom APIs as operations.  Configure authentication via Entra ID, apply rate limiting and CORS policies, and enable logging to Application Insights.
   - If Logic Apps are deployed in a VNET, configure API Management in internal mode and connect it to the VNET.
9. **Key Vault & App Configuration – new**
   - Deploy an **Azure Key Vault** for secrets (connection strings, API keys) and an **Azure App Configuration** store for non‑secret settings (feature flags, endpoint URLs, topic names, correlation settings).
   - Assign managed identities to agents (A01–A08) with appropriate permissions (e.g. `Key Vault Secrets User`, `App Configuration Data Reader`).  Use Key Vault references in Logic Apps and Functions to retrieve secrets at runtime.
10. **Microsoft Fabric / Data Factory** – create data pipelines using Fabric or Data Factory as needed and link to OneLake.
11. **Application Insights** – enable telemetry for Functions, ML endpoints, Logic Apps and API Management to capture metrics and traces.

## Phase 2 – Identity, Security & Compliance (Version 2)

In addition to the original identity and security steps, apply the following updates:

- **Managed identities & RBAC** – create a managed identity for each agent (A01–A08).  Assign `Azure Service Bus Data Sender` or `Data Receiver` roles as needed on the Service Bus namespace and topics.  Grant `API Management Service Contributor` rights to the orchestrator identity if it needs to create or modify APIs.  Assign `Key Vault Secrets User` and `App Configuration Data Reader` roles on the configuration stores.
- **Secrets & configuration** – store all connection strings, API keys and credentials in Key Vault.  Store topic names, API URLs and feature flags in App Configuration.  Use managed identities from Logic Apps and Functions to retrieve these values at runtime.
- **Purview policies & AI guardrails** – extend data classifications and AI guardrail policies to cover Service Bus and API Management logs.  Ensure the LLM guardrail layer (implemented as a function/Logic App) inspects prompts and responses for sensitive data before they are sent to or from the Azure OpenAI service.
- **Logging & monitoring** – configure diagnostic settings for Service Bus and API Management to send logs to Log Analytics.  Set up alerts for dead‑letter queue growth, excessive throttling or failed API calls.

## Phase 3 – Agent Provisioning & Wiring (Version 2)

Provision each agent in dependency order, updating their responsibilities to use the new messaging layer and API gateway:

1. **A03 – Predictive Insights Provider**
   - Develop ML pipelines and models as before.  After each batch scoring or model retraining, **publish a `ml.batch.completed` event to Service Bus** with the workflow instance and correlation IDs.
   - Expose the ML inference endpoint via API Management to allow secure calls from the orchestrator (A02) and other agents.
2. **A07 – Data Pipeline Agent**
   - Build Fabric/Data Factory pipelines for data ingestion and preparation.  When a pipeline completes, **publish a `data.prep.completed` event to Service Bus**.  For on‑demand runs, expose a REST trigger via API Management.
3. **A01 – Cognitive Analysis & Generation**
   - Implement the agent as a Logic App or Function that is triggered by Service Bus messages (e.g. `workflow.started`).  Retrieve prompts and retrieval data from Dataverse or cognitive search as needed.
   - Call the Azure OpenAI endpoint via API Management, passing through the LLM guardrail layer.  After generating a summary or insight, **publish a `llm.summary.completed` event to Service Bus** and write the result to Dataverse.
4. **A04 – Utility Function Agent**
   - Deploy custom Azure Functions for data transformation, enrichment or external API integration.  Expose each function via API Management.  Functions may be triggered directly by the orchestrator (A02) or by Service Bus messages.
5. **A02 – Workflow Orchestrator**
   - Implement the orchestrator as a **Logic App Standard**.  It subscribes to Service Bus topics to react to events (`workflow.started`, `data.prep.completed`, `ml.batch.completed`, `llm.summary.completed`, `workflow.failed`).
   - Use API Management to call downstream services (Functions, ML endpoints, OpenAI).  Update Dataverse records with workflow state and maintain correlation IDs.
   - Include scopes for error handling, retries and compensation.  When a workflow is initiated, publish a `workflow.started` event.
6. **A05 – Long‑Running Workflow Agent**
   - Build stateful Logic Apps for multi‑day processes requiring approvals.  Use Service Bus events to resume after waits and persist state in Dataverse.  Integrate Teams approvals for human decision points.
7. **A06 – Conversational Bot Agent**
   - Develop a bot using the Bot Framework; register with Azure Bot Service.  Use Power Automate flows for human approvals and notifications.  The bot invokes the orchestrator via API Management to start workflows or query status.
8. **A08 – Health & Recovery Agent – new**
   - Create a scheduled Azure Function or Logic App that periodically scans Service Bus **dead‑letter queues** and Dataverse for workflow instances stuck in an `in‑progress` state.
   - For each dead‑lettered message or stalled workflow, decide whether to **replay** the message to the main topic, mark the workflow as failed, or alert an operator.  Publish a `workflow.failed` event when appropriate.
   - Send alerts via Logic App/Power Automate (e.g. Teams messages) for manual intervention when thresholds are exceeded.

After provisioning agents in Dev, test end‑to‑end workflow execution.  Ensure that events flow correctly through Service Bus, that API Management routes calls, and that Dataverse state is updated.  Use correlation IDs to trace a workflow instance across agents and logs.

## Phase 4 – CI/CD & Release Management (Version 2)

Follow the same principles as the original guide, with these additions:

- Include **Service Bus**, **API Management** and **Key Vault/App Configuration** in your infrastructure‑as‑code templates (Bicep/Terraform).  Use environment variables or pipeline variables for topic names and API paths.
- Define pipeline stages to deploy and update APIs in API Management, update Key Vault secrets, and apply App Configuration changes.  Use approvals for changes impacting production.
- Use automated tests to validate that events are published and consumed correctly and that the LLM guardrail layer blocks inappropriate prompts/responses.

## Phase 5 – Testing, Monitoring & Runbooks (Version 2)

- **Testing** – expand integration tests to verify Service Bus event flows, API Management routing and secret retrieval from Key Vault.  Simulate failures to ensure the Health & Recovery Agent triggers correctly.
- **Monitoring** – build dashboards that combine metrics from Service Bus (active messages, dead‑letter count), API Management (request counts, failures), Functions and Logic Apps.  Use the correlation ID to trace workflows across services.
- **Runbooks** – document procedures to replay messages from dead‑letter queues, rotate secrets in Key Vault, update App Configuration values and roll back API changes.  Include steps to enable/disable agents via feature flags.

## Phase 6 – Future Extension Pattern

The extension pattern remains the same as the original guide.  When adding a new agent, ensure you:

1. Define a new event contract (topic and message schema) if the agent needs to publish or consume events.
2. Update the master inventory and create a deep‑dive spec for the agent.
3. Add provisioning steps for any additional resources (Functions, pipelines, models).
4. Update the orchestrator to subscribe/publish to the new events and call the agent via API Management.
5. Include the new agent and its configuration in the CI/CD pipelines, secrets and configuration stores.

By following this Version 2 installation guide, your organisation will deploy the multi‑agent system using a robust event‑driven architecture with enterprise‑grade security, observability and resiliency.
