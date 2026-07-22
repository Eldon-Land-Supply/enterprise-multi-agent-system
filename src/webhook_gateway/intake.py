"""Provider-specific webhook validation and canonical event creation."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from .errors import WebhookError
from .events import EventEnvelope
from .github_admission import GitHubAdmissionPolicy
from .idempotency import IdempotencyStore
from .queueing import EventPublisher
from .quota import DailyQuota
from .security import (
    is_configured_secret,
    verify_client_state,
    verify_github_signature,
    verify_timestamped_signature,
)


@dataclass(frozen=True)
class IntakeResult:
    accepted: int
    duplicates: int = 0
    ignored: int = 0
    rate_limited: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": True,
            "enqueued": self.accepted,
            "duplicates": self.duplicates,
            "ignored": self.ignored,
            "rate_limited": self.rate_limited,
        }


def _headers(value: Mapping[str, str]) -> dict[str, str]:
    return {key.lower(): item for key, item in value.items()}


def _json_object(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebhookError(
            400, "invalid_json", "Request body must be valid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise WebhookError(400, "invalid_payload", "Request body must be a JSON object")
    return value


class WebhookIntake:
    def __init__(
        self,
        store: IdempotencyStore | None,
        publisher: EventPublisher,
        *,
        github_policy: GitHubAdmissionPolicy | None = None,
        github_quota: DailyQuota | None = None,
        github_daily_limit: int = 100,
        direct_sources: frozenset[str] = frozenset(),
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._github_policy = github_policy
        self._github_quota = github_quota
        self._github_daily_limit = github_daily_limit
        self._direct_sources = direct_sources

    def _publish_once(self, source: str, event: EventEnvelope) -> bool:
        if source != event.source:
            raise ValueError("Idempotency source must match the event source")
        if source in self._direct_sources:
            # OneDrive notifications are dirty signals: a broker-level duplicate
            # only causes another idempotent delta read. One direct Service Bus
            # send keeps Graph acknowledgement inside its three-second window.
            self._publisher.publish(event)
            return True
        if self._store is None:
            raise RuntimeError("Durable intake store is not configured")
        staged = self._store.stage(event)
        if staged.state == "sent":
            return False
        # A failed or interrupted send leaves a durable pending record. Provider
        # retries and the timer dispatcher both recover it with the same queue ID.
        self._publisher.publish(staged.event)
        self._store.mark_sent(staged.event)
        return True

    def github(
        self,
        body: bytes,
        headers: Mapping[str, str],
        secret: str,
        *,
        previous_secret: str = "",
    ) -> IntakeResult:
        normalized = _headers(headers)
        delivery_id = normalized.get("x-github-delivery")
        event_type = normalized.get("x-github-event")
        if not delivery_id or not event_type:
            raise WebhookError(
                400,
                "missing_headers",
                "Missing X-GitHub-Delivery or X-GitHub-Event",
            )
        signature = normalized.get("x-hub-signature-256", "")
        try:
            verify_github_signature(body, signature, secret)
        except WebhookError:
            if not is_configured_secret(previous_secret):
                raise
            verify_github_signature(body, signature, previous_secret)
        payload = _json_object(body)
        repository = payload.get("repository")
        subject = (
            repository.get("full_name") if isinstance(repository, Mapping) else None
        )
        repository_id = (
            str(repository.get("id") or "") if isinstance(repository, Mapping) else ""
        )
        event = EventEnvelope(
            id=delivery_id,
            source="github",
            type=event_type,
            data=payload,
            correlation_id=normalized.get("x-correlation-id") or delivery_id,
            subject=str(subject) if subject else None,
            metadata={
                "installation_id": (payload.get("installation") or {}).get("id")
                if isinstance(payload.get("installation"), Mapping)
                else None,
                "repository_id": repository_id or None,
            },
        )

        if self._store is None:
            raise RuntimeError("GitHub intake store is not configured")
        existing = self._store.get("github", delivery_id)
        if existing is not None:
            # A sent record is a duplicate; a pending record is a recovered
            # ambiguous/failed send and must be dispatched without new quota use.
            if self._publish_once("github", event):
                return IntakeResult(accepted=1)
            return IntakeResult(accepted=0, duplicates=1)

        if self._github_policy is not None:
            decision = self._github_policy.evaluate(event_type, payload)
            if not decision.allowed:
                return IntakeResult(accepted=0, ignored=1)
            repository_id = decision.repository_id
        if self._github_quota is not None:
            quota_scope = repository_id or str(subject or "")
            if not self._github_quota.allow(
                quota_scope, delivery_id, self._github_daily_limit
            ):
                return IntakeResult(accepted=0, ignored=1, rate_limited=1)

        if not self._publish_once("github", event):
            return IntakeResult(accepted=0, duplicates=1)
        return IntakeResult(accepted=1)

    def generic(
        self,
        body: bytes,
        headers: Mapping[str, str],
        secret: str,
        *,
        source: str = "external",
    ) -> IntakeResult:
        normalized = _headers(headers)
        verify_timestamped_signature(
            body,
            normalized.get("x-webhook-signature", ""),
            normalized.get("x-webhook-timestamp", ""),
            secret,
        )
        payload = _json_object(body)
        event_id = str(payload.get("id") or "")
        event_type = str(payload.get("type") or "")
        if not event_id or not event_type:
            raise WebhookError(400, "invalid_event", "Event id and type are required")
        data = payload.get("data", {})
        if not isinstance(data, dict):
            raise WebhookError(400, "invalid_event", "Event data must be an object")
        event = EventEnvelope(
            id=event_id,
            source=source,
            type=event_type,
            data=data,
            correlation_id=str(payload.get("correlation_id") or event_id),
            subject=str(payload["subject"]) if payload.get("subject") else None,
        )
        if not self._publish_once(source, event):
            return IntakeResult(accepted=0, duplicates=1)
        return IntakeResult(accepted=1)

    def verified_openai(
        self,
        payload: Mapping[str, Any],
        *,
        delivery_id: str | None = None,
    ) -> IntakeResult:
        event_id = str(payload.get("id") or "")
        event_type = str(payload.get("type") or "")
        if not event_id or not event_type:
            raise WebhookError(
                400, "invalid_event", "OpenAI event id and type are required"
            )
        idempotency_key = delivery_id or event_id
        data = payload.get("data", {})
        if not isinstance(data, Mapping):
            raise WebhookError(
                400, "invalid_event", "OpenAI event data must be an object"
            )
        response_id = data.get("id")
        event = EventEnvelope(
            id=idempotency_key,
            source="openai",
            type=event_type,
            data=dict(data),
            correlation_id=str(response_id or event_id),
            subject=str(response_id) if response_id else None,
            metadata={"openai_event_id": event_id},
        )
        if not self._publish_once("openai", event):
            return IntakeResult(accepted=0, duplicates=1)
        return IntakeResult(accepted=1)

    def verified_anthropic(self, payload: Mapping[str, Any]) -> IntakeResult:
        event_id = str(payload.get("id") or "")
        data = payload.get("data", {})
        if not event_id or not isinstance(data, Mapping):
            raise WebhookError(
                400,
                "invalid_event",
                "Anthropic event id and data are required",
            )
        event_type = str(data.get("type") or "")
        object_id = str(data.get("id") or "")
        if not event_type or not object_id:
            raise WebhookError(
                400,
                "invalid_event",
                "Anthropic data type and id are required",
            )
        event = EventEnvelope(
            id=event_id,
            source="anthropic",
            type=event_type,
            data=dict(data),
            correlation_id=object_id,
            subject=object_id,
            metadata={
                "organization_id": data.get("organization_id"),
                "workspace_id": data.get("workspace_id"),
            },
        )
        if not self._publish_once("anthropic", event):
            return IntakeResult(accepted=0, duplicates=1)
        return IntakeResult(accepted=1)

    def onedrive(
        self,
        body: bytes,
        expected_client_state: str,
        *,
        expected_subscription_id: str | None = None,
        expected_resource: str | None = None,
        expected_tenant_id: str | None = None,
    ) -> IntakeResult:
        payload = _json_object(body)
        notifications = payload.get("value")
        if not isinstance(notifications, list):
            raise WebhookError(400, "invalid_event", "OneDrive value must be an array")

        # Validate the complete batch before performing any durable writes. Drive
        # notifications are dirty signals, so equivalent updates can be collapsed
        # into one delta job without losing file changes.
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for notification in notifications:
            if not isinstance(notification, dict):
                raise WebhookError(
                    400, "invalid_event", "Invalid OneDrive notification"
                )
            verify_client_state(notification.get("clientState"), expected_client_state)
            subscription_id = str(notification.get("subscriptionId") or "")
            resource = str(notification.get("resource") or "")
            tenant_id = str(notification.get("tenantId") or "")
            lifecycle_event = str(notification.get("lifecycleEvent") or "")
            if not subscription_id:
                raise WebhookError(
                    400, "invalid_event", "OneDrive subscriptionId is required"
                )
            if not lifecycle_event and not resource:
                raise WebhookError(
                    400, "invalid_event", "OneDrive resource is required"
                )
            if expected_subscription_id and subscription_id != expected_subscription_id:
                raise WebhookError(
                    401, "invalid_subscription", "Invalid OneDrive subscription"
                )
            if (
                expected_resource
                and not lifecycle_event
                and resource != expected_resource
            ):
                raise WebhookError(401, "invalid_resource", "Invalid OneDrive resource")
            if expected_tenant_id and tenant_id != expected_tenant_id:
                raise WebhookError(401, "invalid_tenant", "Invalid OneDrive tenant")

            kind = f"lifecycle:{lifecycle_event}" if lifecycle_event else "updated"
            key = (subscription_id, kind)
            if key not in grouped:
                grouped[key] = {
                    "subscriptionId": subscription_id,
                    "resource": resource or expected_resource,
                    "tenantId": tenant_id or expected_tenant_id,
                    "changeType": notification.get("changeType") or "updated",
                    "lifecycleEvent": lifecycle_event or None,
                    "notificationCount": 0,
                }
            grouped[key]["notificationCount"] += 1

        accepted = 0
        duplicates = 0
        for (subscription_id, kind), data in grouped.items():
            lifecycle_event = str(data.get("lifecycleEvent") or "")
            event_type = (
                f"drive.lifecycle.{lifecycle_event}"
                if lifecycle_event
                else "drive.updated"
            )
            event = EventEnvelope(
                id=f"{subscription_id}:{uuid.uuid4()}",
                source="onedrive",
                type=event_type,
                data=data,
                correlation_id=subscription_id,
                subject=str(data.get("resource") or expected_resource or "") or None,
                metadata={"tenant_id": data.get("tenantId"), "signal_kind": kind},
            )
            if not self._publish_once("onedrive", event):
                duplicates += 1
            else:
                accepted += 1
        return IntakeResult(accepted=accepted, duplicates=duplicates)

    @staticmethod
    def correlation_id() -> str:
        return str(uuid.uuid4())
