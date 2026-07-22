"""Idempotent daily quotas for repository-scoped model calls."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol

from .azure_tables import preprovisioned_table_client


Clock = Callable[[], datetime]


class DailyQuota(Protocol):
    """Reserve at most ``limit`` unique events for each repository and UTC day."""

    def allow(self, repository: str, event_id: str, limit: int) -> bool: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _validated_inputs(repository: str, event_id: str, limit: int) -> tuple[str, str]:
    normalized_repository = repository.strip().casefold()
    normalized_event_id = event_id.strip()
    if not normalized_repository:
        raise ValueError("repository is required")
    if not normalized_event_id:
        raise ValueError("event_id is required")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    return normalized_repository, normalized_event_id


def _utc_date(clock: Clock) -> str:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("quota clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).date().isoformat()


def _digest(namespace: str, value: str) -> str:
    material = f"{namespace}\0{len(value)}\0{value}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _partition_key(repository: str, utc_date: str) -> str:
    """Return an opaque Azure Table-safe key for one repository-day."""

    return _digest("repository-day", f"{len(repository)}:{repository}{utc_date}")


def _event_row_key(event_id: str) -> str:
    return f"event-{_digest('event', event_id)}"


class MemoryDailyQuota:
    """Thread-safe daily quota for tests and local single-process development."""

    def __init__(self, *, now: Clock | None = None) -> None:
        self._now = now or _utc_now
        self._events: dict[tuple[str, str], set[str]] = {}
        self._lock = threading.Lock()

    def allow(self, repository: str, event_id: str, limit: int) -> bool:
        repository, event_id = _validated_inputs(repository, event_id, limit)
        day = _utc_date(self._now)
        key = (_digest("repository", repository), _digest("date", day))
        event_hash = _digest("event", event_id)
        with self._lock:
            accepted = self._events.setdefault(key, set())
            if event_hash in accepted:
                return True
            if len(accepted) >= limit:
                return False
            accepted.add(event_hash)
            return True


class AzureTableDailyQuota:
    """Durable quota using atomic Table transactions and optimistic ETags.

    Each repository-day is one hashed partition. A reservation atomically updates
    the counter entity and creates a hashed per-event entity, so retries of an
    already accepted event never consume another unit.
    """

    def __init__(
        self,
        connection_string: str | None = None,
        table_name: str = "DailyModelCallQuota",
        *,
        account_url: str | None = None,
        now: Clock | None = None,
        table_client: Any | None = None,
        max_retries: int = 8,
    ) -> None:
        if max_retries < 1:
            raise ValueError("max_retries must be positive")
        self._now = now or _utc_now
        self._max_retries = max_retries
        if table_client is not None:
            self._table = table_client
            return

        try:
            from azure.data.tables import TableServiceClient
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
                "A quota storage connection string or account URL is required"
            )
        self._table = preprovisioned_table_client(service, table_name)

    def _get_entity(self, partition_key: str, row_key: str) -> Any | None:
        try:
            from azure.core.exceptions import ResourceNotFoundError
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("azure-core is required") from exc
        try:
            return self._table.get_entity(partition_key, row_key)
        except ResourceNotFoundError:
            return None

    @staticmethod
    def _etag(entity: Any) -> str:
        metadata = getattr(entity, "metadata", None) or {}
        etag = metadata.get("etag")
        if not etag and hasattr(entity, "get"):
            etag = entity.get("etag") or entity.get("odata.etag")
        if not etag:
            raise RuntimeError("Quota counter entity is missing its ETag")
        return str(etag)

    @staticmethod
    def _is_retryable_conflict(error: Exception) -> bool:
        try:
            from azure.core.exceptions import ResourceExistsError, ResourceModifiedError
            from azure.data.tables import TableTransactionError
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("Azure Tables dependencies are required") from exc
        if isinstance(error, (ResourceExistsError, ResourceModifiedError)):
            return True
        return isinstance(error, TableTransactionError) and getattr(
            error, "status_code", None
        ) in {409, 412}

    def allow(self, repository: str, event_id: str, limit: int) -> bool:
        try:
            from azure.core import MatchConditions
            from azure.data.tables import UpdateMode
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("Azure Tables dependencies are required") from exc

        repository, event_id = _validated_inputs(repository, event_id, limit)
        now = self._now()
        day = _utc_date(lambda: now)
        partition_key = _partition_key(repository, day)
        event_row_key = _event_row_key(event_id)

        for _ in range(self._max_retries):
            if self._get_entity(partition_key, event_row_key) is not None:
                return True

            counter = self._get_entity(partition_key, "quota")
            event_entity = {
                "PartitionKey": partition_key,
                "RowKey": event_row_key,
                "UtcDate": day,
                "CreatedAt": now,
            }
            if counter is None:
                counter_entity = {
                    "PartitionKey": partition_key,
                    "RowKey": "quota",
                    "UtcDate": day,
                    "Count": 1,
                    "UpdatedAt": now,
                }
                operations = [
                    ("create", counter_entity),
                    ("create", event_entity),
                ]
            else:
                count = int(counter.get("Count") or 0)
                if count >= limit:
                    return False
                counter_entity = dict(counter)
                counter_entity.update({"Count": count + 1, "UpdatedAt": now})
                operations = [
                    (
                        "update",
                        counter_entity,
                        {
                            "mode": UpdateMode.REPLACE,
                            "etag": self._etag(counter),
                            "match_condition": MatchConditions.IfNotModified,
                        },
                    ),
                    ("create", event_entity),
                ]
            try:
                self._table.submit_transaction(operations)
                return True
            except Exception as exc:
                if not self._is_retryable_conflict(exc):
                    raise

        raise RuntimeError("Could not reserve daily quota after concurrent updates")


# Explicit Store aliases make the integration intent clear while retaining short
# class names for callers.
MemoryDailyQuotaStore = MemoryDailyQuota
AzureTableDailyQuotaStore = AzureTableDailyQuota
