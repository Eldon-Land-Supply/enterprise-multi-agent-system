# Scope Model

The scope model defines the permissions available to each agent or workflow in the enterprise multi‑agent system.  Each scope represents a collection of capabilities that can be granted to a runbook step or to an agent.

## Principles

- **Least privilege:** grant only the permissions necessary to perform the task.
- **Separation of duties:** avoid combining high‑impact operations in a single scope.
- **Auditable grants:** record when scopes are granted and revoked.

## Implementation

Scopes are defined in the runbook schema.  Each step declares the scopes it requires.  The orchestrator checks that the scopes are available before executing the step.  When scopes change, an audit log entry should capture the reason and the requesting operator.
