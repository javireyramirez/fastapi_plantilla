import base64
import hashlib
import hmac
import os
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from yarl import URL

from fastapi_plantilla.core.config import settings

BACKUP_CODE_ALPHABET: str = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
TWO_FACTOR_KDF_INFO: bytes = b"2fa_totp_encryption"

__all__ = [
    "BACKUP_CODE_ALPHABET",
    "decrypt_totp_secret",
    "derive_encryption_key",
    "encrypt_totp_secret",
    "generate_backup_codes",
    "is_safe_callback_url",
    "sign_token",
    "unsign_token",
]


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


def is_safe_callback_url(url_str: str | None) -> bool:
    """Validate that callback_url is a relative path or matches frontend_url origin."""
    if not url_str:
        return False
    if url_str.startswith("/") and not url_str.startswith("//"):
        return True
    if settings.frontend_url:
        try:
            target = URL(url_str)
            frontend = URL(settings.frontend_url)
            if (
                target.scheme == frontend.scheme
                and target.host == frontend.host
                and target.port == frontend.port
            ):
                return True
        except Exception:
            return False
    return False


def derive_encryption_key(auth_secret: str) -> bytes:
    """Derive 32-byte AES key from auth_secret using HKDF-SHA256."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=TWO_FACTOR_KDF_INFO,
    )
    return hkdf.derive(auth_secret.encode("utf-8"))


def encrypt_totp_secret(secret: str, auth_secret: str) -> str:
    """Encrypt TOTP secret using AES-256-GCM with derived key and 12B nonce."""
    key = derive_encryption_key(auth_secret)
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ct_tag = aesgcm.encrypt(nonce, secret.encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + ct_tag).decode("ascii")


def decrypt_totp_secret(payload_b64: str, auth_secret: str) -> str:
    """Decrypt AES-256-GCM encrypted TOTP secret."""
    key = derive_encryption_key(auth_secret)
    raw = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
    nonce, ct_tag = raw[:12], raw[12:]
    aesgcm = AESGCM(key)
    decrypted = aesgcm.decrypt(nonce, ct_tag, None)
    return decrypted.decode("utf-8")


def generate_backup_codes(count: int = 8) -> tuple[list[str], list[str]]:
    """Generate unambiguous recovery backup codes (plain and SHA-256 hashed)."""
    plain_codes: list[str] = []
    hashed_codes: list[str] = []
    for _ in range(count):
        part1 = "".join(secrets.choice(BACKUP_CODE_ALPHABET) for _ in range(4))
        part2 = "".join(secrets.choice(BACKUP_CODE_ALPHABET) for _ in range(4))
        code = f"{part1}-{part2}"
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        plain_codes.append(code)
        hashed_codes.append(code_hash)
    return plain_codes, hashed_codes
