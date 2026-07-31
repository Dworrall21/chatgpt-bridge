"""Protocol package: schemas, canonicalization, signing."""

from .canonicalization import sha256_hex
from .signing import SigningError, build_hmac, verify_hmac

__all__ = ["sha256_hex", "build_hmac", "verify_hmac", "SigningError"]
