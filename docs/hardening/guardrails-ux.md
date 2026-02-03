# Guardrails UX

Guardrails protect the system and its operators from unintended consequences.  When a guardrail triggers, the user experience should be clear and actionable.

## User messaging

- Present a concise explanation of why the action was blocked.
- Suggest how the user can resolve the issue, such as requesting additional scopes or adjusting parameters.
- Provide a reference to documentation or runbook.

## Logging

- Include guardrail identifiers and trigger conditions in system logs.
- Correlate guardrail events with runbook and step identifiers to aid troubleshooting.

## Operator workflow

Operators should be able to acknowledge guardrail events, override when appropriate, and document their decision.  The system should track these overrides for audit purposes.
