# Enterprise Multi-Agent System for Microsoft Ecosystem

This repository contains the architecture, implementation, and operating guidance
for an event-driven multi-agent system built around Microsoft Azure and Microsoft
365. It includes a runnable Azure Functions webhook gateway that securely connects
GitHub, OpenAI background Responses, OneDrive for Business, and Claude processing.

## Webhook gateway

The gateway exposes:

- `POST /api/webhooks/github` for signed GitHub events.
- `POST /api/webhooks/openai` for signed OpenAI background-response events.
- `POST /api/webhooks/anthropic` for signed Claude Managed Agents events.
- `POST /api/webhooks/onedrive` for Microsoft Graph validation, change, and lifecycle notifications.
- `POST /api/webhooks/events` for timestamp-signed first-party events.
- `POST /api/webhooks/delivery` for timestamp-signed delivery callbacks.
- POST /api/admin/onedrive/bootstrap (Function-key protected) for managed-identity subscription setup.
- GET /api/health for configuration readiness.

Ingress verifies provider-specific authenticity. GitHub, OpenAI, Anthropic, and
generic events are durably staged in a recoverable Table/Blob inbox-outbox before
acknowledgement; validated OneDrive dirty signals go directly to Service Bus to
meet Microsoft Graph's three-second response budget and are safe to duplicate.
GitHub repository IDs, trusted actors, safe actions, and a durable daily model-call
quota guard the spend boundary; push is the only default GitHub event. OneDrive
dirty-signal batches collapse to one delta job. A queue worker uses bounded SDK
timeouts, provider-level checkpoints, and a separate first-wins result outbox, so
ambiguous sends do not repeat completed OpenAI/Claude work.
Claude Messages run in the worker; Claude Managed Agents session and deployment
lifecycle events arrive through Anthropic's signed webhook API.

Start with [the webhook setup and operations runbook](docs/webhook-setup.md).
The original GitHub-specific contract remains in
[GitHub webhook integration](docs/github-webhook-integration.md).

## Local validation

```powershell
python -m pip install --require-hashes --only-binary=:all: -r requirements-dev.txt
Copy-Item local.settings.example.json local.settings.json
python -m pytest tests/ -v --tb=short
func start
```

Dependency ranges live in `requirements.in` and `requirements-dev.in`; the two
`.txt` files are universal Python 3.11 hash locks. Review dependency changes, then
regenerate both locks with `uv pip compile --python-version 3.11 --universal
--generate-hashes` before committing them.

Never commit `local.settings.json`, `.env`, API keys, webhook secrets, Graph tokens,
or OneDrive download URLs.

## Architecture

- **Session Manager** maintains state, correlation IDs, policy checks, and routing.
- **API Client** centralizes authentication, retries, schema validation, and telemetry.
- **Azure Function ingress** verifies webhooks and writes a recoverable private inbox/outbox.
- **Service Bus** provides durable delivery, duplicate detection, retry, and dead-lettering.
- **Provider worker** performs guarded OpenAI or Claude analysis with per-provider checkpoints.
- **Result outbox** persists the first completed result and privately carries OneDrive cursor checkpoints.
- **OneDrive delta processing** turns change notifications into deterministic file changes.
- **A08 Health & Recovery** monitors failures and supports controlled replay.

Read [architecture.md](docs/architecture.md) for the complete runtime flows. The
master inventory is in [master_inventory.md](docs/master_inventory.md), and each
agent has a deep dive under `docs/agents/`.

## Deployment

The Azure resource template is
[`infra/bicep/webhook-gateway.bicep`](infra/bicep/webhook-gateway.bicep). It creates
a Linux Function App, Service Bus queues with duplicate detection and dead-lettering,
Storage, Application Insights, and Key Vault references. Deployment uses managed
identity for Storage, Service Bus, Key Vault, and Microsoft Graph. The GitHub OIDC
deployment principal receives narrowly scoped Function and package-Blob roles.

Use feature branches off `main`, run the full test suite, and open a pull request.
Secrets belong in Azure Key Vault or GitHub environment secrets, never source files.
