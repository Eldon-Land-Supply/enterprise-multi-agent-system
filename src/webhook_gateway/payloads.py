"""Private blob claim-check storage for queue payloads that must stay small."""

from __future__ import annotations

import hashlib
import re
from typing import Mapping, Protocol

from .events import EventEnvelope, event_payload_hash


POINTER_KEY = "_event_blob"


class EventPayloadStore(Protocol):
    def prepare(self, event: EventEnvelope) -> EventEnvelope: ...

    def resolve(self, event: EventEnvelope) -> EventEnvelope: ...


class AzureBlobEventPayloadStore:
    """Persist full envelopes privately and place only an integrity-checked pointer on queues."""

    def __init__(
        self,
        connection_string: str | None,
        container_name: str,
        *,
        account_url: str | None = None,
    ) -> None:
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("azure-storage-blob is required") from exc
        if not connection_string and not account_url:
            raise RuntimeError(
                "PAYLOAD_STORAGE_CONNECTION or PAYLOAD_STORAGE_ACCOUNT_URL is required"
            )
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?", container_name):
            raise ValueError("Invalid Azure Blob container name")
        self._container_name = container_name
        if connection_string:
            service = BlobServiceClient.from_connection_string(connection_string)
        else:
            try:
                from azure.identity import DefaultAzureCredential
            except ImportError as exc:  # pragma: no cover - deployment dependency
                raise RuntimeError("azure-identity is required") from exc
            service = BlobServiceClient(
                account_url, credential=DefaultAzureCredential()
            )
        self._container = service.get_container_client(container_name)

    @staticmethod
    def _is_pointer(event: EventEnvelope) -> bool:
        return bool(event.metadata.get("payload_offloaded")) and isinstance(
            event.data.get(POINTER_KEY), Mapping
        )

    def prepare(self, event: EventEnvelope) -> EventEnvelope:
        if self._is_pointer(event):
            return event
        try:
            from azure.core.exceptions import ResourceExistsError
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("azure-core is required") from exc

        payload = event.to_json().encode("utf-8")
        content_hash = hashlib.sha256(payload).hexdigest()
        payload_hash = event_payload_hash(event)
        event_key = hashlib.sha256(
            f"{event.source}\0{event.id}".encode("utf-8")
        ).hexdigest()
        safe_source = re.sub(r"[^a-z0-9-]", "-", event.source.lower()).strip("-")
        blob_name = f"events/{safe_source or 'unknown'}/{event_key}/{content_hash}.json"
        blob = self._container.get_blob_client(blob_name)
        try:
            blob.upload_blob(
                payload,
                overwrite=False,
                metadata={"sha256": content_hash, "payloadhash": payload_hash},
            )
        except ResourceExistsError:
            metadata = blob.get_blob_properties().metadata or {}
            if (
                metadata.get("sha256") != content_hash
                or metadata.get("payloadhash") != payload_hash
            ):
                raise RuntimeError("Existing event blob has invalid integrity metadata")

        return EventEnvelope(
            id=event.id,
            source=event.source,
            type=event.type,
            data={
                POINTER_KEY: {
                    "container": self._container_name,
                    "name": blob_name,
                    "sha256": content_hash,
                }
            },
            correlation_id=event.correlation_id,
            received_at=event.received_at,
            subject=event.subject,
            metadata={**event.metadata, "payload_offloaded": True},
            version=event.version,
        )

    def resolve(self, event: EventEnvelope) -> EventEnvelope:
        if not self._is_pointer(event):
            return event
        reference = event.data[POINTER_KEY]
        container = str(reference.get("container") or "")
        blob_name = str(reference.get("name") or "")
        expected_hash = str(reference.get("sha256") or "")
        if container != self._container_name or not blob_name.startswith("events/"):
            raise RuntimeError("Invalid event payload reference")
        if not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
            raise RuntimeError("Invalid event payload hash")
        payload = self._container.download_blob(blob_name).readall()
        if hashlib.sha256(payload).hexdigest() != expected_hash:
            raise RuntimeError("Event payload integrity check failed")
        resolved = EventEnvelope.from_json(payload)
        if resolved.id != event.id or resolved.source != event.source:
            raise RuntimeError("Event payload identity mismatch")
        return resolved
