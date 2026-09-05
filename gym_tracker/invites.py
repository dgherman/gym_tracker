"""Invite-token helpers shared by the admin API and the public confirm route.

The raw token appears only in the emailed confirmation URL. Only its SHA-256
hex digest is ever persisted (``users.invite_token_hash``); lookups hash the
incoming token and match on the digest.
"""
import hashlib
import secrets


def generate_token() -> str:
    """A fresh URL-safe raw invite token."""
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    """SHA-256 hex digest of a raw invite token."""
    return hashlib.sha256(raw.encode()).hexdigest()
