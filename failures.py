"""What a provider failure *means*, as opposed to what number it arrived with.

A status code is a lossy key. The same number means different things on
different vendors, and worse, different things on the *same* vendor:

* Anthropic returns **400 `invalid_request_error`** both for a request this code
  built wrong and for hitting a spend limit someone set in the console. Same
  status, same type string. Only the message separates a bug from an outage.
* **429** is a rate limit that clears in seconds, or a monthly spend cap that
  never clears on its own. Anthropic's own docs name the tell: a tier spend-cap
  429 "has no `retry-after` header and keeps failing until access resumes".
* **402** is a dead balance on Anthropic and DeepSeek; Gemini has no 402 at all
  and reports an exhausted daily quota as a 429 `quota_exceeded`.

So failures are classified into a small set of *reasons*, and behaviour hangs off
the reason rather than off the code. Two decisions come out of it, and they are
different decisions:

1. **Should the next vendor try?** Yes for anything that means "this vendor is
   not answering". No for our own bugs, because a second vendor quietly
   succeeding would bury them.
2. **Will trying this vendor again this session ever work?** A rate limit clears.
   A revoked key and a dead balance do not. A link that failed for a terminal
   reason is skipped for the rest of the conversation instead of costing a
   wasted round trip on every single turn.

Sources, all read 2026-08-25:
  Anthropic  https://platform.claude.com/docs/en/api/errors
  DeepSeek   https://api-docs.deepseek.com/quick_start/error_codes
  Gemini     https://ai.google.dev/gemini-api/docs/api-errors
"""

from __future__ import annotations

from enum import StrEnum


class Reason(StrEnum):
    """Why a link could not answer. The value is what gets logged."""

    UNREACHABLE = "unreachable"
    """Never got there — DNS, refused connection, dropped socket."""

    TIMEOUT = "timeout"
    """Got there, no answer inside the deadline."""

    RATE_LIMITED = "rate-limited"
    """Too many requests. Clears on its own, usually in seconds."""

    OVERLOADED = "overloaded"
    """The vendor is having a bad day. 5xx, 529. Clears on its own."""

    EXHAUSTED = "exhausted"
    """Out of money or out of quota: dead balance, spend limit, daily cap.
    Does NOT clear on its own — a human has to go and fix an account."""

    CREDENTIAL = "credential"
    """The key is wrong, revoked, expired, or lacks permission. Also needs a
    human, and also will not fix itself before this conversation ends."""

    BAD_REQUEST = "bad-request"
    """We built the request wrong, or asked for a model that does not exist.
    Our bug. Never failed over."""

    UNKNOWN = "unknown"
    """Unrecognised. Treated as our problem, because the alternative is failing
    open on the failure nobody has thought of yet."""


# Reasons that mean "let the next vendor try". An allow-list: anything not named
# here stays put and reaches the screen. Failing over on an unrecognised failure
# would be the direction that fails open.
FAILOVER = frozenset(
    {
        Reason.UNREACHABLE,
        Reason.TIMEOUT,
        Reason.RATE_LIMITED,
        Reason.OVERLOADED,
        Reason.EXHAUSTED,
        Reason.CREDENTIAL,
    }
)

# Reasons that will not resolve themselves inside one conversation. A link that
# fails for one of these is skipped for the rest of the session rather than
# re-tried every turn: the retry cannot succeed, and it costs a round trip of
# latency on the one path that has none to spare.
#
# Deliberately excludes RATE_LIMITED and OVERLOADED. Both clear on their own,
# often within a turn or two, and demoting the primary for an hour over a
# transient blip would be a worse outcome than the blip.
TERMINAL = frozenset({Reason.EXHAUSTED, Reason.CREDENTIAL})

# What to put on the second screen. The person reading this is mid-conversation
# and can act on "the account is out of money"; they cannot act on
# "APIStatusError". Kept to the same six-word budget as any other card label.
HEADLINE: dict[Reason, str] = {
    Reason.UNREACHABLE: "No connection to the model",
    Reason.TIMEOUT: "Model timed out",
    Reason.RATE_LIMITED: "Rate limited",
    Reason.OVERLOADED: "Model provider overloaded",
    Reason.EXHAUSTED: "Provider out of credit",
    Reason.CREDENTIAL: "API key rejected",
    Reason.BAD_REQUEST: "Enrichment is down",
    Reason.UNKNOWN: "Enrichment is down",
}

# Substrings that turn an otherwise-ambiguous 400 into a money problem.
#
# String-matching an error message is exactly what Anthropic's docs tell you not
# to do ("catch the SDK's typed classes rather than string-matching error
# messages"). It is done here because for this one case there IS no typed
# distinction: a spend limit and a malformed request arrive as the same status
# and the same `error.type`, and the message is the only thing that differs.
#
# It fails in the safe direction. If a vendor rewords the message, the match
# stops firing and the failure classifies as BAD_REQUEST — which does not fail
# over and does surface an error card. That is a visible degradation, not a
# hidden one. Matching too eagerly would be the dangerous direction, so the
# substrings are all unambiguously about money.
MONEY_IN_A_400 = (
    "credit balance",
    "spend limit",
    "purchase credits",
    "billing",
    "insufficient balance",
    "out of balance",
    "quota",
)


def _connection_error_types() -> tuple[type[BaseException], ...]:
    """Transport failures, gathered from whichever vendor SDKs are installed.

    Assembled rather than hardcoded: the installed set follows the provider rows
    in providers.py, and an SDK that is not installed must not become an
    ImportError at startup.
    """
    types: list[type[BaseException]] = [TimeoutError, ConnectionError]
    try:
        import httpx

        types.append(httpx.TransportError)
    except ImportError:  # pragma: no cover — httpx ships under both SDKs today
        pass
    for module in ("anthropic", "openai"):
        try:
            sdk = __import__(module)
        except ImportError:
            continue
        for attr in ("APIConnectionError", "APITimeoutError"):
            if isinstance(candidate := getattr(sdk, attr, None), type):
                types.append(candidate)
    return tuple(types)


CONNECTION_ERRORS = _connection_error_types()

_TIMEOUT_NAMES = ("timeout", "deadline")


def _error_type(exc: BaseException) -> str:
    """The vendor's own machine-readable label, lowercased, or "".

    Anthropic and the OpenAI-compatible vendors both put a `type` inside the
    `error` object of the response body, and both SDKs keep that body on the
    exception. It is the most specific signal available and is preferred over
    the status code wherever it says something the code does not.
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            for key in ("type", "status", "code"):
                if isinstance(value := error.get(key), str):
                    return value.lower()
    for attr in ("type", "code"):
        if isinstance(value := getattr(exc, attr, None), str):
            return value.lower()
    return ""


def _has_retry_after(exc: BaseException) -> bool:
    """Whether the response carried a `retry-after` header.

    On a 429 this is the difference between "wait a moment" and "you are out of
    money until someone changes a setting" — Anthropic documents the spend-cap
    429 as the one with no such header.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    try:
        return bool(headers and headers.get("retry-after"))
    except (AttributeError, TypeError):  # not a header mapping after all
        return False


def classify(exc: BaseException) -> Reason:
    """What this failure means. Never raises; unrecognised maps to UNKNOWN.

    Ordered most-specific-signal first: transport, then the vendor's own error
    type, then the status code, and only then the message — which is consulted
    solely for the collisions where nothing else distinguishes the cases.
    """
    # 1. Transport. No status, nothing to read from a body.
    if isinstance(exc, CONNECTION_ERRORS):
        name = type(exc).__name__.lower()
        return Reason.TIMEOUT if any(t in name for t in _TIMEOUT_NAMES) else Reason.UNREACHABLE

    status = getattr(exc, "status_code", None)
    error_type = _error_type(exc)
    message = str(getattr(exc, "message", "") or exc).lower()

    if status is None and not error_type:
        return Reason.UNKNOWN

    # 2. The vendor's own label, where it is more specific than the code.
    #    Gemini reports an exhausted daily quota as 429 `quota_exceeded`, the
    #    same status as an ordinary rate limit.
    if "quota" in error_type or "billing" in error_type:
        return Reason.EXHAUSTED
    if "authentication" in error_type or "permission" in error_type:
        return Reason.CREDENTIAL
    if "overloaded" in error_type or "unavailable" in error_type:
        return Reason.OVERLOADED
    if "timeout" in error_type or "deadline" in error_type:
        return Reason.TIMEOUT

    # 3. Status code.
    if status in (401, 403):
        return Reason.CREDENTIAL
    if status == 402:
        return Reason.EXHAUSTED
    if status == 429:
        # A rate limit clears itself; a spend cap does not. The header is the
        # documented discriminator, and an explicit "quota"/money message beats
        # the header if both are present.
        if any(token in message for token in MONEY_IN_A_400):
            return Reason.EXHAUSTED
        return Reason.RATE_LIMITED if _has_retry_after(exc) else Reason.EXHAUSTED
    if status == 408 or status == 504:
        return Reason.TIMEOUT
    if status == 529 or (isinstance(status, int) and 500 <= status < 600):
        return Reason.OVERLOADED
    if status == 400:
        # 4. The one place a message is load-bearing: Anthropic returns a spend
        #    limit as a 400 `invalid_request_error`, indistinguishable by type
        #    from a request this code built wrong.
        if any(token in message for token in MONEY_IN_A_400):
            return Reason.EXHAUSTED
        return Reason.BAD_REQUEST
    if isinstance(status, int) and 400 <= status < 500:
        return Reason.BAD_REQUEST  # 404 wrong model, 413 too big, 422 bad params
    return Reason.UNKNOWN


def should_failover(exc: BaseException) -> bool:
    """Whether `exc` means the next vendor should get a turn."""
    return classify(exc) in FAILOVER


def is_terminal(reason: Reason) -> bool:
    """Whether retrying this link later in the same conversation is pointless."""
    return reason in TERMINAL
