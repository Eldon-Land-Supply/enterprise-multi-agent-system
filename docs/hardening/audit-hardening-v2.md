# Audit Hardening v2

This document outlines the second iteration of audit hardening across the multi‑agent system.  It describes the scope model, failure categories, guardrails user experience (UX), schema versioning strategy, A08 recovery procedures, and CI/CD rollback considerations.

## Scope model

Define least‑privilege scopes for each agent or workflow.  Scopes should follow the principle of least privilege: only the minimum permissions required to perform a task.  Document how scopes are granted and revoked.

## Failure taxonomy

Categorize failures into classes (transient, persistent, external dependency, etc.) and describe appropriate retry and escalation strategies for each class.  Include examples.

## Guardrails UX

Explain how guardrail failures should be surfaced to users and operators, including messages, logs, and actionable instructions.  Provide a user‑friendly explanation for each guardrail scenario.

## Schema versioning

All runbook files must include a `schemaVersion` field.  Document the versioning policy and how backwards compatibility is maintained when the schema evolves.

## A08 health & recovery agent

Describe recovery phases for the A08 agent, including dead‑letter sweep, stuck workflow detection, replay attempts, and operator notifications.  Provide thresholds and alerting guidance.

## CI/CD rollback

Outline rollback mechanisms in CI/CD pipelines.  Include sample templates for Azure DevOps and GitHub Actions to revert deployments safely.
