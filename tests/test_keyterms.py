"""Keyterm extraction from a prep pack.

Priming Nova-3 is upstream of everything: a term the recogniser mishears is a
term no later stage can recover. The failure mode is quiet — a mangled term
produces a plausible transcript and simply never matches — so what these tests
mostly assert is what must NOT come out. Budget spent on `dataJson` is budget
not spent on the one word the conversation turns on.
"""

from __future__ import annotations

import keyterms
from keyterms import extract, fit, from_pack


def test_backticked_terms_are_found_first() -> None:
    terms = extract("Deploys go through `Kestrel` before release.")
    assert terms[0] == "Kestrel"


def test_code_fragments_are_not_vocabulary() -> None:
    """Nobody says "deque(maxlen=6" out loud, and each one costs budget a real
    term needed."""
    terms = extract(
        "See `b_server.py:154` and `deque(maxlen=6)` and `include_loopback=True` "
        "and `reunravel/docs/PLAN.md` and commit `e82d7cf`."
    )
    assert terms == []


def test_a_heading_that_is_a_sentence_is_not_a_term() -> None:
    assert "Something that went badly" not in extract("## Something that went badly\n")
    assert "Latency on a hot read path" not in extract("## Latency on a hot read path\n")


def test_a_heading_that_reads_as_a_name_survives() -> None:
    assert "Kestrel Migration" in extract("## Kestrel Migration\n\nNotes here.\n")


def test_the_first_word_of_a_heading_is_not_a_proper_noun() -> None:
    """A level-two heading opens with "## ". Allowing only one marker character
    let every heading's first word through as if it were a name."""
    for heading in ("# Who I'm talking to", "## Raising the floor", "### Something else"):
        assert not any(t in ("Who", "Raising", "Something") for t in extract(heading + "\n"))


def test_a_colon_does_not_hide_a_proper_noun() -> None:
    """English does not capitalise after a colon, so a capital there is
    evidence — and "Company: Example Corp" is the shape prep packs use."""
    assert "Example Corp" in extract("Company: Example Corp — mid-size, B2B.\n")


def test_a_multi_word_name_stays_one_term() -> None:
    assert "Deepgram Nova" in " ".join(extract("We run Deepgram Nova for streaming.\n"))


def test_leading_english_words_are_trimmed_not_rejected() -> None:
    """"The Bluetooth trap" is a sentence fragment wrapped around a real term."""
    terms = extract("Everyone hits The Bluetooth trap eventually.\n")
    assert any("Bluetooth" in t for t in terms)
    assert not any(t.startswith("The ") for t in terms)


def test_acronyms_are_kept_because_they_are_what_gets_mangled() -> None:
    terms = extract("Audio arrives as PCM over WASAPI, and the headset drops to HFP.\n")
    for acronym in ("PCM", "WASAPI", "HFP"):
        assert acronym in terms


def test_punctuation_shaped_acronyms_are_excluded() -> None:
    assert "TODO" not in extract("TODO: fix this\n")
    assert "OK" not in extract("That is OK by me.\n")


def test_terms_are_deduplicated_case_insensitively() -> None:
    terms = extract("`Kestrel` gates deploys. Kestrel is required. `kestrel` again.\n")
    assert len([t for t in terms if t.lower() == "kestrel"]) == 1


def test_an_empty_pack_yields_nothing_rather_than_failing() -> None:
    assert from_pack("") == ([], 0)
    assert from_pack("   \n\n  ") == ([], 0)


def test_the_budget_is_enforced_and_the_overflow_counted() -> None:
    """Deepgram errors above 500 tokens per request. A silently truncated
    vocabulary looks identical to a full one until the term that mattered is
    the one that was cut."""
    kept, dropped = fit([f"Term{i}" for i in range(500)], budget_tokens=30)
    assert 0 < len(kept) < 500
    assert dropped == 500 - len(kept)


def test_the_budget_default_sits_under_deepgrams_ceiling() -> None:
    assert keyterms.DEFAULT_BUDGET_TOKENS < 500


def test_rank_order_survives_the_budget_cut() -> None:
    """What gets cut has to be the least valuable, not an arbitrary tail."""
    terms = extract("`Kestrel` and `Kestrel` and `Griffon`. Also PCM.\n")
    kept, _ = fit(terms, budget_tokens=3)
    assert kept == terms[:1]


def test_a_real_pack_yields_real_vocabulary(tmp_path) -> None:
    pack = (
        "# Who I'm talking to\n\n"
        "Company: Example Corp — mid-size, B2B.\n"
        "They gate deploys through `Kestrel` and stream over WASAPI.\n\n"
        "## Something that went badly\n\n"
        "The Bluetooth trap cost us a week.\n"
    )
    kept, _ = from_pack(pack)
    assert "Kestrel" in kept
    assert "WASAPI" in kept
    assert "Example Corp" in kept
    assert "Something that went badly" not in kept
    assert "Who" not in kept
