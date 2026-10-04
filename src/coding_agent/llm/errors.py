"""Classify provider failures so the router can cool down, skip, or retry."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorKind(Enum):
    RATE_LIMIT = "rate_limit"
    ACCOUNT_QUOTA = "account_quota"
    AUTH = "auth"
    MODEL_NOT_FOUND = "model_not_found"
    TRANSIENT = "transient"
    OTHER = "other"


@dataclass(frozen=True)
class ClassifiedError:
    kind: ErrorKind
    status: int | None
    message: str


_ACCOUNT_PHRASES = ("insufficient_quota", "insufficient quota")
_RATE_PHRASES = (
    "rate limit",
    "rate_limit",
    "ratelimit",
    "quota",
    "too many requests",
    "tpm",
    "rpd",
    "tokens",
)


def classify(exc: BaseException) -> ClassifiedError:
    """Map an SDK or transport exception to a failover action."""
    status = _status_code(exc)
    body = _body_text(exc)
    message = _short_message(exc)

    if status == 401:
        return ClassifiedError(ErrorKind.AUTH, status, message)
    if status == 404:
        return ClassifiedError(ErrorKind.MODEL_NOT_FOUND, status, message)
    if status == 403:
        if _is_account(body):
            return ClassifiedError(ErrorKind.ACCOUNT_QUOTA, status, message)
        if _is_rate(body):
            return ClassifiedError(ErrorKind.RATE_LIMIT, status, message)
        return ClassifiedError(ErrorKind.AUTH, status, message)
    if status == 402 or _is_account(body):
        return ClassifiedError(ErrorKind.ACCOUNT_QUOTA, status, message)
    if status == 429 or _is_rate(body):
        return ClassifiedError(ErrorKind.RATE_LIMIT, status, message)
    if _is_transient(exc, status, body):
        return ClassifiedError(ErrorKind.TRANSIENT, status, message)
    return ClassifiedError(ErrorKind.OTHER, status, message)


def _status_code(exc: BaseException) -> int | None:
    raw = getattr(exc, "status_code", None)
    if isinstance(raw, int):
        return raw
    return None


def _body_text(exc: BaseException) -> str:
    parts = [str(exc)]
    body = getattr(exc, "body", None)
    if body is not None:
        parts.append(str(body))
    response = getattr(exc, "response", None)
    if response is not None:
        text = getattr(response, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts).lower()


def _short_message(exc: BaseException) -> str:
    text = str(exc).strip().replace("\n", " ")
    if len(text) > 300:
        return text[:300] + "..."
    return text or type(exc).__name__


def _is_account(body: str) -> bool:
    return any(phrase in body for phrase in _ACCOUNT_PHRASES)


def _is_rate(body: str) -> bool:
    return any(phrase in body for phrase in _RATE_PHRASES)


def _is_transient(exc: BaseException, status: int | None, body: str) -> bool:
    if status is not None and status >= 500:
        return True
    name = type(exc).__name__.lower()
    if any(token in name for token in ("timeout", "connection", "connect")):
        return True
    return any(
        phrase in body
        for phrase in ("timed out", "timeout", "connection error", "connection reset")
    )
