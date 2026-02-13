import json
import time
from typing import Any, Dict, Iterable, List, Optional, TypeVar

from .errors import ApiError
from .http import ConnectionError, Response, Session, Timeout
from .types import ErrorResponse, Headers, HttpMethod, JSONValue, Payload, Repository

T = TypeVar("T")


class GitHubApiClient:
    """Lightweight GitHub API wrapper with retry, pagination, and typed responses."""

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.github.com",
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        session: Optional[Session] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.session = session or Session()
        self.default_headers: Headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "enterprise-multi-agent-system",
        }

    def get_repository(self, owner: str, repo: str) -> Repository:
        path = f"/repos/{owner}/{repo}"
        return self._request("GET", path, operation="get_repository")

    def paginate(self, path: str, params: Optional[Dict[str, Any]] = None) -> Iterable[JSONValue]:
        """Iterate through paginated GitHub resources using `page` and `per_page` parameters."""

        page = 1
        params = params.copy() if params else {}
        params.setdefault("per_page", 100)

        while True:
            params["page"] = page
            data: List[JSONValue] = self._request(
                "GET",
                path,
                params=params,
                operation="paginate",
            )
            if not data:
                break
            for item in data:
                yield item
            page += 1

    def _request(
        self,
        method: HttpMethod,
        path: str,
        *,
        operation: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Payload] = None,
    ) -> T:
        url = f"{self.base_url}{path}"
        headers = self.default_headers
        last_error: Optional[ApiError] = None

        for attempt in range(1, self.max_retries + 2):
            try:
                request_params = params.copy() if params else None
                response = self.session.request(
                    method=method,
                    url=url,
                    params=request_params,
                    json=json_body,
                    headers=headers,
                    timeout=self.timeout,
                )
                if self._is_retryable_status(response.status_code):
                    raise self._build_error(response, operation)
                return self._decode_response(response, operation)
            except (Timeout, ConnectionError) as exc:
                last_error = ApiError(status_code=0, message=str(exc), operation=operation)
            except ApiError as exc:
                last_error = exc

            if attempt > self.max_retries:
                break
            self._sleep_with_backoff(attempt)

        if last_error is None:
            last_error = ApiError(status_code=0, message="Unknown error", operation=operation)
        raise last_error

    def _decode_response(self, response: Response, operation: str) -> Any:
        if 200 <= response.status_code < 300:
            if response.content:
                try:
                    return response.json()
                except json.JSONDecodeError:
                    return response.text
            return None
        raise self._build_error(response, operation)

    def _build_error(self, response: Response, operation: str) -> ApiError:
        message = response.reason or "Request failed"
        details: Optional[ErrorResponse] = None

        try:
            body = response.json()
            if isinstance(body, dict):
                details = body  # type: ignore[assignment]
                message = body.get("message", message)
        except json.JSONDecodeError:
            pass

        return ApiError(status_code=response.status_code, message=message, operation=operation, details=details)

    def _sleep_with_backoff(self, attempt: int) -> None:
        delay = self.backoff_factor * (2 ** (attempt - 1))
        time.sleep(delay)

    @staticmethod
    def _is_retryable_status(status: int) -> bool:
        return status >= 500 or status == 429
