import time

import pytest

from chatgpt_bridge.protocol.signing import SigningError, build_hmac, new_nonce, verify_hmac


def test_hmac_roundtrip():
    secret = b"test-secret"
    ts = str(int(time.time() * 1000))
    nonce = new_nonce()
    sig = build_hmac(secret, "POST", "/v1/delegations", ts, nonce, "aa" * 32, "k" * 16)
    verify_hmac(secret, "POST", "/v1/delegations", ts, nonce, "aa" * 32, "k" * 16, sig)
    # tampered signature fails
    with pytest.raises(SigningError):
        verify_hmac(secret, "POST", "/v1/delegations", ts, nonce, "aa" * 32, "k" * 16, "0" * 64)


def test_hmac_rejects_stale_timestamp():
    secret = b"test-secret"
    old = str(int(time.time() * 1000) - 120_000)
    sig = build_hmac(secret, "GET", "/v1/health", old, new_nonce(), "aa" * 32, "k" * 16)
    with pytest.raises(SigningError):
        verify_hmac(secret, "GET", "/v1/health", old, new_nonce(), "aa" * 32, "k" * 16, sig)


def test_hmac_rejects_short_nonce():
    secret = b"test-secret"
    ts = str(int(time.time() * 1000))
    sig = build_hmac(secret, "GET", "/v1/health", ts, "short", "aa" * 32, "k" * 16)
    with pytest.raises(SigningError):
        verify_hmac(secret, "GET", "/v1/health", ts, "short", "aa" * 32, "k" * 16, sig)
