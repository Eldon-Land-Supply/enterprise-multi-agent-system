## Alerting Architecture

This document describes how alerts are generated, routed, and delivered across the multi‑agent system. It focuses on the notification connectors available in Power Automate/Logic Apps and the payload formats exchanged between the Workflow Orchestrator, bot, and downstream channels.

### Connector Capabilities and Limits

| Connector | Primary Use in Alerting | Strengths | Limits & Considerations |
|-----------|------------------------|-----------|-------------------------|
| **Teams** (post message, adaptive card) | Notify channels or chats about failures, approvals, or drift; surface action buttons | Rich cards, mentions, action submissions; tenant‑level governance controls | Channel posts may lag during throttling; per‑app rate limits apply; requires connection reference with appropriate scope |
| **Outlook** (send email, create event) | Escalate incidents to distribution lists; schedule follow‑up meetings | Reliable delivery, calendaring, support for attachments | Slower for real‑time chat; attachment size limits; shared mailbox permissions must be granted |
| **Dataverse** (create/update row) | Persist alert records, audit trail, and suppression rules | Structured storage, trigger flows from table changes | Row‑level security must be configured; API limits apply during bursts |
| **HTTP webhook** (custom endpoint) | Integrate with SIEM/ITSM (e.g., ServiceNow, PagerDuty) | Flexible routing to external tools; supports signed payloads | Must handle auth/ingress; Logic App IP allow‑listing often required |
| **Application Insights / Azure Monitor** | Emit metrics and logs for automated alert rules | Native alert rules, action groups, log search | Query latency; workspace retention and cost considerations |

**Mapping to use cases**

- **Workflow failures or retries exhausted:** Teams adaptive card to the owning squad + Dataverse incident row; optional webhook to PagerDuty.
- **Model drift or performance degradation:** Azure Monitor rule triggers HTTP webhook toward ML on‑call; Teams summary with drift chart link; Dataverse record for auditing.
- **User‑visible degradation (bot/offline):** Outlook email to service owner DL; Teams status banner via bot conversation update; Application Insights availability alert.
- **Approval or manual intervention needed:** Teams adaptive card with Approve/Reject actions routed to the requestor’s channel; Dataverse record to unblock orchestrator branch.

### Notification Payload Format

All alerts share a normalized envelope to simplify routing:

```json
{
  "alertId": "<guid>",
  "source": "A02-orchestrator | A03-ml | bot | monitor",
  "severity": "Sev0 | Sev1 | Sev2 | Sev3",
  "category": "reliability | security | data-quality | user-impact",
  "summary": "Human-readable title",
  "details": "Long‑form description or markdown",
  "correlationId": "<workflow-run or conversation id>",
  "occurredAt": "<UTC timestamp>",
  "actions": [
    {
      "type": "link | approve | acknowledge",
      "label": "Action label",
      "target": "URL or Dataverse record reference"
    }
  ],
  "routing": {
    "channels": ["teams", "email", "webhook", "dataverse"],
    "audience": "oncall | ops | data-science | requestor"
  },
  "metadata": {
    "env": "dev|test|prod",
    "tenant": "<tenant id>",
    "feature": "alerting.v1"
  }
}
```

- **Teams adaptive card**: Renders `summary`, `details`, `severity`, and `actions` as card sections; `correlationId` stored as a hidden field for callbacks.
- **Email**: Uses `summary` as subject, `details` and `actions` as body, `severity` mapped to priority.
- **Dataverse**: Stores envelope in an `Alerts` table with columns for severity, category, source, correlation ID, routing, and status.
- **Webhooks**: Full envelope posted with HMAC signature header and `feature` flag included for downstream routing.

### Routing Rules

1. **Severity**: `Sev0/1` go to Teams (24x7 channel) + webhook action group; `Sev2` to team channel; `Sev3` to Dataverse only unless opted‑in to email.
2. **Source**: `source == "bot"` routes to bot operations channel; `source == "A03-ml"` adds ML on‑call email; orchestrator failures also notify workflow owners from Dataverse lookup.
3. **Category**: `security` always posts to dedicated compliance channel and skips suppression; `data-quality` creates/updates a Dataverse quality issue record.
4. **Quiet hours**: email is suppressed 22:00–07:00 local; Teams posts remain, but `@mention` only for Sev0/1.
5. **Deduplication**: same `correlationId + category + severity` within 10 minutes updates existing Dataverse row instead of creating a new one; Teams card is edited instead of re‑posted.

### Prerequisites

- Connection references for Teams, Outlook, Dataverse, and any webhook auth (managed identity or API key) are provisioned in each environment.
- Dataverse `Alerts` table (or equivalent) exists with columns: `AlertId`, `Severity`, `Category`, `Summary`, `Details`, `CorrelationId`, `Routing`, `Status`, `FeatureFlag`, timestamps.
- Azure Monitor workspace with action groups reachable by the orchestrator/Logic App.
- Service principals or managed identities granted least‑privilege rights to post messages and modify Dataverse rows.

### Known Limitations

- Teams throttling can delay posts during incident storms; ensure fallback email for Sev0/1.
- Adaptive card refreshes are limited; too many edits may require new posts to avoid stale content.
- Webhook consumers must handle retries and idempotency (`alertId` + `correlationId`).
- Email is not real‑time and may not be visible to on‑call engineers; always pair critical alerts with chat/webhook.
- Dataverse retention and API limits can impact high‑volume alerting; archive closed alerts periodically.

### Feature Flags and Config Placeholders

- `ALERTING_ENABLED` (bool): master switch; if `false`, alerts write only to Dataverse audit without outbound notifications.
- `ALERTING_CHANNELS` (list): allowed channels per environment, e.g., `["teams","webhook"]` in dev; stored as environment variable/logic app parameter.
- `ALERT_DEDUP_WINDOW_MINUTES` (int): time window for deduplication checks.
- `ALERT_EMAIL_SUPPRESS_AFTER_HOURS` (bool) and `ALERT_QUIET_HOURS` (string): controls quiet‑hour routing.
- `ALERT_WEBHOOK_SIGNING_KEY` (secret): required for webhook delivery; absence disables webhook channel.
- `ALERT_MIN_SEVERITY` (enum): lowest severity that triggers outbound notifications (e.g., `Sev2+`).

Placeholders should be defined as environment variables in Power Automate/Logic Apps and surfaced as parameters in the orchestrator deployment templates so they can be toggled without code changes.
