"""Durable OneDrive delta cursors, leases, and notification processing."""

from __future__ import annotations

import hashlib
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol

from .events import EventEnvelope

from .azure_tables import preprovisioned_table_client
from .onedrive import DeltaTokenExpired, GraphClient


class DeltaCursorStore(Protocol):
    def get(self, drive_id: str) -> str | None: ...

    def set(self, drive_id: str, delta_link: str) -> None: ...

    def acquire(self, drive_id: str, owner: str, lease_seconds: int = 720) -> bool: ...

    def renew(self, drive_id: str, owner: str, lease_seconds: int = 720) -> bool: ...

    def commit(self, drive_id: str, delta_link: str, owner: str) -> bool: ...

    def release(self, drive_id: str, owner: str) -> None: ...


class MemoryDeltaCursorStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._leases: dict[str, tuple[str, datetime]] = {}
        self._committed_owners: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, drive_id: str) -> str | None:
        with self._lock:
            return self._values.get(drive_id)

    def set(self, drive_id: str, delta_link: str) -> None:
        with self._lock:
            self._values[drive_id] = delta_link
            self._committed_owners.pop(drive_id, None)

    def acquire(self, drive_id: str, owner: str, lease_seconds: int = 720) -> bool:
        now = datetime.now(timezone.utc)
        with self._lock:
            existing = self._leases.get(drive_id)
            if existing and existing[1] > now and existing[0] != owner:
                return False
            self._leases[drive_id] = (owner, now + timedelta(seconds=lease_seconds))
            self._committed_owners.pop(drive_id, None)
            return True

    def renew(self, drive_id: str, owner: str, lease_seconds: int = 720) -> bool:
        with self._lock:
            existing = self._leases.get(drive_id)
            if not existing or existing[0] != owner:
                return False
            self._leases[drive_id] = (
                owner,
                datetime.now(timezone.utc) + timedelta(seconds=lease_seconds),
            )
            return True

    def commit(self, drive_id: str, delta_link: str, owner: str) -> bool:
        with self._lock:
            if (
                self._values.get(drive_id) == delta_link
                and self._committed_owners.get(drive_id) == owner
            ):
                return True
            existing = self._leases.get(drive_id)
            if not existing or existing[0] != owner:
                return False
            self._values[drive_id] = delta_link
            self._committed_owners[drive_id] = owner
            self._leases.pop(drive_id, None)
            return True

    def release(self, drive_id: str, owner: str) -> None:
        with self._lock:
            existing = self._leases.get(drive_id)
            if existing and existing[0] == owner:
                self._leases.pop(drive_id, None)


class AzureTableDeltaCursorStore:
    def __init__(
        self,
        connection_string: str | None,
        table_name: str = "OneDriveDeltaCursors",
        *,
        account_url: str | None = None,
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

    @staticmethod
    def _row_key(drive_id: str) -> str:
        return hashlib.sha256(drive_id.encode()).hexdigest()

    def _get_entity(self, drive_id: str) -> Any | None:
        try:
            from azure.core.exceptions import ResourceNotFoundError
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("azure-core is required") from exc
        try:
            return self._table.get_entity("onedrive", self._row_key(drive_id))
        except ResourceNotFoundError:
            return None

    def get(self, drive_id: str) -> str | None:
        entity = self._get_entity(drive_id)
        value = entity.get("DeltaLink") if entity else None
        return str(value) if value else None

    def set(self, drive_id: str, delta_link: str) -> None:
        self._table.upsert_entity(
            {
                "PartitionKey": "onedrive",
                "RowKey": self._row_key(drive_id),
                "DriveId": drive_id,
                "DeltaLink": delta_link,
                "CommittedLeaseOwner": "",
                "UpdatedAt": datetime.now(timezone.utc),
            },
            mode=self._update_mode,
        )

    def acquire(self, drive_id: str, owner: str, lease_seconds: int = 720) -> bool:
        try:
            from azure.core import MatchConditions
            from azure.core.exceptions import ResourceExistsError, ResourceModifiedError
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("azure-core is required") from exc

        now = datetime.now(timezone.utc)
        lease_until = now + timedelta(seconds=lease_seconds)
        entity = self._get_entity(drive_id)
        if entity is None:
            try:
                self._table.create_entity(
                    {
                        "PartitionKey": "onedrive",
                        "RowKey": self._row_key(drive_id),
                        "DriveId": drive_id,
                        "LeaseOwner": owner,
                        "LeaseUntil": lease_until,
                        "CommittedLeaseOwner": "",
                        "UpdatedAt": now,
                    }
                )
                return True
            except ResourceExistsError:
                return False

        current_until = entity.get("LeaseUntil")
        current_owner = str(entity.get("LeaseOwner") or "")
        if current_until and current_until > now and current_owner != owner:
            return False
        entity.update(
            {
                "LeaseOwner": owner,
                "LeaseUntil": lease_until,
                "CommittedLeaseOwner": "",
                "UpdatedAt": now,
            }
        )
        try:
            self._table.update_entity(
                entity,
                mode=self._update_mode,
                etag=entity.metadata["etag"],
                match_condition=MatchConditions.IfNotModified,
            )
            return True
        except ResourceModifiedError:
            return False

    def renew(self, drive_id: str, owner: str, lease_seconds: int = 720) -> bool:
        try:
            from azure.core import MatchConditions
            from azure.core.exceptions import ResourceModifiedError
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("azure-core is required") from exc
        entity = self._get_entity(drive_id)
        if entity is None or str(entity.get("LeaseOwner") or "") != owner:
            return False
        now = datetime.now(timezone.utc)
        entity.update(
            {
                "LeaseUntil": now + timedelta(seconds=lease_seconds),
                "UpdatedAt": now,
            }
        )
        try:
            self._table.update_entity(
                entity,
                mode=self._update_mode,
                etag=entity.metadata["etag"],
                match_condition=MatchConditions.IfNotModified,
            )
            return True
        except ResourceModifiedError:
            return False

    def commit(self, drive_id: str, delta_link: str, owner: str) -> bool:
        try:
            from azure.core import MatchConditions
            from azure.core.exceptions import ResourceModifiedError
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("azure-core is required") from exc
        entity = self._get_entity(drive_id)
        if entity is None:
            return False
        if (
            str(entity.get("DeltaLink") or "") == delta_link
            and str(entity.get("CommittedLeaseOwner") or "") == owner
        ):
            return True
        if str(entity.get("LeaseOwner") or "") != owner:
            return False
        now = datetime.now(timezone.utc)
        entity.update(
            {
                "DeltaLink": delta_link,
                "LeaseOwner": "",
                "LeaseUntil": now,
                "CommittedLeaseOwner": owner,
                "UpdatedAt": now,
            }
        )
        try:
            self._table.update_entity(
                entity,
                mode=self._update_mode,
                etag=entity.metadata["etag"],
                match_condition=MatchConditions.IfNotModified,
            )
            return True
        except ResourceModifiedError:
            return False

    def release(self, drive_id: str, owner: str) -> None:
        try:
            from azure.core import MatchConditions
            from azure.core.exceptions import ResourceModifiedError
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("azure-core is required") from exc
        entity = self._get_entity(drive_id)
        if entity is None or str(entity.get("LeaseOwner") or "") != owner:
            return
        entity.update(
            {
                "LeaseOwner": "",
                "LeaseUntil": datetime.now(timezone.utc),
                "UpdatedAt": datetime.now(timezone.utc),
            }
        )
        try:
            self._table.update_entity(
                entity,
                mode=self._update_mode,
                etag=entity.metadata["etag"],
                match_condition=MatchConditions.IfNotModified,
            )
        except ResourceModifiedError:
            return


def _safe_drive_item(value: Any) -> Any:
    """Remove preauthenticated download URLs without discarding useful metadata."""

    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("@", "").replace(".", "")
            if "downloadurl" in normalized:
                continue
            output[str(key)] = _safe_drive_item(item)
        return output
    if isinstance(value, list):
        return [_safe_drive_item(item) for item in value]
    return value


class OneDriveDeltaProcessor:
    def __init__(
        self, drive_id: str, graph: GraphClient, cursors: DeltaCursorStore
    ) -> None:
        self._drive_id = drive_id
        self._graph = graph
        self._cursors = cursors

    def process(self, event: EventEnvelope) -> Mapping[str, Any]:
        if event.type == "drive.lifecycle.reauthorizationRequired":
            subscription_id = str(event.data.get("subscriptionId") or "")
            if not subscription_id:
                raise ValueError("OneDrive lifecycle event omitted subscriptionId")
            response = self._graph.renew_subscription(subscription_id, lifetime_days=28)
            return {
                "lifecycle_event": "reauthorizationRequired",
                "subscription_id": subscription_id,
                "renewed": bool(response),
            }
        if event.type != "drive.updated":
            return {"ignored": True, "reason": "unsupported_onedrive_event"}

        owner = str(uuid.uuid4())
        if not self._cursors.acquire(self._drive_id, owner):
            raise RuntimeError("OneDrive sync is already in progress")
        try:
            previous_cursor = self._cursors.get(self._drive_id)
            try:
                changes, new_cursor = self._graph.list_delta(
                    self._drive_id,
                    delta_link=previous_cursor,
                )
            except DeltaTokenExpired as exc:
                changes, new_cursor = self._graph.list_delta(
                    self._drive_id,
                    delta_link=exc.recovery_url,
                )
            by_id: dict[str, Mapping[str, Any]] = {}
            without_id: list[Mapping[str, Any]] = []
            for raw_change in changes:
                change = _safe_drive_item(raw_change)
                item_id = change.get("id")
                if item_id:
                    by_id[str(item_id)] = change
                else:
                    without_id.append(change)
            collapsed = [*by_id.values(), *without_id]
            if not self._cursors.renew(self._drive_id, owner):
                raise RuntimeError("OneDrive delta lease was lost")
            return {
                "drive_id": self._drive_id,
                "changes": collapsed,
                "change_count": len(collapsed),
                "delta_cursor_advanced": False,
                "_checkpoint": {
                    "drive_id": self._drive_id,
                    "delta_link": new_cursor,
                    "lease_owner": owner,
                },
            }
        except Exception:
            self._cursors.release(self._drive_id, owner)
            raise

    def acknowledge(self, checkpoint: Mapping[str, Any]) -> None:
        drive_id = str(checkpoint.get("drive_id") or "")
        delta_link = str(checkpoint.get("delta_link") or "")
        owner = str(checkpoint.get("lease_owner") or "")
        if drive_id != self._drive_id or not delta_link or not owner:
            raise ValueError("Invalid OneDrive delta checkpoint")
        if not self._cursors.commit(self._drive_id, delta_link, owner):
            raise RuntimeError("OneDrive delta lease was lost before cursor commit")

    def abandon(self, checkpoint: Mapping[str, Any]) -> None:
        owner = str(checkpoint.get("lease_owner") or "")
        if owner:
            self._cursors.release(self._drive_id, owner)
