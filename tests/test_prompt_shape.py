"""The prompt shape, which is load-bearing and fails silently when wrong.

Prefix caching matches the exact serialised request, block boundaries included.
Two shapes that looked equivalent were measured failing before the current one,
and both failed the same way: every lookup fell through to the system
breakpoint, so `cache_read` sat at exactly the prefix size and looked healthy.
Nothing raised. Nothing logged.

A test is the only thing that catches that before a live conversation does, so
these assert the *structure* rather than the text — that is what the cache key
covers. See SPEC.md § "The prompt shape is load-bearing".
"""

from __future__ import annotations

import pytest

import providers

ANTHROPIC = providers.PROFILES["anthropic"]
DEEPSEEK = providers.PROFILES["deepseek"]


@pytest.fixture
def server(build_server):
    return build_server(GLOSS_PROVIDER="anthropic", GLOSS_FALLBACKS="deepseek")


def texts(message) -> list[str]:
    return [block["text"] for block in message.content]


def test_empty_transcript_is_just_the_ask(server) -> None:
    assert server.build_turn_message(ANTHROPIC).content == server.ASK


def test_one_block_per_turn_and_the_ask_last(server) -> None:
    server.transcript.extend([("interviewer", "one"), ("user", "two"), ("interviewer", "three")])
    blocks = server.build_turn_message(ANTHROPIC).content
    assert len(blocks) == 4  # three turns plus the ASK
    assert texts(server.build_turn_message(ANTHROPIC)) == [
        "[interviewer] one", "[user] two", "[interviewer] three", server.ASK,
    ]


def test_breakpoints_ride_the_last_two_turns_only(server) -> None:
    """Anthropic allows four; the system prefix already holds one. Older
    breakpoints are dropped rather than accumulated."""
    for i in range(5):
        server.transcript.append(("interviewer", f"turn {i}"))
    blocks = server.build_turn_message(ANTHROPIC).content
    marked = [i for i, b in enumerate(blocks) if "cache_control" in b]
    assert marked == [3, 4]  # the last two turn blocks, of five


def test_the_ask_never_carries_a_breakpoint(server) -> None:
    """It is constant and sits after every breakpoint on purpose, so it never
    enters a cached prefix and never invalidates one."""
    server.transcript.extend([("interviewer", "a"), ("interviewer", "b")])
    blocks = server.build_turn_message(ANTHROPIC).content
    assert blocks[-1]["text"] == server.ASK
    assert "cache_control" not in blocks[-1]


def test_turn_n_is_a_byte_prefix_of_turn_n_plus_one(server) -> None:
    """The invariant the whole caching design rests on.

    This is the assertion that would have caught both shapes that failed:
    gluing the ASK to the newest turn, and re-joining history into one growing
    block. Each changes the bytes of an *earlier* block, so the prefix match
    dies at that point — with no error and a healthy-looking counter.
    """
    server.transcript.append(("interviewer", "first"))
    before = server.build_turn_message(ANTHROPIC).content[:-1]  # drop the ASK
    server.transcript.append(("user", "second"))
    after = server.build_turn_message(ANTHROPIC).content[:-1]

    assert len(after) == len(before) + 1
    for i, block in enumerate(before):
        # Ignore cache_control: it moves with the window by design. Text and
        # block boundaries are what the prefix match is made of.
        assert after[i]["text"] == block["text"], f"block {i} changed between turns"


def test_history_is_never_re_merged(server) -> None:
    """Turn N+1 must not fold turn N's blocks into one. The text would be
    nearly identical and the block structure would not, and the cache key
    covers structure."""
    for i in range(4):
        server.transcript.append(("interviewer", f"turn {i}"))
    blocks = server.build_turn_message(ANTHROPIC).content
    assert len(blocks) == 5
    assert all("\n[" not in b["text"] for b in blocks[:-1])


def test_deepseek_gets_a_plain_string_with_no_markers(server) -> None:
    """Everything except Anthropic caches implicitly and takes no marker."""
    server.transcript.extend([("interviewer", "one"), ("user", "two")])
    content = server.build_turn_message(DEEPSEEK).content
    assert isinstance(content, str)
    assert content.endswith(server.ASK)
    assert "cache_control" not in content


def test_the_two_shapes_carry_the_same_conversation(server) -> None:
    """A fallback must see the same turns as the primary, differently packed."""
    server.transcript.extend([("interviewer", "alpha"), ("user", "beta")])
    anthropic_text = " ".join(texts(server.build_turn_message(ANTHROPIC)))
    deepseek_text = server.build_turn_message(DEEPSEEK).content
    for turn in ("[interviewer] alpha", "[user] beta"):
        assert turn in anthropic_text
        assert turn in deepseek_text


def test_eviction_drops_the_oldest_and_keeps_order(server, monkeypatch) -> None:
    monkeypatch.setattr(server, "MAX_TRANSCRIPT_TOKENS", 10)
    for i in range(6):
        server.transcript.append(("interviewer", f"turn number {i} with some words"))
    server.evict_if_needed()
    kept = [text for _, text in server.transcript]
    assert kept == sorted(kept, key=lambda t: int(t.split()[2]))
    assert len(kept) < 6
    assert "turn number 5" in kept[-1]


def test_the_system_prompt_is_frozen_across_calls(server) -> None:
    """One timestamp in here and the cache is rewritten every turn, never read."""
    first = server.system_message_for(ANTHROPIC).content[0]["text"]
    second = server.system_message_for(ANTHROPIC).content[0]["text"]
    assert first == second == server.SYSTEM_PROMPT


def test_only_anthropic_gets_a_cache_control_block(server) -> None:
    assert isinstance(server.system_message_for(ANTHROPIC).content, list)
    assert "cache_control" in server.system_message_for(ANTHROPIC).content[0]
    assert isinstance(server.system_message_for(DEEPSEEK).content, str)
