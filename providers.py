"""One row per provider gloss is known to work against.

Four things have to agree for a provider to work at all: the model id, the
kwargs that model accepts, whether it wants a cache breakpoint named
explicitly, and the prefix size below which it silently stops caching. Before
this file they were four independent environment variables, and setting
`GLOSS_PROVIDER=anthropic` while an old `GLOSS_MODEL_KWARGS` still held
Gemini's `thinking_level` produced a 400 from a vendor that has never heard of
that parameter. The cache floor had drifted further still — it was hardcoded a
second time inside `tools/check_pack.py`, so the gate and the server could
disagree about whether a pack was big enough.

Selecting a provider now selects the whole row. Overrides exist, but they are
constrained so the row cannot come apart — see `resolve()`.

**This is an allow-list.** A provider that is not named here is refused rather
than attempted, because "unrecognised" and "unsupported" are the same thing
from a live conversation's point of view, and the failure would land mid-call.

When adding a row, look the numbers up. `cache_min_tokens=None` means nobody
has looked it up yet, and every consumer must treat that as "cannot verify"
rather than "no minimum" — an unrun check is not a pass.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    """Everything about one provider that has to stay in lock step."""

    model: str
    """Pinned model id. A version, not a family alias — `claude-haiku-4-5`
    caches, prices and behaves differently from any other Claude model, and the
    numbers in this row describe *this* id only."""

    kwargs: dict
    """Provider-specific knobs for `model`. Empty is a real answer, not a
    placeholder: on Anthropic, no thinking is exactly what the 1-2s budget
    wants."""

    key_env: tuple[str, ...]
    """Environment variables that can supply this provider's credential. Any
    one is enough. Empty means the provider needs no key (local inference)."""

    key_url: str
    """Where a human goes to get that credential. Named here so the startup
    error can point at it instead of at whichever provider was hardcoded."""

    cache_breakpoint: bool
    """Whether the provider needs the reusable prefix marked explicitly with a
    `cache_control` block. True on Anthropic. Everything else here caches
    implicitly on a prefix match and rejects or ignores the marker."""

    cache_min_tokens: int | None
    """Prefix size below which this provider silently stops caching. `None`
    means unverified — treat it as "cannot check", never as zero."""

    note: str = ""


PROFILES: dict[str, Profile] = {
    "anthropic": Profile(
        model="claude-haiku-4-5",
        kwargs={},
        key_env=("ANTHROPIC_API_KEY",),
        key_url="https://console.anthropic.com/settings/keys",
        cache_breakpoint=True,
        # Verified 2026-08-24 against the prompt-caching docs. This is the
        # HIGHEST minimum of any current Claude model -- Opus 5 is 512 and
        # Sonnet 5 is 1024 -- so a pack sized for a different Claude will
        # silently never cache here. Re-look-up if `model` changes.
        cache_min_tokens=4096,
        note="Phase 3 primary. Native structured output; 200K context.",
    ),
    "google_genai": Profile(
        model="gemini-3.6-flash",
        # Measured 2026-08-23: default thinking 2.7s and 4.4s, "low" 15.6s,
        # "minimal" 1.7s and 1.3s. Only "minimal" fits the budget, which is why
        # this is a default here rather than advice in a comment. Note 3.x
        # rejects thinking_budget with a 400; that parameter was 2.5-era.
        kwargs={"thinking_level": "minimal"},
        key_env=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        key_url="https://aistudio.google.com/apikey",
        cache_breakpoint=False,
        cache_min_tokens=4096,  # gemini-3.x Flash; 2.5 Flash was 2048, closed to new users
        note="Phase 2 default. Free tier is 20 requests/day, per model, per project.",
    ),
    "deepseek": Profile(
        model="deepseek-v4-flash",
        kwargs={},
        key_env=("DEEPSEEK_API_KEY",),
        key_url="https://platform.deepseek.com/api_keys",
        cache_breakpoint=False,
        # UNVERIFIED. The pricing page bills cache hits and misses separately
        # but never states how a hit is produced or what the floor is. Left as
        # None on purpose: check_pack refuses rather than guessing, because a
        # guessed floor that is too low reads as a PASS on a pack that will
        # never cache. PHASE-3-PLAN.md open question 1.
        cache_min_tokens=None,
        note="Phase 3 fallback link. 1M context; caching assumed implicit, unconfirmed.",
    ),
    "ollama": Profile(
        model="llama3.2",
        kwargs={},
        key_env=(),
        key_url="https://ollama.com/download",
        cache_breakpoint=False,
        cache_min_tokens=0,  # local; no floor that costs money
        note="Local. Latency on this hardware is unmeasured and probably poor.",
    ),
    "fake": Profile(
        model="fake",
        kwargs={},
        # Deliberately still requires a real provider key. The fake path exists
        # so the mocked E2E suite can start b_server, and a keyless fake would
        # make it the one path where credential plumbing goes untested --
        # exactly the path that must be tested, because a deployment that
        # forgot the key should fail in CI rather than in a live conversation.
        key_env=(
            "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "DEEPSEEK_API_KEY",
            "OPENAI_API_KEY",
            "AWS_ACCESS_KEY_ID",
        ),
        key_url="(any real provider key; the fake never calls out)",
        cache_breakpoint=False,
        cache_min_tokens=0,
        note="Canned cards, no model called. Mocked E2E suite only.",
    ),
}


class ProfileError(SystemExit):
    """Refusal to start, with a reason and the thing that would change it."""


def resolve(env: dict[str, str]) -> tuple[str, Profile, list[str]]:
    """Pick a profile from the environment and apply constrained overrides.

    Returns `(provider, profile, warnings)`. Raises `ProfileError` rather than
    falling back to a default, because a wrong provider is not a degraded
    conversation -- it is no conversation, discovered live.

    The one override rule that matters: **kwargs may not be overridden without
    also pinning the model.** Kwargs are meaningful only against a specific
    model, so a loose `GLOSS_MODEL_KWARGS` in a shell profile or a `.env` is
    precisely the drift this module exists to stop -- it silently follows you
    onto whichever provider you switch to next.
    """
    import json

    provider = env.get("GLOSS_PROVIDER", "google_genai")
    if provider not in PROFILES:
        raise ProfileError(
            f"Unknown GLOSS_PROVIDER={provider!r}.\n"
            f"Known providers: {', '.join(sorted(PROFILES))}.\n"
            "Add a row to providers.py -- with its model id, kwargs, key "
            "variable and cache floor looked up, not estimated -- before using it."
        )

    profile = PROFILES[provider]
    warnings: list[str] = []

    model_override = env.get("GLOSS_MODEL")
    kwargs_override = env.get("GLOSS_MODEL_KWARGS")

    if kwargs_override and not model_override:
        raise ProfileError(
            f"GLOSS_MODEL_KWARGS is set but GLOSS_MODEL is not, on provider "
            f"{provider!r}.\n"
            f"Kwargs only mean anything against a specific model, so an "
            f"unpinned override follows you onto the next provider you switch "
            f"to and fails there. This is the exact drift providers.py exists "
            f"to prevent.\n"
            f"Either unset GLOSS_MODEL_KWARGS and take {provider}'s default "
            f"({profile.kwargs or 'no kwargs'}), or set GLOSS_MODEL too and own both."
        )

    model = model_override or profile.model
    if model_override and model_override != profile.model:
        warnings.append(
            f"GLOSS_MODEL={model_override} overrides {provider}'s pinned "
            f"{profile.model}. The cache floor "
            f"({profile.cache_min_tokens} tokens) and kwargs in providers.py "
            f"describe {profile.model}, not this one -- they are now unverified."
        )

    if kwargs_override:
        try:
            kwargs = json.loads(kwargs_override)
        except json.JSONDecodeError as exc:
            raise ProfileError(
                f"GLOSS_MODEL_KWARGS is not valid JSON: {exc}"
            ) from exc
    else:
        kwargs = dict(profile.kwargs)

    resolved = Profile(
        model=model,
        kwargs=kwargs,
        key_env=profile.key_env,
        key_url=profile.key_url,
        cache_breakpoint=profile.cache_breakpoint,
        cache_min_tokens=profile.cache_min_tokens,
        note=profile.note,
    )
    return provider, resolved, warnings


def missing_key(profile: Profile, env: dict[str, str]) -> str | None:
    """The message to refuse with when no credential is set, else None."""
    if not profile.key_env:
        return None
    if any(env.get(name) for name in profile.key_env):
        return None
    return (
        f"No credential set. Provide one of: {', '.join(profile.key_env)}.\n"
        f"Get one at {profile.key_url}"
    )
