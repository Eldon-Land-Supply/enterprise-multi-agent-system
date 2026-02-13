# Architecture v2 – Improved Multi‑Agent System Design

This document outlines the improved architecture for the enterprise multi‑agent system. Version 2 introduces a dedicated messaging layer, clear separation of orchestration responsibilities, and additional platform services to increase reliability, scalability, and governance.

## Overview

Version 2 builds on the original plan by incorporating lessons from production deployments. In this design, agents communicate through an event‑driven message bus rather than relying on Dataverse triggers. Orchestration duties are explicitly split between enterprise workflows and human‑centric flows, and platform services such as API Management, Key Vault, and App Configuration are used to manage security, configuration, and external access.

## Key Improvements

1. **Event‑Driven Messaging via Azure Service Bus**  
   - All agents publish and consume events on Azure Service Bus topics, decoupling producers from consumers and providing durable message storage, retries, dead‑lettering, and ordered delivery.
   - Topics such as `workflow.started`, `data.prep.completed`, `ml.batch.completed`, `llm.summary.completed` and `workflow.failed` are defined to coordinate agent tasks.  
   - Dataverse remains the system of record for state and audit logs but no longer functions as the event bus.

2. **Control‑Plane Orchestration with Logic Apps Standard**  
   - Core orchestration logic runs in Logic Apps Standard, which supports version control, private networking, application insights, and enterprise CI/CD.  
   - Power Automate flows are reserved for human approval processes, notifications, and lightweight user‑centric tasks.  
   - This boundary simplifies operations: Logic Apps handle backend integration and cross‑service workflows, while Power Automate handles user workflows and approvals.

3. **Unified Front Door with Azure API Management**  
   - All publicly exposed agent endpoints (Azure Functions, ML endpoints, and any custom APIs) are published through Azure API Management.  
   - API Management enforces authentication and authorization via Entra ID, provides rate limiting and quotas, and offers a single point for monitoring and versioning agent APIs.  
   - Logic Apps running inside a Virtual Network can remain private; API Management exposes them securely to other services or clients.

4. **Centralized Configuration and Secrets**  
   - Secrets (connection strings, API keys, credentials) are stored in Azure Key Vault and accessed via managed identities.  
   - Non‑secret configuration (feature flags, endpoint URLs, correlation settings) is stored in Azure App Configuration.  
   - Agents read configuration at runtime through these services, avoiding hard‑coded values and simplifying environment promotion.

5. **Uniform Workflow Instance Schema & Correlation IDs**  
   - A standard schema defines how every workflow instance is tracked across agents. Fields include: `workflow_instance_id`, `step_id`, `agent_id`, `correlation_id`, `idempotency_key`, `status`, timestamps, and references to artifacts (e.g., blob URIs).  
   - Each event message and Dataverse record includes these identifiers, enabling end‑to‑end traceability and safe retries.  
   - Correlation IDs are propagated through Service Bus messages and logs, and they surface in Azure Monitor dashboards.

6. **LLM Guardrail Layer**  
   - Before invoking the Azure OpenAI agent, a dedicated policy middleware checks prompts and context for sensitive data, applies Purview labels, and enforces data masking and prompt injection protection.  
   - After receiving responses, the middleware applies output filtering and redaction policies before persisting results or exposing them to users.  
   - This guardrail is implemented as an Azure Function or Logic App step and is required for all LLM calls.

7. **Health & Recovery Agent**  
   - A new scheduled agent periodically scans workflow instances and Service Bus dead‑letter queues to detect stuck or failed processes.  
   - It replays messages, updates statuses, and triggers alerts through Logic Apps or Power Automate when thresholds are exceeded.  
   - This agent ensures long‑running workflows remain healthy without manual intervention.

## Responsibilities and Interactions

- Agents publish messages to Service Bus topics when they complete a task. Subscribed agents pick up new work based on the event type.  
- Logic Apps orchestrate multi‑step processes by sequencing Service Bus events, calling functions and ML endpoints through API Management, and updating Dataverse.  
- Data transformation and custom logic run in Azure Functions, which are exposed via API Management and triggered from Logic Apps or Service Bus.  
- Power Automate flows handle human approvals and notifications, reading and writing to Dataverse and Teams.

## Next Steps

- Update agent specifications to reflect the new messaging and orchestration patterns.  
- Expand the installation guide to include provisioning of Service Bus, API Management, Key Vault, and App Configuration.  
- Define the workflow instance schema as a shared contract across agents.  
- Implement the Health & Recovery Agent specification and corresponding deployment instructions.
