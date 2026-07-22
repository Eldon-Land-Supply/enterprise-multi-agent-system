"""Canonical event envelope shared by webhook receivers and workers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


@dataclass(frozen=True)
class EventEnvelope:
    """Provider-neutral event with trace and idempotency metadata."""

    id: str
    source: str
    type: str
    data: Mapping[str, Any]
    correlation_id: str
    received_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    subject: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    version: str = "1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EventEnvelope":
        required = ("id", "source", "type", "data", "correlation_id")
        missing = [name for name in required if name not in value]
        if missing:
            raise ValueError(f"Missing event envelope fields: {', '.join(missing)}")
        if not isinstance(value["data"], Mapping):
            raise ValueError("Event envelope data must be an object")
        metadata = value.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError("Event envelope metadata must be an object")
        return cls(
            id=str(value["id"]),
            source=str(value["source"]),
            type=str(value["type"]),
            data=value["data"],
            correlation_id=str(value["correlation_id"]),
            received_at=str(
                value.get("received_at") or datetime.now(timezone.utc).isoformat()
            ),
            subject=str(value["subject"]) if value.get("subject") else None,
            metadata=metadata,
            version=str(value.get("version", "1")),
        )

    @classmethod
    def from_json(cls, value: str | bytes) -> "EventEnvelope":
        parsed = json.loads(value)
        if not isinstance(parsed, Mapping):
            raise ValueError("Event envelope must be a JSON object")
        return cls.from_dict(parsed)


def event_payload_hash(event: EventEnvelope) -> str:
    """Hash provider content while excluding the gateway receipt timestamp."""

    value = event.to_dict()
    value.pop("received_at", None)
    canonical = json.dumps(value, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
