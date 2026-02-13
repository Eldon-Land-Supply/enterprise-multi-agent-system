# LLM Guardrails Middleware

This document describes the middleware layer responsible for enforcing safety and compliance guardrails on large language model (LLM) interactions (e.g. Azure OpenAI). The middleware sits between the orchestrator/agents and the LLM endpoint to ensure that prompts and responses comply with enterprise policies.

## Purpose

- **Input filtering** – Inspect prompts for sensitive data (PII, PCI, etc.) and block or mask disallowed content before sending to the LLM.
- **Prompt injection detection** – Identify and reject malicious instructions injected into prompts.
- **Output filtering and redaction** – Scan model outputs for sensitive or disallowed content and apply redaction or blocking as per policy.
- **Policy enforcement** – Apply organization-specific policies defined in Microsoft Purview and Azure Policy to every call.
- **Logging and traceability** – Record all prompt/response interactions with correlation IDs for audit and troubleshooting.

## Implementation

- Implement the middleware as a **stateful Logic App** or **Azure Function** that exposes an HTTP endpoint. Agents call this endpoint instead of calling Azure OpenAI directly.
- **Input processing**:
  - Use Microsoft Purview’s Data Loss Prevention (DLP) policies to classify sensitive fields in the prompt.
  - If sensitive data is detected, either mask it or reject the request based on policy.
  - Run a prompt injection detector (custom logic or an Azure AI Content Safety API) to identify malicious instructions and reject if found.
- **Calling the LLM**:
  - After input validation, call the Azure OpenAI endpoint via **Azure API Management** using the managed identity.
  - Include correlation and idempotency identifiers in headers for end-to-end trace.
- **Output processing**:
  - Inspect the model response for classified data or policy violations (using Purview and Content Safety tools).
  - Mask or redact any detected sensitive content.
  - Evaluate the response against guardrail policies (e.g., banned topics, maximum length) and block if necessary.
- **Logging**:
  - Log the prompt, masked prompt, response, classification results and policy outcomes to Azure Monitor / Application Insights with the `correlation_id`.
  - Emit a structured `policy_outcome` field (e.g. `allowed`, `redacted`, `blocked`) to the workflow instance log and Service Bus.

## Integration

- Agents (A01, A02, A03, A04) call the middleware via API Management as a normal HTTP action.
- The middleware uses managed identities to access Purview classification and to call Azure OpenAI.
- Policy definitions reside in Purview and Azure Policy; updates to policies take effect without redeploying the middleware.
- Use Service Bus to publish `workflow.failed` events if the guardrail blocks a request, so that the orchestrator can handle it.

## Benefits

- Centralizes all LLM safety checks in one component.
- Ensures consistent application of policies across all agents and workflows.
- Provides traceability and audit records for every LLM interaction.
- Reduces the risk of data leakage or unintended model behaviour.

This middleware is a critical part of the secure and compliant deployment of AI-powered agents.
