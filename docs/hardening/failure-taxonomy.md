# Failure Taxonomy

This document categorizes failures and describes the appropriate handling strategy for each.

## Transient failures

Transient failures are temporary errors caused by network issues, rate limits, or other recoverable conditions.  Recommended action: retry with exponential backoff.

## Persistent failures

Persistent failures occur when the operation cannot succeed without external intervention (e.g., invalid parameters, missing resources).  Recommended action: log the error and alert the operator; do not retry automatically.

## External dependency failures

Failures triggered by dependencies outside of the system's control (e.g., cloud service outage).  Recommended action: enter a backoff period, monitor the external service, and retry when the service is healthy.  Provide clear status to the operator.

## Idempotency

Ensure that retries are safe: operations should be idempotent whenever possible.  If not, implement compensation logic to handle duplicate execution.
