"""Queue publisher adapters used by HTTP ingress and workers."""

from __future__ import annotations

import hashlib
import os
from typing import Protocol

from .events import EventEnvelope
from .payloads import EventPayloadStore


class EventPublisher(Protocol):
    def publish(self, event: EventEnvelope) -> None: ...


def service_bus_message_id(event: EventEnvelope) -> str:
    """Return a queue-wide stable ID scoped by provider and bounded to 64 bytes."""

    return hashlib.sha256(f"{event.source}\0{event.id}".encode("utf-8")).hexdigest()


class MemoryEventPublisher:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    def publish(self, event: EventEnvelope) -> None:
        self.events.append(event)


class AzureServiceBusPublisher:
    """Publish small claim-check envelopes using a connection or managed identity."""

    def __init__(
        self,
        queue_name: str,
        *,
        connection_string: str | None = None,
        fully_qualified_namespace: str | None = None,
        payloads: EventPayloadStore | None = None,
    ) -> None:
        try:
            from azure.servicebus import ServiceBusClient
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("azure-servicebus is required") from exc

        if connection_string:
            self._client = ServiceBusClient.from_connection_string(connection_string)
        elif fully_qualified_namespace:
            try:
                from azure.identity import DefaultAzureCredential
            except ImportError as exc:  # pragma: no cover - deployment dependency
                raise RuntimeError("azure-identity is required") from exc
            self._client = ServiceBusClient(
                fully_qualified_namespace,
                credential=DefaultAzureCredential(),
            )
        else:
            raise RuntimeError(
                "SERVICE_BUS_CONNECTION or SERVICE_BUS_FULLY_QUALIFIED_NAMESPACE is required"
            )
        self._queue_name = queue_name
        self._payloads = payloads

    def publish(self, event: EventEnvelope) -> None:
        from azure.servicebus import ServiceBusMessage

        dispatch_event = self._payloads.prepare(event) if self._payloads else event
        body = dispatch_event.to_json()
        max_bytes = int(os.getenv("MAX_QUEUE_MESSAGE_BYTES", "192000"))
        if len(body.encode("utf-8")) > max_bytes:
            raise RuntimeError("Queue envelope exceeds MAX_QUEUE_MESSAGE_BYTES")
        message = ServiceBusMessage(
            body,
            message_id=service_bus_message_id(dispatch_event),
            correlation_id=dispatch_event.correlation_id,
            subject=dispatch_event.type,
            content_type="application/json",
        )
        with self._client.get_queue_sender(self._queue_name) as sender:
            sender.send_messages(message)
