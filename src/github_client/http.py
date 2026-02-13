import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional


class Timeout(Exception):
    """Raised when a request exceeds the allotted timeout."""


class ConnectionError(Exception):
    """Raised when a network connection cannot be established."""


@dataclass
class Response:
    status_code: int
    reason: str
    content: bytes
    headers: Dict[str, str]

    def json(self) -> object:
        return json.loads(self.content.decode() or "{}")

    @property
    def text(self) -> str:
        return self.content.decode()


class Session:
    def request(
        self,
        *,
        method: str,
        url: str,
        params: Optional[Dict[str, object]] = None,
        json: Optional[Dict[str, object]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> Response:
        headers = headers or {}
        full_url = self._prepare_url(url, params)
        data = self._prepare_body(json, headers)
        request = urllib.request.Request(full_url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return Response(
                    status_code=resp.status,
                    reason=resp.reason or "",
                    content=resp.read(),
                    headers=dict(resp.headers),
                )
        except urllib.error.HTTPError as exc:
            return Response(
                status_code=exc.code,
                reason=exc.reason or "",
                content=exc.read(),
                headers=dict(exc.headers or {}),
            )
        except socket.timeout as exc:
            raise Timeout(str(exc))
        except urllib.error.URLError as exc:
            raise ConnectionError(str(exc))

    @staticmethod
    def _prepare_url(url: str, params: Optional[Dict[str, object]]) -> str:
        if not params:
            return url
        return f"{url}?{urllib.parse.urlencode(params, doseq=True)}"

    @staticmethod
    def _prepare_body(json_body: Optional[Dict[str, object]], headers: Dict[str, str]) -> Optional[bytes]:
        if json_body is None:
            return None
        headers.setdefault("Content-Type", "application/json")
        return json.dumps(json_body).encode()
