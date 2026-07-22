"""Microsoft Graph subscriptions and OneDrive delta synchronization."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class DeltaTokenExpired(RuntimeError):
    def __init__(self, recovery_url: str | None) -> None:
        super().__init__("Microsoft Graph delta token expired")
        self.recovery_url = recovery_url


class GraphRequestError(RuntimeError):
    """A non-success Microsoft Graph response with a preserved status code."""

    def __init__(self, status: int, details: str) -> None:
        super().__init__(f"Microsoft Graph returned {status}: {details}")
        self.status = status
        self.details = details


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never let urllib forward a Graph bearer token across a redirect.
        return None


class AccessTokenProvider(Protocol):
    def token(self) -> str: ...


class DefaultAzureTokenProvider:
    def __init__(self) -> None:
        try:
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("azure-identity is required") from exc
        self._credential = DefaultAzureCredential()

    def token(self) -> str:
        return self._credential.get_token(GRAPH_SCOPE).token


@dataclass(frozen=True)
class GraphResponse:
    status: int
    body: Mapping[str, Any]


class GraphClient:
    def __init__(
        self,
        tokens: AccessTokenProvider,
        base_url: str = GRAPH_ROOT,
        opener: Any = None,
    ) -> None:
        self._tokens = tokens
        self._base_url = base_url.rstrip("/")
        parsed_base = urllib.parse.urlsplit(self._base_url)
        if parsed_base.scheme != "https" or not parsed_base.netloc:
            raise ValueError("Microsoft Graph base URL must be an HTTPS origin")
        self._base_origin = (parsed_base.scheme, parsed_base.netloc.lower())
        self._opener = opener or urllib.request.build_opener(_NoRedirectHandler()).open

    def create_drive_subscription(
        self,
        *,
        drive_id: str,
        notification_url: str,
        client_state: str,
        lifecycle_notification_url: str | None = None,
        lifetime_days: int = 28,
    ) -> Mapping[str, Any]:
        if not 1 <= lifetime_days <= 29:
            raise ValueError(
                "OneDrive subscription lifetime must be between 1 and 29 days"
            )
        if len(client_state) > 128:
            raise ValueError("OneDrive client state must be at most 128 characters")
        expiration = datetime.now(timezone.utc) + timedelta(days=lifetime_days)
        payload: dict[str, Any] = {
            "changeType": "updated",
            "notificationUrl": notification_url,
            "resource": f"/drives/{drive_id}/root",
            "expirationDateTime": expiration.isoformat().replace("+00:00", "Z"),
            "clientState": client_state,
        }
        if lifecycle_notification_url:
            payload["lifecycleNotificationUrl"] = lifecycle_notification_url
        return self._request("POST", "/subscriptions", payload)

    def renew_subscription(
        self,
        subscription_id: str,
        *,
        lifetime_days: int = 28,
    ) -> Mapping[str, Any]:
        if not 1 <= lifetime_days <= 29:
            raise ValueError(
                "OneDrive subscription lifetime must be between 1 and 29 days"
            )
        expiration = datetime.now(timezone.utc) + timedelta(days=lifetime_days)
        return self._request(
            "PATCH",
            f"/subscriptions/{subscription_id}",
            {"expirationDateTime": expiration.isoformat().replace("+00:00", "Z")},
        )

    def reauthorize_subscription(self, subscription_id: str) -> Mapping[str, Any]:
        return self._request(
            "POST", f"/subscriptions/{subscription_id}/reauthorize", None
        )

    def get_subscription(self, subscription_id: str) -> Mapping[str, Any]:
        return self._request("GET", f"/subscriptions/{subscription_id}")

    def delete_subscription(self, subscription_id: str) -> None:
        self._request("DELETE", f"/subscriptions/{subscription_id}")

    def list_subscriptions(self) -> list[Mapping[str, Any]]:
        response = self._request("GET", "/subscriptions")
        values = response.get("value", [])
        if not isinstance(values, list):
            raise RuntimeError("Microsoft Graph subscriptions response is invalid")
        return [item for item in values if isinstance(item, Mapping)]

    def list_delta(
        self,
        drive_id: str,
        delta_link: str | None = None,
        *,
        latest_only: bool = False,
    ) -> tuple[list[Mapping[str, Any]], str]:
        if delta_link:
            url = delta_link
        else:
            suffix = "?token=latest" if latest_only else ""
            url = f"{self._base_url}/drives/{drive_id}/root/delta{suffix}"

        changes: list[Mapping[str, Any]] = []
        while url:
            page = self._request_url("GET", url)
            values = page.get("value", [])
            if not isinstance(values, list):
                raise RuntimeError("Microsoft Graph delta response has invalid value")
            changes.extend(item for item in values if isinstance(item, Mapping))
            next_link = page.get("@odata.nextLink")
            if next_link:
                url = str(next_link)
                continue
            delta = page.get("@odata.deltaLink")
            if not delta:
                raise RuntimeError(
                    "Microsoft Graph delta response omitted @odata.deltaLink"
                )
            return changes, str(delta)
        raise RuntimeError("Microsoft Graph delta request ended without a cursor")

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        return self._request_url(method, f"{self._base_url}{path}", payload)

    def _request_url(
        self,
        method: str,
        url: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        target = urllib.parse.urlsplit(url)
        if (target.scheme, target.netloc.lower()) != self._base_origin:
            raise RuntimeError(
                "Refusing to send Microsoft Graph credentials to another origin"
            )
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {
            "Authorization": f"Bearer {self._tokens.token()}",
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener(request, timeout=30) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 410 and "/delta" in target.path:
                recovery_url = exc.headers.get("Location") if exc.headers else None
                if recovery_url:
                    recovery_target = urllib.parse.urlsplit(recovery_url)
                    if (
                        recovery_target.scheme,
                        recovery_target.netloc.lower(),
                    ) != self._base_origin:
                        raise RuntimeError(
                            "Invalid Microsoft Graph delta recovery origin"
                        ) from exc
                raise DeltaTokenExpired(recovery_url) from exc
            details = exc.read().decode(errors="replace")
            raise GraphRequestError(exc.code, details) from exc
        parsed = json.loads(body or b"{}")
        if not isinstance(parsed, Mapping):
            raise RuntimeError("Microsoft Graph response must be a JSON object")
        return parsed
