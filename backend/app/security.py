import hashlib
import hmac
import time


def sign_request(secret: str, timestamp: str, body: bytes) -> str:
    digest = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def verify_request(
    secret: str,
    timestamp: str,
    signature: str,
    body: bytes,
    *,
    now: int | None = None,
    tolerance_seconds: int = 300,
) -> bool:
    if not timestamp.isascii() or not timestamp.isdecimal():
        return False
    try:
        timestamp_value = int(timestamp)
        signature_bytes = signature.encode("ascii")
    except (UnicodeEncodeError, ValueError):
        return False
    current_time = int(time.time()) if now is None else now
    if abs(current_time - timestamp_value) > tolerance_seconds:
        return False
    expected = sign_request(secret, timestamp, body).encode("ascii")
    return hmac.compare_digest(expected, signature_bytes)
