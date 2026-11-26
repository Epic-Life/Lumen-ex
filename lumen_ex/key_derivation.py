"""Purpose-separated key derivation shared by registry and privacy stages."""

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


_KDF_SALT = b"lumen-ex-global-identity-registry-v1"
_KEY_LENGTH = 32


def derive_key(secret: str | bytes, purpose: str | bytes) -> bytes:
    secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
    purpose_bytes = purpose.encode("ascii") if isinstance(purpose, str) else purpose
    if not secret_bytes or not purpose_bytes:
        raise ValueError("key_derivation_input_invalid")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_LENGTH,
        salt=_KDF_SALT,
        info=b"lumen-ex/" + purpose_bytes,
    ).derive(secret_bytes)
