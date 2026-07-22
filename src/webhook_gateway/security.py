"""Signature, replay, and shared-state validation helpers."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Callable

from .errors import WebhookError


def is_configured_secret(value: str | None) -> bool:
    """Reject missing and unresolved App Service Key Vault settings."""

    return bool(value) and not str(value).startswith("@Microsoft.KeyVault(")


def _require_secret(secret: str, name: str) -> bytes:
    if not is_configured_secret(secret):
        raise RuntimeError(f"{name} is not configured")
    return secret.encode("utf-8")


def verify_github_signature(body: bytes, signature: str, secret: str) -> None:
    """Verify GitHub's X-Hub-Signature-256 over the exact raw body."""

    if not signature:
        raise WebhookError(400, "missing_signature", "Missing X-Hub-Signature-256")
    if not signature.startswith("sha256="):
        raise WebhookError(401, "invalid_signature", "Invalid GitHub signature")
    expected = (
        "sha256="
        + hmac.new(
            _require_secret(secret, "GITHUB_WEBHOOK_SECRET"), body, hashlib.sha256
        ).hexdigest()
    )
    if not hmac.compare_digest(expected, signature):
        raise WebhookError(401, "invalid_signature", "Invalid GitHub signature")


def verify_timestamped_signature(
    body: bytes,
    signature: str,
    timestamp: str,
    secret: str,
    *,
    tolerance_seconds: int = 300,
    now: Callable[[], float] = time.time,
) -> None:
    """Verify a generic sha256 HMAC over ``timestamp.body`` with replay limits."""

    if not signature or not timestamp:
        raise WebhookError(
            400,
            "missing_signature",
            "Missing webhook signature or timestamp",
        )
    try:
        numeric_timestamp = int(timestamp)
    except ValueError as exc:
        raise WebhookError(
            401, "invalid_timestamp", "Invalid webhook timestamp"
        ) from exc
    if abs(now() - numeric_timestamp) > tolerance_seconds:
        raise WebhookError(
            401, "stale_request", "Webhook timestamp is outside tolerance"
        )

    supplied = signature.removeprefix("sha256=")
    if len(supplied) != 64:
        raise WebhookError(401, "invalid_signature", "Invalid webhook signature")
    signed_payload = timestamp.encode("ascii") + b"." + body
    expected = hmac.new(
        _require_secret(secret, "INBOUND_WEBHOOK_SECRET"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise WebhookError(401, "invalid_signature", "Invalid webhook signature")


def verify_client_state(received: str | None, expected: str) -> None:
    """Validate Microsoft Graph clientState without leaking its value."""

    if not received or not is_configured_secret(expected):
        raise WebhookError(401, "invalid_client_state", "Invalid client state")
    if not hmac.compare_digest(received, expected):
        raise WebhookError(401, "invalid_client_state", "Invalid client state")
