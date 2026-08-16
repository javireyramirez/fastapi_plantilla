import hashlib
import hmac


def sign_token(token: str, secret: str) -> str:
    """Sign a token using HMAC-SHA256."""
    signature = hmac.new(
        key=secret.encode("utf-8"),
        msg=token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()
    return f"{token}.{signature}"


def unsign_token(signed_token: str, secret: str) -> str | None:
    """Verify HMAC signature and return raw token if valid."""
    if "." not in signed_token:
        return None

    token, signature = signed_token.rsplit(".", 1)
    expected_signature = hmac.new(
        key=secret.encode("utf-8"),
        msg=token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if hmac.compare_digest(signature, expected_signature):
        return token

    return None
