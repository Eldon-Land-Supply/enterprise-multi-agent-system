import hashlib
import hmac

import pytest

from webhook_gateway.errors import WebhookError
from webhook_gateway.security import (
    is_configured_secret,
    verify_client_state,
    verify_github_signature,
    verify_timestamped_signature,
)


def test_unresolved_key_vault_reference_is_not_configured():
    unresolved = "@Microsoft.KeyVault(SecretUri=https://vault.example/secrets/webhook)"
    assert not is_configured_secret(unresolved)
    with pytest.raises(RuntimeError, match="not configured"):
        verify_github_signature(b"{}", "sha256=" + "0" * 64, unresolved)


def test_github_signature_accepts_exact_raw_body():
    body = b'{"action":"opened"}\n'
    secret = "test-secret"
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    verify_github_signature(body, signature, secret)


@pytest.mark.parametrize("signature", ["", "sha1=abc", "sha256=" + "0" * 64])
def test_github_signature_rejects_missing_or_invalid_values(signature):
    with pytest.raises(WebhookError) as exc_info:
        verify_github_signature(b"{}", signature, "test-secret")

    assert exc_info.value.status_code in {400, 401}


def test_timestamped_signature_accepts_fresh_request():
    body = b'{"id":"evt-1"}'
    timestamp = "1700000000"
    secret = "shared"
    digest = hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()

    verify_timestamped_signature(
        body,
        f"sha256={digest}",
        timestamp,
        secret,
        now=lambda: 1700000001,
    )


def test_timestamped_signature_rejects_replay():
    with pytest.raises(WebhookError) as exc_info:
        verify_timestamped_signature(
            b"{}",
            "sha256=" + "0" * 64,
            "1700000000",
            "shared",
            now=lambda: 1700001000,
        )

    assert exc_info.value.code == "stale_request"


def test_client_state_is_required_and_compared():
    verify_client_state("expected", "expected")

    with pytest.raises(WebhookError) as exc_info:
        verify_client_state("wrong", "expected")

    assert exc_info.value.code == "invalid_client_state"

    unresolved = "@Microsoft.KeyVault(SecretUri=https://vault.example/secrets/state)"
    with pytest.raises(WebhookError):
        verify_client_state(unresolved, unresolved)
