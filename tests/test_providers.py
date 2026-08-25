"""The provider allow-list and its refusals.

Every assertion here is about a refusal, because that is the half that has to
work: a provider row that comes apart produces a 400 from a vendor mid-call,
which is the expensive place to find out.
"""

from __future__ import annotations

import pytest

from providers import PROFILES, ProfileError, estimate_tokens, missing_key, resolve


def test_default_is_anthropic() -> None:
    name, profile, warnings = resolve({})
    assert name == "anthropic"
    assert profile.model == "claude-haiku-4-5"
    assert warnings == []


def test_an_unlisted_provider_is_refused_not_attempted() -> None:
    """"Unrecognised" and "unsupported" are the same thing from a live
    conversation's point of view."""
    with pytest.raises(ProfileError) as caught:
        resolve({"GLOSS_PROVIDER": "openai"})
    assert "Unknown GLOSS_PROVIDER" in str(caught.value)
    assert "anthropic" in str(caught.value)  # names what IS allowed


def test_kwargs_without_a_pinned_model_is_refused() -> None:
    """The exact drift providers.py exists to stop: an unpinned kwargs override
    follows you onto the next provider and fails there."""
    with pytest.raises(ProfileError) as caught:
        resolve({"GLOSS_PROVIDER": "anthropic", "GLOSS_MODEL_KWARGS": '{"thinking_level": "low"}'})
    assert "GLOSS_MODEL" in str(caught.value)


def test_kwargs_with_a_pinned_model_is_allowed() -> None:
    _, profile, _ = resolve({
        "GLOSS_PROVIDER": "anthropic",
        "GLOSS_MODEL": "claude-sonnet-5",
        "GLOSS_MODEL_KWARGS": '{"temperature": 0.1}',
    })
    assert profile.model == "claude-sonnet-5"
    assert profile.kwargs == {"temperature": 0.1}


def test_malformed_kwargs_json_is_refused() -> None:
    with pytest.raises(ProfileError):
        resolve({"GLOSS_PROVIDER": "anthropic", "GLOSS_MODEL": "x", "GLOSS_MODEL_KWARGS": "{oops"})


def test_overriding_the_model_warns_that_the_row_no_longer_describes_it() -> None:
    _, _, warnings = resolve({"GLOSS_PROVIDER": "anthropic", "GLOSS_MODEL": "claude-opus-5"})
    assert warnings and "cache floor" in warnings[0]


def test_missing_key_names_the_variable_and_where_to_get_one() -> None:
    message = missing_key(PROFILES["deepseek"], {})
    assert message and "DEEPSEEK_API_KEY" in message
    assert "platform.deepseek.com" in message


def test_a_present_key_satisfies_the_check() -> None:
    assert missing_key(PROFILES["deepseek"], {"DEEPSEEK_API_KEY": "x"}) is None


def test_an_empty_key_does_not_satisfy_it() -> None:
    assert missing_key(PROFILES["deepseek"], {"DEEPSEEK_API_KEY": ""}) is not None


def test_the_fake_provider_still_requires_a_credential() -> None:
    """The mocked path must not be the one place credential plumbing goes
    untested — a deployment that forgot the key should fail in CI."""
    assert PROFILES["fake"].key_env
    assert missing_key(PROFILES["fake"], {}) is not None


def test_ollama_needs_no_key_because_it_is_local() -> None:
    assert missing_key(PROFILES["ollama"], {}) is None


def test_every_row_is_internally_complete() -> None:
    for name, profile in PROFILES.items():
        assert profile.model, name
        assert profile.key_url, name
        assert isinstance(profile.cache_breakpoint, bool), name


def test_deepseeks_cache_floor_was_measured_not_left_unverified() -> None:
    """`None` means nobody has looked it up, and check_pack refuses on it.
    Measured 2026-08-25 at 128 tokens."""
    assert PROFILES["deepseek"].cache_min_tokens == 128


def test_only_anthropic_wants_an_explicit_cache_breakpoint() -> None:
    assert PROFILES["anthropic"].cache_breakpoint
    assert not any(p.cache_breakpoint for n, p in PROFILES.items() if n != "anthropic")


def test_deepseek_disables_thinking_or_structured_output_400s() -> None:
    """`deepseek-v4-flash` thinks by default and thinking mode rejects the
    forced tool_choice that structured output depends on."""
    assert PROFILES["deepseek"].kwargs["extra_body"]["thinking"]["type"] == "disabled"


def test_token_estimate_is_conservative() -> None:
    """It under-counts on purpose, so clearing a floor by this measure leaves
    real margin rather than sitting exactly on the line."""
    assert estimate_tokens("word " * 100) < 100 * 1.4
    assert estimate_tokens("") == 0
