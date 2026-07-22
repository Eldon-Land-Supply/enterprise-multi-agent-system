"""Idempotently create or update the repository webhook."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from typing import Any, Mapping


# Push is the safe default because only repository writers can create it. Public
# PR/comment/check activity requires explicit admission configuration and opt-in.
DEFAULT_EVENTS = ["push"]


def github_request(
    method: str,
    path: str,
    token: str,
    payload: Mapping[str, Any] | None = None,
) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "enterprise-multi-agent-system",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode(errors="replace")
        raise RuntimeError(f"GitHub returned {exc.code}: {details}") from exc


def configure(
    repo: str,
    url: str,
    token: str,
    secret: str,
    events: list[str] | None = None,
) -> Mapping[str, Any]:
    if not token or not secret:
        raise RuntimeError("GITHUB_TOKEN and GITHUB_WEBHOOK_SECRET are required")
    hooks: list[Mapping[str, Any]] = []
    for page in range(1, 101):
        batch = github_request(
            "GET", f"/repos/{repo}/hooks?per_page=100&page={page}", token
        )
        if not isinstance(batch, list):
            raise RuntimeError("GitHub hook listing returned an invalid response")
        hooks.extend(item for item in batch if isinstance(item, Mapping))
        if len(batch) < 100:
            break
    else:
        raise RuntimeError("GitHub hook listing exceeded 10,000 entries")
    existing = next(
        (
            hook
            for hook in hooks
            if isinstance(hook, Mapping)
            and isinstance(hook.get("config"), Mapping)
            and hook["config"].get("url") == url
        ),
        None,
    )
    payload = {
        "name": "web",
        "active": True,
        "events": list(dict.fromkeys(events or DEFAULT_EVENTS)),
        "config": {
            "url": url,
            "content_type": "json",
            "secret": secret,
            "insecure_ssl": "0",
        },
    }
    if existing:
        return github_request(
            "PATCH", f"/repos/{repo}/hooks/{existing['id']}", token, payload
        )
    return github_request("POST", f"/repos/{repo}/hooks", token, payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="GitHub repository as owner/name")
    parser.add_argument(
        "--url", required=True, help="Public HTTPS /webhooks/github URL"
    )
    parser.add_argument(
        "--event",
        dest="events",
        action="append",
        help="Explicit event opt-in; repeat for multiple events (default: push)",
    )
    args = parser.parse_args()
    result = configure(
        args.repo,
        args.url,
        os.environ.get("GITHUB_TOKEN", ""),
        os.environ.get("GITHUB_WEBHOOK_SECRET", ""),
        events=args.events,
    )
    print(json.dumps({"id": result.get("id"), "active": result.get("active")}))


if __name__ == "__main__":
    main()
