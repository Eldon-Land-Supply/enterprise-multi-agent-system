"""Invoke the protected OneDrive bootstrap using the Function managed identity."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request


def bootstrap(base_url: str, function_key: str) -> dict:
    if not function_key:
        raise RuntimeError("WEBHOOK_GATEWAY_FUNCTION_KEY is required")
    endpoint = f"{base_url.rstrip('/')}/admin/onedrive/bootstrap"
    if not endpoint.startswith("https://"):
        raise RuntimeError("The gateway base URL must use HTTPS")
    request = urllib.request.Request(
        endpoint,
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Functions-Key": function_key,
            "User-Agent": "enterprise-multi-agent-system",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            value = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        details = exc.read().decode(errors="replace")
        raise RuntimeError(f"Gateway returned {exc.code}: {details}") from exc
    if not isinstance(value, dict) or not value.get("subscription_id"):
        raise RuntimeError("Gateway returned an invalid bootstrap response")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        required=True,
        help="Deployed gateway base URL ending in /api",
    )
    args = parser.parse_args()
    result = bootstrap(
        args.base_url,
        os.environ.get("WEBHOOK_GATEWAY_FUNCTION_KEY", ""),
    )
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
