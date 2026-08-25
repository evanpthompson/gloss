"""The transcript ceiling, and what crossing it costs.

The transcript is unbounded by design — a glossary that cannot connect a thing
said now to the thing that defined it earlier is not doing its job — but
"unbounded" is really "bounded far past any real conversation". These cover the
part past that bound, which nothing exercised before: roughly 400 turns of
speech at Haiku's window, so it had never happened and would first happen live.

Eviction is not free and not silent. It loses conversation the tool exists to
remember, **and** it changes the front of the prompt, which invalidates every
cached read after that point. The last test here asserts that consequence
directly rather than leaving it as a comment.
"""

from __future__ import annotations

import logging

import pytest

import providers

ANTHROPIC = providers.PROFILES["anthropic"]


@pytest.fixture
def server(build_server):
    return build_server(GLOSS_PROVIDER="anthropic", GLOSS_FALLBACKS="deepseek")


def turn(n: int, words: int = 20) -> tuple[str, str]:
    """A turn with a findable marker and a predictable size."""
    return ("interviewer", f"turn-{n} " + f"word{n} " * words)


def test_nothing_is_dropped_under_the_ceiling(server, monkeypatch) -> None:
    monkeypatch.setattr(server, "MAX_TRANSCRIPT_TOKENS", 100_000)
    server.transcript.extend(turn(i) for i in range(20))
    server.evict_if_needed()
    assert len(server.transcript) == 20


def test_the_oldest_turns_go_first(server, monkeypatch) -> None:
    """The newest turn is the one cards are about; the oldest is the most
    disposable."""
    monkeypatch.setattr(server, "MAX_TRANSCRIPT_TOKENS", 60)
    server.transcript.extend(turn(i) for i in range(12))
    server.evict_if_needed()

    kept = [t for _, t in server.transcript]
    assert len(kept) < 12
    assert kept[-1].startswith("turn-11"), "the newest turn was not kept"
    numbers = [int(t.split()[0].removeprefix("turn-")) for t in kept]
    assert numbers == sorted(numbers), "order was not preserved"
    assert numbers == list(range(numbers[0], 12)), "a hole was punched in the middle"


def test_eviction_brings_the_transcript_under_the_ceiling(server, monkeypatch) -> None:
    monkeypatch.setattr(server, "MAX_TRANSCRIPT_TOKENS", 80)
    server.transcript.extend(turn(i) for i in range(30))
    server.evict_if_needed()
    size = providers.estimate_tokens("\n".join(t for _, t in server.transcript))
    assert size <= 80


def test_every_drop_is_reported(server, monkeypatch, caplog) -> None:
    """Dropping the front of the conversation also invalidates every cache read
    after it, so the cost profile changes for a reason nothing else would show."""
    monkeypatch.setattr(server, "MAX_TRANSCRIPT_TOKENS", 60)
    server.transcript.extend(turn(i) for i in range(12))
    before = len(server.transcript)
    with caplog.at_level(logging.WARNING):
        server.evict_if_needed()
    dropped = before - len(server.transcript)
    messages = [r.getMessage() for r in caplog.records]
    assert sum("dropped oldest turn" in m for m in messages) == dropped
    assert any("cache prefix invalidated" in m for m in messages)


# --- the case that emptied the transcript ----------------------------------


def test_the_newest_turn_is_never_evicted(server, monkeypatch) -> None:
    """A single turn larger than the whole ceiling used to evict itself. The
    model was then asked for "cards for the final [interviewer] turn" with no
    turn in the prompt: a call that costs money and can only return nothing."""
    monkeypatch.setattr(server, "MAX_TRANSCRIPT_TOKENS", 10)
    server.transcript.append(("interviewer", "a very long question " * 200))
    server.evict_if_needed()
    assert len(server.transcript) == 1, "the only turn was evicted"


def test_an_oversized_turn_survives_a_full_transcript(server, monkeypatch) -> None:
    monkeypatch.setattr(server, "MAX_TRANSCRIPT_TOKENS", 40)
    server.transcript.extend(turn(i) for i in range(8))
    server.transcript.append(("interviewer", "enormous " * 400))
    server.evict_if_needed()
    assert len(server.transcript) == 1
    assert server.transcript[0][1].startswith("enormous")


def test_an_oversized_turn_says_so(server, monkeypatch, caplog) -> None:
    """Degrading by carrying fewer turns is correct; carrying none is not. The
    operator needs to know the ceiling is not doing what they set it to do."""
    monkeypatch.setattr(server, "MAX_TRANSCRIPT_TOKENS", 10)
    server.transcript.append(("interviewer", "a very long question " * 200))
    with caplog.at_level(logging.WARNING):
        server.evict_if_needed()
    assert any("over the 10-token ceiling on its own" in r.getMessage() for r in caplog.records)


def test_an_empty_transcript_is_not_a_special_case(server, monkeypatch) -> None:
    monkeypatch.setattr(server, "MAX_TRANSCRIPT_TOKENS", 10)
    server.evict_if_needed()
    assert server.transcript == []


# --- what eviction costs ----------------------------------------------------


def test_the_prompt_still_ends_with_the_ask_after_eviction(server, monkeypatch) -> None:
    """build_turn_message runs eviction itself, so the shape has to survive it."""
    monkeypatch.setattr(server, "MAX_TRANSCRIPT_TOKENS", 60)
    server.transcript.extend(turn(i) for i in range(12))
    blocks = server.build_turn_message(ANTHROPIC).content
    assert blocks[-1]["text"] == server.ASK
    assert "cache_control" not in blocks[-1]
    assert len(blocks) == len(server.transcript) + 1


def test_eviction_breaks_prefix_continuity_and_that_is_the_cost(server, monkeypatch) -> None:
    """The invariant caching depends on is that turn N's blocks are a prefix of
    turn N+1's. Eviction removes blocks from the *front*, so it deliberately
    breaks that — every cached read after the dropped turn is lost.

    Asserted rather than commented, because it is the whole reason the drop is
    logged at warning level.
    """
    monkeypatch.setattr(server, "MAX_TRANSCRIPT_TOKENS", 100_000)
    server.transcript.extend(turn(i) for i in range(10))
    before = [b["text"] for b in server.build_turn_message(ANTHROPIC).content[:-1]]

    monkeypatch.setattr(server, "MAX_TRANSCRIPT_TOKENS", 60)
    server.transcript.append(turn(10))
    after = [b["text"] for b in server.build_turn_message(ANTHROPIC).content[:-1]]

    assert len(after) < len(before), "nothing was evicted; the test proves nothing"
    assert after[0] != before[0], "the front of the prompt did not move"
    assert not before[: len(after)] == after, "prefix survived eviction, unexpectedly"
