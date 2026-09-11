"""API-kalit va webhook imzosi testlari."""

import json
import time

from app.core.security import (
    generate_api_key,
    hash_api_key,
    key_hint,
    sign_webhook,
    verify_api_key,
    verify_webhook_signature,
)


def test_api_key_format_and_uniqueness():
    live, test = generate_api_key(), generate_api_key(test=True)
    assert live.startswith("sk_live_")
    assert test.startswith("sk_test_")
    assert generate_api_key() != generate_api_key()


def test_api_key_hash_roundtrip():
    key = generate_api_key()
    digest = hash_api_key(key)
    assert len(digest) == 64
    assert verify_api_key(key, digest)
    assert not verify_api_key(generate_api_key(), digest)


def test_key_hint_masks_the_secret():
    key = generate_api_key()
    hint = key_hint(key)
    assert hint.endswith(key[-4:])
    assert key not in hint


def test_webhook_signature_roundtrip():
    payload = json.dumps({"event": "order.completed"}).encode()
    header, _ = sign_webhook(payload, "whsec_test")
    assert verify_webhook_signature(payload, header, "whsec_test")


def test_webhook_signature_rejects_tampering():
    header, _ = sign_webhook(b'{"amount":10}', "whsec_test")
    assert not verify_webhook_signature(b'{"amount":9999}', header, "whsec_test")
    assert not verify_webhook_signature(b'{"amount":10}', header, "wrong_secret")


def test_webhook_signature_rejects_stale_timestamp():
    payload = b"{}"
    old_ts = int(time.time()) - 3600
    header, _ = sign_webhook(payload, "whsec_test", timestamp=old_ts)
    assert not verify_webhook_signature(payload, header, "whsec_test", tolerance_seconds=300)
