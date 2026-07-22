"""Durable state for Microsoft Graph OneDrive subscriptions."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from .azure_tables import preprovisioned_table_client


@dataclass(frozen=True)
class OneDriveSubscriptionState:
    """The Graph subscription metadata needed for renewal and reconciliation."""

    subscription_id: str
    drive_id: str
    expiration: datetime
    resource: str
    notification_url: str

    def __post_init__(self) -> None:
        for field_name in (
            "subscription_id",
            "drive_id",
            "resource",
            "notification_url",
        ):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} is required")
        if not isinstance(self.expiration, datetime):
            raise TypeError("expiration must be a datetime")
        expiration = self.expiration
        if expiration.tzinfo is None:
            expiration = expiration.replace(tzinfo=timezone.utc)
        else:
            expiration = expiration.astimezone(timezone.utc)
        object.__setattr__(self, "expiration", expiration)


class OneDriveSubscriptionStore(Protocol):
    def get(self, drive_id: str) -> OneDriveSubscriptionState | None: ...

    def save(self, state: OneDriveSubscriptionState) -> None: ...


class MemoryOneDriveSubscriptionStore:
    """Thread-safe subscription state for tests and local development."""

    def __init__(self) -> None:
        self._values: dict[str, OneDriveSubscriptionState] = {}
        self._lock = threading.Lock()

    def get(self, drive_id: str) -> OneDriveSubscriptionState | None:
        with self._lock:
            return self._values.get(drive_id)

    def save(self, state: OneDriveSubscriptionState) -> None:
        with self._lock:
            self._values[state.drive_id] = state


class AzureTableOneDriveSubscriptionStore:
    """Azure Table-backed subscription state with identity-based auth support."""

    _PARTITION_KEY = "onedrive-subscriptions"

    def __init__(
        self,
        connection_string: str | None,
        table_name: str = "OneDriveSubscriptions",
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
                "A storage connection string or storage account URL is required"
            )

        self._table = preprovisioned_table_client(service, table_name)
        # Replacing the complete entity avoids retaining fields from older schemas.
        self._update_mode = UpdateMode.REPLACE

    @staticmethod
    def _row_key(drive_id: str) -> str:
        return hashlib.sha256(drive_id.encode("utf-8")).hexdigest()

    def get(self, drive_id: str) -> OneDriveSubscriptionState | None:
        try:
            from azure.core.exceptions import ResourceNotFoundError
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("azure-core is required") from exc

        try:
            entity = self._table.get_entity(
                self._PARTITION_KEY,
                self._row_key(drive_id),
            )
        except ResourceNotFoundError:
            return None

        return OneDriveSubscriptionState(
            subscription_id=str(entity["SubscriptionId"]),
            drive_id=str(entity["DriveId"]),
            expiration=self._parse_expiration(entity["Expiration"]),
            resource=str(entity["Resource"]),
            notification_url=str(entity["NotificationUrl"]),
        )

    def save(self, state: OneDriveSubscriptionState) -> None:
        now = datetime.now(timezone.utc)
        self._table.upsert_entity(
            {
                "PartitionKey": self._PARTITION_KEY,
                "RowKey": self._row_key(state.drive_id),
                "SubscriptionId": state.subscription_id,
                "DriveId": state.drive_id,
                "Expiration": state.expiration,
                "Resource": state.resource,
                "NotificationUrl": state.notification_url,
                "UpdatedAt": now,
            },
            mode=self._update_mode,
        )

    @staticmethod
    def _parse_expiration(value: Any) -> datetime:
        if isinstance(value, datetime):
            expiration = value
        elif isinstance(value, str):
            try:
                expiration = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise RuntimeError(
                    "Stored OneDrive subscription expiration is invalid"
                ) from exc
        else:
            raise RuntimeError("Stored OneDrive subscription expiration is invalid")

        if expiration.tzinfo is None:
            return expiration.replace(tzinfo=timezone.utc)
        return expiration.astimezone(timezone.utc)
