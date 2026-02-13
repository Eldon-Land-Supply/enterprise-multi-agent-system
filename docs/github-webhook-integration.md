# GitHub Webhook Integration and Polling Migration

This document captures the plan for moving GitHub event ingestion from a polling scheduler to a webhook-first model. It also documents validation, routing, and retry expectations so the implementation can be aligned with the existing event processors.

## Current State

The repository does not currently contain a polling scheduler, cron job, or GitHub client code responsible for periodic fetching of events. Any future implementation should avoid duplicating webhook deliveries once webhooks are enabled.

## Webhook Receiver Endpoint

* **Endpoint:** `POST /webhooks/github`
* **Security:** Validate the `X-Hub-Signature-256` header using HMAC-SHA256 with the shared GitHub secret. Reject payloads missing the header or those that fail verification.
* **Response:** Return `200 OK` when the payload is accepted for processing, `401 Unauthorized` for failed signatures, and `400 Bad Request` when required headers are absent.
* **Idempotency:** Use the `X-GitHub-Delivery` identifier to prevent duplicate processing when GitHub retries deliveries.

### Signature Verification Flow

1. Extract the raw request body and the `X-Hub-Signature-256` header.
2. Compute `sha256` HMAC with the configured secret and format as `sha256=<digest>`.
3. Perform a constant-time comparison between the computed digest and the header value.
4. If verification succeeds, enqueue the payload for routing; otherwise respond with `401`.

## Event Routing Pipeline

1. Parse the `X-GitHub-Event` header to determine the event type.
2. Wrap the payload with metadata (delivery ID, repository, installation ID when present).
3. Dispatch to existing processors via the internal event bus or message queue. For example:
   * `push` → source control sync processor
   * `pull_request` → review and automation processor
   * `issues`/`issue_comment` → triage and conversation agents
   * `check_run`/`check_suite` → CI feedback processor
4. If a processor is unavailable or rejects the message, mark the delivery for retry with exponential backoff.

## Migration Plan from Polling to Webhooks

1. **Configuration flag:** Introduce a per-repository boolean `enable_webhook_ingest` (default `false`).
2. **Dual-mode period:** While the flag is `false`, continue polling; when set to `true`, accept webhooks but leave polling enabled for verification.
3. **Verification:** After confirming webhook deliveries for a repository, disable polling by toggling `disable_polling_after_webhook_verified` or removing the repository from the polling schedule.
4. **Monitoring:** Track webhook delivery metrics (latency, failures, retries) and compare against polling results during the dual-mode period.
5. **Rollback:** Provide a flag to re-enable polling if webhook failures exceed thresholds.

## Testing Strategy

### Signature Validation

* Accept valid HMAC signatures and reject malformed or mismatched signatures.
* Reject requests without the `X-Hub-Signature-256` header.
* Ensure comparison is constant-time to avoid timing attacks.

### Event Routing

* Route payloads based on `X-GitHub-Event` header to the correct processor.
* Confirm unhandled events are safely ignored or logged without crashing the pipeline.

### Retry Behavior

* Simulate processor failures and verify deliveries are retried with exponential backoff and capped attempts.
* Ensure idempotency by using `X-GitHub-Delivery` to avoid double-processing during retries.

## Operational Considerations

* Store GitHub webhook secrets securely (e.g., Azure Key Vault or environment secret manager).
* Audit logs should record receipt, verification outcome, and routing decisions without persisting raw payloads longer than necessary.
* Document webhook setup steps per repository, including secret rotation procedures.
