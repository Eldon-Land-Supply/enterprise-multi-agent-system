# Installation & Configuration Framework

This installation framework provides a step‑by‑step guide for setting up the multi‑agent system described in this repository.  It is organized into phases to ensure a logical and repeatable process.  While the instructions are comprehensive, tailor the specifics (such as names and regions) to your organisation’s standards and policies.

## Phase 0 – Prerequisites & Global Setup

1. **Establish the Microsoft ecosystem**:
   - Ensure access to an Azure subscription with sufficient quotas for OpenAI, Machine Learning, Logic Apps and storage services.
   - Provision a Power Platform environment (Dataverse) and a Microsoft 365 tenant with Teams and Outlook enabled.
2. **Create global resources**:
   - Set up a central **Log Analytics workspace** and enable **Azure Monitor** for diagnostics across all services.
   - Deploy a **Purview** account for data classification and lineage.
   - Create a **OneLake / Fabric workspace** if using Microsoft Fabric for data pipelines.
3. **Naming & tagging conventions**:
   - Define prefixes/suffixes for resource names (e.g. `prod-ai-openai`, `dev-ml-workspace`).
   - Apply tags such as `environment`, `owner`, `costCenter` to all resources.
4. **Version control**:
   - Create or select a Git repository (e.g. this repository) to store agent code, pipeline definitions, Logic App JSON and Power Automate solutions.
   - Define a branching strategy (e.g. `main` for prod, `dev` and `feature/*` branches).

## Phase 1 – Core Platform Resources

For each environment (Dev, Test, Prod):

1. **Resource groups** – create resource groups to contain all Azure resources for that environment.
2. **Dataverse** – provision a Dataverse environment if not already available.  Create base tables for `Summaries`, `Predictions`, `WorkflowRuns`, `Approvals` and any other shared entities.
3. **Azure OpenAI** – create an Azure OpenAI resource.  Deploy the desired model (e.g. GPT‑4) and note the deployment name and endpoint URL.
4. **Azure ML Workspace** – deploy an ML workspace.  Create compute clusters for training and inference.  Register data stores pointing to OneLake and Dataverse.
5. **Logic Apps / Power Automate** – set up Logic App and Power Automate environments.  Create custom connections for Dataverse, Outlook, Teams and any external services.  If using Logic App Standard, configure VNET and private endpoints if required.
6. **Azure Functions** – create a Function App with consumption plan (or App Service Plan if needed).  Set up deployment slots for Dev/Test/Prod.  Configure storage accounts for function code and file output.
7. **Microsoft Fabric / Data Factory** – if using Fabric, create a Data Pipeline in the Fabric workspace.  Otherwise, create an Azure Data Factory instance.  Configure integration runtimes and linked services for each data source.
8. **Application Insights** – enable Application Insights for Functions and ML endpoints to collect telemetry.

## Phase 2 – Identity, Security & Compliance

1. **Managed identities** – create or assign a managed identity for each agent (A01‑A07).  For bots (A06), register an app in Entra ID and configure OAuth scopes for Teams/Outlook.
2. **RBAC** – assign roles to each identity:
   - Grant Dataverse security roles (e.g. environment maker, table access) for read/write where needed.
   - Assign `Contributor` on specific resource groups for the orchestrator identity.
   - For the ML agent, assign `ML User` and `Storage Blob Data Contributor` roles.
   - For the data pipeline, assign data reader roles on sources and writer roles on OneLake.
3. **Purview policies** – define data classifications and label sensitive fields in Dataverse and OneLake.  Configure policies to mask or block sensitive data usage.
4. **Azure Policy & AI guardrails** – apply policies restricting resource deployment to approved regions, enforcing private endpoints and controlling AI model usage.  Configure the AI guardrail policy to restrict tool invocations and inspect prompts for sensitive content.
5. **Logging & monitoring** – configure diagnostic settings for each resource to send logs to Log Analytics.  Set retention periods (e.g. 90 days for application logs, 365 days for audit logs).  Create alert rules for failures and anomalies.

## Phase 3 – Agent Provisioning & Wiring

Provision and configure each agent in dependency order:

1. **A03 – Predictive Insights Provider**
   - Develop ML pipelines using Azure ML (e.g. Python SDK).  Store code in the repository under `ml/`.
   - Use Azure DevOps pipelines to build and register models.  Configure scheduled triggers for batch scoring and retraining.
   - Deploy a REST inference endpoint.  Record the endpoint URL and authentication details for A02.
2. **A07 – Data Pipeline Agent**
   - Define Fabric / Data Factory pipelines in JSON or YAML and store under `data-pipelines/`.
   - Deploy pipelines and schedule nightly runs.  Expose pipeline execution via REST for ad‑hoc triggers.
3. **A01 – Cognitive Analysis & Generation**
   - Create a Power Automate custom connector or an Azure Logic App that wraps calls to the Azure OpenAI endpoint.
   - Store prompts and system messages in the repository (e.g. `prompts/` folder).  Implement functions to call Cognitive Search if retrieval is required.
4. **A04 – Utility Function Agent**
   - Write Azure Function code (e.g. `functions/` folder) for each utility task.  Include a host.json and function.json per function.  Use Python or C# as desired.
   - Deploy via Azure DevOps release pipeline.  Configure app settings (e.g. storage connection strings) via environment variables.
5. **A02 – Workflow Orchestrator**
   - Design Logic App or Power Automate flows in the portal or via JSON definition stored in `flows/`.
   - Import the solution into the environment.  Configure connection references to Dataverse, A01, A03, A04 and A07 endpoints.
   - Set up triggers: scheduled recurrence, Dataverse changes, HTTP requests.  Define scopes and error handling.
6. **A05 – Long‑Running Workflow Agent**
   - Build stateful Logic Apps in the portal or via ARM template stored in `longflows/`.
   - Create approval processes and adaptive cards.  Connect to Teams.  Persist state in Dataverse tables.
7. **A06 – Conversational Bot Agent**
   - Develop a bot using the Bot Framework SDK and register it in Azure Bot Service (stored in `bot/`).  Implement authentication and message routing.
   - Configure channels (Teams and Outlook) and set up adaptive card templates.  Store conversation state in Dataverse.
   - Link the bot’s webhook to A02’s HTTP endpoint.

Each of the above steps should be implemented first in the Dev environment.  Once tested, export solutions/artifacts and promote to Test and Prod via CI/CD pipelines.

## Phase 4 – CI/CD & Release Management

1. **Repository structure** – organise code into folders (`functions/`, `ml/`, `flows/`, `data-pipelines/`, `bot/`, `prompts/`).  Include pipeline definitions (`azure-pipelines.yml`) for each.
2. **Build & test** – configure Azure DevOps or GitHub Actions to run unit tests for functions and ML code.  Validate Logic App JSON against schemas.  Run static code analysis for security.
3. **Deploy** – create multi‑stage pipelines that deploy to Dev, Test and Prod.  Use approval gates before promoting to Prod.  For Power Automate, export solutions and import via pipeline tasks.  For ML, use MLOps tasks to register and deploy models.
4. **Versioning** – tag releases in Git.  Maintain changelogs.  Use feature flags or configuration toggles to enable new agents gradually.

## Phase 5 – Testing, Monitoring & Runbooks

1. **Testing**
   - Create sample datasets and requests for unit testing of each agent.
   - Set up integration tests that simulate the full workflow: start a run in Dev, verify that each agent executes and that outputs appear in Dataverse.
   - Conduct User Acceptance Testing (UAT) with business stakeholders.
2. **Monitoring**
   - Build dashboards in Azure Monitor or Power BI showing run counts, success/failure rates, latency and throughput per agent.
   - Configure alerts for critical failures (e.g. orchestrator errors, ML model drift) and performance issues.
   - Regularly review logs for anomalies and tune performance (e.g. adjust schedules, scale settings).
3. **Runbooks**
   - Document procedures to restart agents, re‑run failed instances, roll back deployments and update prompts/models.
   - Define escalation paths for unresolved errors.  Include contacts for each component owner.

## Phase 6 – Future Extension Pattern

1. **Adding a new agent**
   - Identify the need (e.g. new automation requirement).  Create a new entry in the master inventory table with a unique ID.
   - Draft a deep‑dive spec using the same template as the existing agents.  Include responsibilities, tasks, integrations, triggers, error handling and security.
   - Develop the agent’s code or workflow in the `feature/<agent-id>` branch.
   - Update the orchestrator (A02) to invoke the new agent at the appropriate step.
   - Deploy and test in Dev, then promote via CI/CD.
2. **Feature flags and dark launches**
   - Use configuration settings or environment variables to enable/disable new agents in production.  This allows gradual rollout and quick rollback if issues arise.
3. **Documentation and training**
   - Update this repository’s documentation whenever agents are added or modified.  Provide training to operators and stakeholders on new capabilities and any required changes to processes.

Following this framework ensures a consistent, secure and scalable rollout of the multi‑agent system across your organisation.
