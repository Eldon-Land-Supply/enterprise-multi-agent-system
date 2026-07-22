"""Durable webhook inbox/outbox stores for idempotency and crash recovery."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from typing import Literal, Protocol

from .events import EventEnvelope, event_payload_hash

from .azure_tables import preprovisioned_table_client
from .payloads import EventPayloadStore


@dataclass(frozen=True)
class StagedEvent:
    event: EventEnvelope
    state: str
    created: bool


ExistingPayloadPolicy = Literal["reject", "first_wins"]


def _validate_existing_payload_policy(
    value: ExistingPayloadPolicy,
) -> ExistingPayloadPolicy:
    if value not in ("reject", "first_wins"):
        raise ValueError(
            "existing_payload_policy must be either 'reject' or 'first_wins'"
        )
    return value


class IdempotencyStore(Protocol):
    def get(self, source: str, event_id: str) -> StagedEvent | None: ...

    def stage(self, event: EventEnvelope) -> StagedEvent: ...

    def mark_sent(self, event: EventEnvelope) -> None: ...

    def pending(self, limit: int = 100) -> list[EventEnvelope]: ...


class MemoryIdempotencyStore:
    """Thread-safe outbox for tests and local single-process development."""

    def __init__(
        self, *, existing_payload_policy: ExistingPayloadPolicy = "reject"
    ) -> None:
        self._values: dict[tuple[str, str], tuple[EventEnvelope, str, str]] = {}
        self._lock = threading.Lock()
        self._existing_payload_policy = _validate_existing_payload_policy(
            existing_payload_policy
        )

    def get(self, source: str, event_id: str) -> StagedEvent | None:
        with self._lock:
            existing = self._values.get((source, event_id))
            if existing is None:
                return None
            stored, state, _ = existing
            return StagedEvent(stored, state, False)

    def stage(self, event: EventEnvelope) -> StagedEvent:
        key = (event.source, event.id)
        payload_hash = event_payload_hash(event)
        with self._lock:
            existing = self._values.get(key)
            if existing:
                stored, state, stored_hash = existing
                if (
                    stored_hash != payload_hash
                    and self._existing_payload_policy == "reject"
                ):
                    raise RuntimeError(
                        "Webhook event ID was reused with a different payload"
                    )
                return StagedEvent(stored, state, False)
            self._values[key] = (event, "pending", payload_hash)
            return StagedEvent(event, "pending", True)

    def mark_sent(self, event: EventEnvelope) -> None:
        key = (event.source, event.id)
        with self._lock:
            existing = self._values.get(key)
            if not existing:
                raise RuntimeError("Cannot mark an unstaged webhook event as sent")
            self._values[key] = (existing[0], "sent", existing[2])

    def pending(self, limit: int = 100) -> list[EventEnvelope]:
        with self._lock:
            return [
                event for event, state, _ in self._values.values() if state == "pending"
            ][:limit]


class SqliteIdempotencyStore:
    """Transactional local inbox/outbox for development and one-host deployments."""

    def __init__(
        self,
        path: str,
        *,
        existing_payload_policy: ExistingPayloadPolicy = "reject",
    ) -> None:
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._existing_payload_policy = _validate_existing_payload_policy(
            existing_payload_policy
        )
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS webhook_outbox (
                    source TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source, event_id)
                )
                """
            )

    def get(self, source: str, event_id: str) -> StagedEvent | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT payload, state FROM webhook_outbox
                WHERE source = ? AND event_id = ?
                """,
                (source, event_id),
            ).fetchone()
        if row is None:
            return None
        return StagedEvent(EventEnvelope.from_json(row[0]), row[1], False)

    def stage(self, event: EventEnvelope) -> StagedEvent:
        payload = event.to_json()
        payload_hash = event_payload_hash(event)
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO webhook_outbox(
                    source, event_id, payload, payload_hash, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (event.source, event.id, payload, payload_hash, now, now),
            )
            created = cursor.rowcount == 1
            row = self._connection.execute(
                """
                SELECT payload, payload_hash, state FROM webhook_outbox
                WHERE source = ? AND event_id = ?
                """,
                (event.source, event.id),
            ).fetchone()
        if row is None:
            raise RuntimeError("Webhook outbox row disappeared")
        if row[1] != payload_hash and self._existing_payload_policy == "reject":
            raise RuntimeError("Webhook event ID was reused with a different payload")
        return StagedEvent(EventEnvelope.from_json(row[0]), row[2], created)

    def mark_sent(self, event: EventEnvelope) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE webhook_outbox SET state = 'sent', updated_at = ?
                WHERE source = ? AND event_id = ?
                """,
                (now, event.source, event.id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("Cannot mark an unstaged webhook event as sent")

    def pending(self, limit: int = 100) -> list[EventEnvelope]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT payload FROM webhook_outbox
                WHERE state = 'pending' ORDER BY created_at LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [EventEnvelope.from_json(row[0]) for row in rows]


class AzureTableIdempotencyStore:
    """Azure Table inbox/outbox with private Blob claim-check payloads."""

    def __init__(
        self,
        connection_string: str | None,
        table_name: str,
        payloads: EventPayloadStore,
        *,
        account_url: str | None = None,
        existing_payload_policy: ExistingPayloadPolicy = "reject",
    ) -> None:
        try:
            from azure.data.tables import TableServiceClient, UpdateMode
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("azure-data-tables is required") from exc
        if connection_string:
            service = TableServiceClient.from_connection_string(connection_string)
        elif account_url:
            try:
                from azure.identity import DefaultAzureCredential
            except ImportError as exc:  # pragma: no cover - deployment dependency
                raise RuntimeError("azure-identity is required") from exc
            service = TableServiceClient(
                endpoint=account_url,
                credential=DefaultAzureCredential(),
            )
        else:
            raise RuntimeError(
                "IDEMPOTENCY_STORAGE_CONNECTION or IDEMPOTENCY_STORAGE_ACCOUNT_URL is required"
            )
        self._table = preprovisioned_table_client(service, table_name)
        self._update_mode = UpdateMode.MERGE
        self._payloads = payloads
        self._existing_payload_policy = _validate_existing_payload_policy(
            existing_payload_policy
        )

    @staticmethod
    def _row_key(event_id: str) -> str:
        return hashlib.sha256(event_id.encode("utf-8")).hexdigest()

    def get(self, source: str, event_id: str) -> StagedEvent | None:
        try:
            from azure.core.exceptions import ResourceNotFoundError
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("azure-core is required") from exc

        try:
            entity = self._table.get_entity(source, self._row_key(event_id))
        except ResourceNotFoundError:
            return None
        if str(entity.get("EventId") or "") != event_id:
            raise RuntimeError("Webhook event identity mismatch")
        stored = EventEnvelope.from_json(str(entity["DispatchPayload"]))
        return StagedEvent(stored, str(entity.get("State") or "pending"), False)

    def stage(self, event: EventEnvelope) -> StagedEvent:
        try:
            from azure.core.exceptions import ResourceExistsError
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("azure-core is required") from exc

        dispatch_event = self._payloads.prepare(event)
        payload_hash = event_payload_hash(event)
        row_key = self._row_key(event.id)
        created = True
        try:
            self._table.create_entity(
                {
                    "PartitionKey": event.source,
                    "RowKey": row_key,
                    "EventId": event.id,
                    "PayloadHash": payload_hash,
                    "DispatchPayload": dispatch_event.to_json(),
                    "State": "pending",
                    "Attempts": 0,
                    "CreatedAt": datetime.now(timezone.utc),
                    "UpdatedAt": datetime.now(timezone.utc),
                }
            )
        except ResourceExistsError:
            created = False
        entity = self._table.get_entity(event.source, row_key)
        if (
            entity.get("PayloadHash") != payload_hash
            and self._existing_payload_policy == "reject"
        ):
            raise RuntimeError("Webhook event ID was reused with a different payload")
        stored = EventEnvelope.from_json(str(entity["DispatchPayload"]))
        return StagedEvent(stored, str(entity.get("State") or "pending"), created)

    def mark_sent(self, event: EventEnvelope) -> None:
        self._table.update_entity(
            {
                "PartitionKey": event.source,
                "RowKey": self._row_key(event.id),
                "State": "sent",
                "UpdatedAt": datetime.now(timezone.utc),
            },
            mode=self._update_mode,
        )

    def pending(self, limit: int = 100) -> list[EventEnvelope]:
        entities = self._table.query_entities(
            query_filter="State eq 'pending'",
            select=["DispatchPayload"],
        )
        return [
            EventEnvelope.from_json(str(entity["DispatchPayload"]))
            for entity in islice(entities, limit)
        ]
