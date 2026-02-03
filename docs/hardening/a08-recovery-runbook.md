# A08 Recovery Runbook

This document describes the health and recovery procedures for the A08 agent responsible for orchestrating stuck or failed workflows.

## Overview

The recovery agent monitors in‑progress workflows and attempts to heal them automatically.  It performs the following phases:

1. **Dead‑letter sweep:** Scan for messages and tasks that have failed and moved to a dead‑letter queue.  Attempt a controlled replay if the cause is transient.
2. **Stuck workflow detection:** Identify workflows that have not made progress for a configurable period.  Inspect the current step and decide whether to retry or alert an operator.
3. **Replay attempts:** Retry steps that may succeed upon re‑execution.  Ensure that the operations are idempotent or compensate for duplicates.
4. **Operator notification:** When automatic recovery fails, notify an operator with details and suggested actions.

## Safety guidelines

- Limit the number of automatic retries to avoid runaway loops.
- Include correlation identifiers in alerts so operators can trace the issue quickly.
- Document any manual actions taken during recovery for future audits.
