from __future__ import annotations

from dataclasses import dataclass
import base64
import json
import logging
import time
from typing import Any

import httpx
from fastapi import HTTPException, status
from jose import jwk

from app.core.config import settings

APPLE_ISSUER = "https://appleid.apple.com"
APPLE_JWKS_CACHE_TTL_SECONDS = 60 * 60 * 6

logger = logging.getLogger(__name__)

_apple_jwks_cache: dict[str, Any] = {"expires_at": 0.0, "keys": []}


@dataclass(frozen=True)
class VerifiedAppleIdentity:
    subject: str
    email: str | None
    email_verified: bool
    audience: str


def verify_apple_identity_token(
    identity_token: str,
    *,
    expected_user_identifier: str | None = None,
) -> VerifiedAppleIdentity:
    token = identity_token.strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Apple identity token is missing.")

    try:
        header = _decode_jwt_segment(token, 0)
        claims = _decode_jwt_segment(token, 1)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Apple identity token is malformed.") from exc

    key_id = str(header.get("kid", "")).strip()
    if not key_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Apple identity token is missing a key id.")

    jwks_key = _find_apple_jwk(key_id)
    _verify_signature(token, jwks_key)

    issuer = str(claims.get("iss", "")).strip()
    if issuer != APPLE_ISSUER:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Apple identity token issuer is invalid.")

    audience = _resolve_audience(claims.get("aud"))
    if audience not in settings.apple_sign_in_audiences:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Apple identity token audience is invalid.")

    expiration = claims.get("exp")
    if not isinstance(expiration, (int, float)) or float(expiration) <= time.time():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Apple identity token has expired.")

    subject = str(claims.get("sub", "")).strip()
    if not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Apple identity token subject is missing.")

    if expected_user_identifier and subject != expected_user_identifier.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Apple identity token does not match the provided user identifier.",
        )

    email = claims.get("email")
    email_value = str(email).strip().lower() if isinstance(email, str) and email.strip() else None

    return VerifiedAppleIdentity(
        subject=subject,
        email=email_value,
        email_verified=_normalize_boolean_claim(claims.get("email_verified")),
        audience=audience,
    )


def _find_apple_jwk(key_id: str) -> dict[str, Any]:
    cached_keys = _load_apple_jwks()
    for key in cached_keys:
        if str(key.get("kid", "")).strip() == key_id:
            return key

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Apple signing key was not found.")


def _load_apple_jwks() -> list[dict[str, Any]]:
    now = time.time()
    if _apple_jwks_cache["keys"] and now < float(_apple_jwks_cache["expires_at"]):
        return list(_apple_jwks_cache["keys"])

    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(settings.APPLE_JWKS_URL)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.exception("apple-jwks-fetch-failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Apple Sign In is temporarily unavailable. Please try again shortly.",
        ) from exc

    payload = response.json()
    keys = payload.get("keys")
    if not isinstance(keys, list) or not keys:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Apple Sign In verification keys are unavailable.",
        )

    _apple_jwks_cache["keys"] = keys
    _apple_jwks_cache["expires_at"] = now + APPLE_JWKS_CACHE_TTL_SECONDS
    return keys


def _verify_signature(token: str, jwks_key: dict[str, Any]) -> None:
    try:
        public_key = jwk.construct(jwks_key)
        signing_input, signature_segment = token.rsplit(".", 1)
        decoded_signature = _decode_base64url(signature_segment)
        verified = public_key.verify(signing_input.encode("utf-8"), decoded_signature)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Apple identity token signature is invalid.",
        ) from exc

    if not verified:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Apple identity token signature is invalid.",
        )


def _decode_jwt_segment(token: str, index: int) -> dict[str, Any]:
    segments = token.split(".")
    if len(segments) != 3:
        raise ValueError("JWT must contain three segments.")

    segment = segments[index]
    payload = _decode_base64url(segment)
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JWT segment did not decode to an object.")
    return value


def _decode_base64url(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("utf-8"))


def _resolve_audience(claim_value: Any) -> str:
    if isinstance(claim_value, str):
        return claim_value.strip()
    if isinstance(claim_value, list):
        for value in claim_value:
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _normalize_boolean_claim(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    if isinstance(value, (int, float)):
        return value != 0
    return False
