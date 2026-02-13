import json
from typing import List
from unittest.mock import MagicMock, call, patch

import pytest

from github_client import ApiError, GitHubApiClient
from github_client.http import Response, Timeout


def make_response(status_code: int, body: object, reason: str = "") -> Response:
    return Response(
        status_code=status_code,
        reason=reason,
        content=json.dumps(body).encode(),
        headers={},
    )


def test_get_repository_success():
    session = MagicMock()
    repo_payload = {
        "id": 1,
        "name": "demo",
        "full_name": "octocat/demo",
        "private": False,
        "owner": {"login": "octocat", "id": 42},
    }
    session.request.return_value = make_response(200, repo_payload)

    client = GitHubApiClient(token="token", session=session)
    repo = client.get_repository("octocat", "demo")

    assert repo["full_name"] == "octocat/demo"
    assert repo["owner"]["login"] == "octocat"
    session.request.assert_called_once_with(
        method="GET",
        url="https://api.github.com/repos/octocat/demo",
        params=None,
        json=None,
        headers=client.default_headers,
        timeout=client.timeout,
    )


def test_request_retries_then_succeeds(monkeypatch):
    session = MagicMock()
    responses: List[object] = [Timeout("boom"), make_response(200, {"ok": True})]
    session.request.side_effect = responses

    client = GitHubApiClient(token="token", session=session, max_retries=2, backoff_factor=0.01)

    with patch("time.sleep") as mocked_sleep:
        result = client._request("GET", "/status", operation="retry_test")

    assert result == {"ok": True}
    assert session.request.call_count == 2
    mocked_sleep.assert_called_once()  # only one backoff before success


def test_request_failure_raises_api_error():
    session = MagicMock()
    session.request.return_value = make_response(
        404, {"message": "Not Found", "documentation_url": "docs"}, reason="Not Found"
    )

    client = GitHubApiClient(token="token", session=session)

    with pytest.raises(ApiError) as exc_info:
        client.get_repository("octocat", "missing")

    error = exc_info.value
    assert error.status_code == 404
    assert "Not Found" in error.message
    assert error.details == {"message": "Not Found", "documentation_url": "docs"}


def test_paginate_accumulates_items():
    session = MagicMock()
    session.request.side_effect = [
        make_response(200, [1, 2]),
        make_response(200, [3]),
        make_response(200, []),
    ]

    client = GitHubApiClient(token="token", session=session)
    items = list(client.paginate("/items", params={"per_page": 2}))

    assert items == [1, 2, 3]
    assert session.request.call_args_list == [
        call(
            method="GET",
            url="https://api.github.com/items",
            params={"per_page": 2, "page": 1},
            json=None,
            headers=client.default_headers,
            timeout=client.timeout,
        ),
        call(
            method="GET",
            url="https://api.github.com/items",
            params={"per_page": 2, "page": 2},
            json=None,
            headers=client.default_headers,
            timeout=client.timeout,
        ),
        call(
            method="GET",
            url="https://api.github.com/items",
            params={"per_page": 2, "page": 3},
            json=None,
            headers=client.default_headers,
            timeout=client.timeout,
        ),
    ]
