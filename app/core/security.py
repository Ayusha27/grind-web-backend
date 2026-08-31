# app/core/security.py
import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import settings
from app.core.exceptions import AuthError

logger = logging.getLogger(__name__)

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_admin_credentials(username: str, password: str) -> bool:
    """Replaces `$username == "admin" && $password == "Grind@2026"`.

    compare_digest on the username prevents a timing oracle that would leak
    the admin username one character at a time. argon2 verify is constant
    time by construction.
    """
    username_ok = secrets.compare_digest(username, settings.ADMIN_USERNAME)
    # verify() is typed Literal[True] (it raises on failure), so the variable
    # needs the wider type for the except branch.
    password_ok: bool
    try:
        password_ok = _hasher.verify(settings.ADMIN_PASSWORD_HASH, password)
    # InvalidHashError subclasses ValueError, not VerificationError: without it
    # a malformed ADMIN_PASSWORD_HASH turns every login into a 500 rather
    # than a clean rejection.
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        password_ok = False

    # Both are evaluated before returning, so a wrong username costs the same
    # time as a wrong password.
    return username_ok and bool(password_ok)


def create_access_token(subject: str, *, extra: dict[str, Any] | None = None) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "typ": "admin",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],  # list, never "any"
        )
    except jwt.ExpiredSignatureError:
        raise AuthError("Session expired. Please log in again.") from None
    except jwt.InvalidTokenError:
        raise AuthError("Invalid credentials") from None


def verify_razorpay_signature(*, order_id: str, payment_id: str, signature: str) -> bool:
    """Decision 2 — the check verify_payment.php never performed.

    Razorpay signs `"{order_id}|{payment_id}"` with your key secret using
    HMAC-SHA256 and hex-encodes it. compare_digest is required: a naive `==`
    on a hex string leaks the correct prefix through timing.
    """
    if not (order_id and payment_id and signature):
        return False

    expected = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode("utf-8"),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)
