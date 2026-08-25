"""The failure taxonomy: does a real vendor error mean what we think it means?

This is the gate the fallback chain hangs off. Everything here runs offline —
no keys, no network — which is the point: the classifier has to be checkable in
the environment that actually gates, and CI has neither.

The exception objects are built to the shape the SDKs really produce: a
`status_code`, a `body` with a nested `error.type`, a `message`, and response
headers. Each case cites the vendor doc it came from.
"""

from __future__ import annotations

import httpx
import pytest

import failures
from failures import Reason


class FakeResponse:
    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}


class VendorError(Exception):
    """The shape both SDKs raise: status, parsed body, message, response."""

    def __init__(self, status: int, error_type: str = "", message: str = "",
                 headers: dict[str, str] | None = None) -> None:
        super().__init__(message)
        self.status_code = status
        self.message = message
        self.body = {"error": {"type": error_type, "message": message}}
        self.response = FakeResponse(headers)


RETRY = {"retry-after": "30"}

# (label, exception, expected reason)
CASES = [
    # --- Anthropic, https://platform.claude.com/docs/en/api/errors ---------
    ("anthropic 401 revoked key",
     VendorError(401, "authentication_error", "invalid x-api-key"), Reason.CREDENTIAL),
    ("anthropic 402 billing",
     VendorError(402, "billing_error", "There's an issue with your billing"), Reason.EXHAUSTED),
    ("anthropic 403 no permission",
     VendorError(403, "permission_error", "not permitted"), Reason.CREDENTIAL),
    ("anthropic 404 wrong model",
     VendorError(404, "not_found_error", "model not found"), Reason.BAD_REQUEST),
    ("anthropic 413 too large",
     VendorError(413, "request_too_large", "too big"), Reason.BAD_REQUEST),
    ("anthropic 500",
     VendorError(500, "api_error", "internal"), Reason.OVERLOADED),
    ("anthropic 504",
     VendorError(504, "timeout_error", "timed out"), Reason.TIMEOUT),
    ("anthropic 529 overloaded",
     VendorError(529, "overloaded_error", "overloaded"), Reason.OVERLOADED),
    # The two documented collisions. Same status, same type, different meaning.
    ("anthropic 400 our malformed request",
     VendorError(400, "invalid_request_error",
                 "messages.0.content.0: unexpected field"), Reason.BAD_REQUEST),
    ("anthropic 400 spend limit (docs: 400 on spend limit)",
     VendorError(400, "invalid_request_error",
                 "Your credit balance is too low to access the API"), Reason.EXHAUSTED),
    ("anthropic 429 rate limit, has retry-after",
     VendorError(429, "rate_limit_error", "slow down", RETRY), Reason.RATE_LIMITED),
    ("anthropic 429 spend cap, no retry-after (documented tell)",
     VendorError(429, "rate_limit_error", "monthly cap reached"), Reason.EXHAUSTED),

    # --- DeepSeek, https://api-docs.deepseek.com/quick_start/error_codes ---
    ("deepseek 400 invalid format",
     VendorError(400, "invalid_request_error", "Invalid request body format"), Reason.BAD_REQUEST),
    ("deepseek 401 wrong key",
     VendorError(401, "authentication_error", "Authentication fails"), Reason.CREDENTIAL),
    ("deepseek 402 out of balance",
     VendorError(402, "insufficient_balance", "You have run out of balance"), Reason.EXHAUSTED),
    ("deepseek 422 invalid parameters",
     VendorError(422, "invalid_request_error", "invalid parameters"), Reason.BAD_REQUEST),
    ("deepseek 429 too quick",
     VendorError(429, "rate_limit_reached", "sending requests too quickly", RETRY),
     Reason.RATE_LIMITED),
    ("deepseek 503 overloaded",
     VendorError(503, "server_overloaded", "server is overloaded"), Reason.OVERLOADED),

    # --- Gemini, https://ai.google.dev/gemini-api/docs/api-errors ----------
    ("gemini 401 authentication",
     VendorError(401, "authentication", "missing API key"), Reason.CREDENTIAL),
    ("gemini 403 permission_denied",
     VendorError(403, "permission_denied", "denied"), Reason.CREDENTIAL),
    ("gemini 429 rate_limit_exceeded",
     VendorError(429, "rate_limit_exceeded", "per-minute limit", RETRY), Reason.RATE_LIMITED),
    ("gemini 429 quota_exceeded is NOT a rate limit",
     VendorError(429, "quota_exceeded", "daily quota exhausted", RETRY), Reason.EXHAUSTED),
    ("gemini 503 service_unavailable",
     VendorError(503, "service_unavailable", "overloaded"), Reason.OVERLOADED),
    ("gemini 504 deadline_exceeded",
     VendorError(504, "deadline_exceeded", "timeout"), Reason.TIMEOUT),

    # --- transport, no status at all --------------------------------------
    ("connection refused", httpx.ConnectError("refused"), Reason.UNREACHABLE),
    ("read timeout", httpx.ReadTimeout("slow"), Reason.TIMEOUT),
    ("connect timeout", httpx.ConnectTimeout("slow"), Reason.TIMEOUT),
    ("builtin TimeoutError", TimeoutError(), Reason.TIMEOUT),

    # --- unrecognised ------------------------------------------------------
    ("a plain ValueError", ValueError("schema mismatch"), Reason.UNKNOWN),
    ("a KeyError", KeyError("cards"), Reason.UNKNOWN),
]


@pytest.mark.parametrize("label,exc,expected", CASES, ids=[c[0] for c in CASES])
def test_classify(label: str, exc: BaseException, expected: Reason) -> None:
    assert failures.classify(exc) is expected


def test_bad_request_never_fails_over() -> None:
    """Our own bug must not be hidden by a second vendor answering."""
    assert Reason.BAD_REQUEST not in failures.FAILOVER
    assert Reason.UNKNOWN not in failures.FAILOVER


def test_unknown_is_refused_not_admitted() -> None:
    """The allow-list must not admit a failure mode nobody has thought of."""
    assert not failures.should_failover(ValueError("something new"))


def test_transient_reasons_do_not_retire_a_link() -> None:
    """A rate limit clears in seconds; retiring the primary over one is worse
    than the blip it was reacting to."""
    for reason in (Reason.RATE_LIMITED, Reason.OVERLOADED, Reason.TIMEOUT, Reason.UNREACHABLE):
        assert not failures.is_terminal(reason)


def test_money_and_key_problems_retire_a_link() -> None:
    """Neither fixes itself before the conversation ends."""
    assert failures.is_terminal(Reason.EXHAUSTED)
    assert failures.is_terminal(Reason.CREDENTIAL)


def test_every_reason_has_a_headline() -> None:
    """An unmapped reason would raise a KeyError while building the error card
    — inside the handler that exists to report failures."""
    for reason in Reason:
        assert failures.HEADLINE[reason]


def test_terminal_reasons_are_a_subset_of_failover() -> None:
    """Retiring a link that was never allowed to fail over would silently drop
    a link for a reason that should have surfaced instead."""
    assert failures.TERMINAL <= failures.FAILOVER
