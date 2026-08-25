"""The card contract. A card is the only thing gloss puts on screen, and it
arrives from the least trustworthy input in the system."""

from __future__ import annotations

import cards
from cards import Card, card_schema, valid_cards

GOOD = {"kind": "jargon", "label": "Kestrel", "detail": "Their deploy gate."}
WITH_ID = {"id": "kestrel", **GOOD}


def test_a_good_card_survives() -> None:
    kept, dropped = valid_cards([GOOD], 3)
    assert kept == [WITH_ID]
    assert dropped == 0


def test_the_empty_object_gemini_returns_is_dropped_not_fatal() -> None:
    """Asked for zero cards, Gemini 3.6 returns `[{}]`. Zero cards is the
    correct answer for most turns, so this must be an ordinary quiet turn and
    not "Enrichment is down" on the screen."""
    kept, dropped = valid_cards([{}], 3)
    assert kept == []
    assert dropped == 1


def test_one_bad_card_does_not_take_the_good_ones_with_it() -> None:
    kept, dropped = valid_cards([GOOD, {}, {"kind": "nope"}], 3)
    assert kept == [WITH_ID]
    assert dropped == 2


def test_a_model_cannot_emit_an_error_card() -> None:
    """`error` is the server speaking about itself. Letting a model emit one
    would let a bad turn impersonate an outage."""
    kept, dropped = valid_cards([{"kind": "error", "label": "x", "detail": "y"}], 3)
    assert kept == []
    assert dropped == 1


def test_empty_strings_are_not_cards() -> None:
    kept, dropped = valid_cards([{"kind": "jargon", "label": "", "detail": "y"}], 3)
    assert (kept, dropped) == ([], 1)


def test_extra_fields_are_refused_rather_than_ignored() -> None:
    kept, _ = valid_cards([{**GOOD, "url": "http://x"}], 3)
    assert kept == []


def test_non_list_input_is_survivable() -> None:
    assert valid_cards(None, 3) == ([], 0)
    assert valid_cards({"cards": []}, 3) == ([], 0)


def test_max_cards_is_enforced_on_the_way_out() -> None:
    kept, _ = valid_cards([GOOD] * 10, 3)
    assert len(kept) == 3


def test_schema_carries_the_tool_name_langchain_anthropic_requires() -> None:
    """Without it, langchain-anthropic raises "Unsupported function" at import,
    before any call is made."""
    assert card_schema(3)["title"] == "emit_cards"


def test_schema_has_no_refs_left_for_a_strict_dialect_to_choke_on() -> None:
    rendered = repr(card_schema(3))
    assert "$ref" not in rendered
    assert "$defs" not in rendered


def test_schema_max_items_follows_the_runtime_setting() -> None:
    assert card_schema(2)["properties"]["cards"]["maxItems"] == 2
    assert card_schema(5)["properties"]["cards"]["maxItems"] == 5


def test_the_schema_and_the_validator_are_the_same_contract() -> None:
    """The whole reason cards.py exists: two statements of one contract drift,
    and the one that drifts silently is the check."""
    schema_kinds = set(card_schema(3)["properties"]["cards"]["items"]["properties"]["kind"]["enum"])
    assert schema_kinds == set(cards.CardKind.__args__)
    assert Card.model_validate(GOOD).kind in schema_kinds


# --- card identity, which is what lets the display update instead of flash ---


def test_a_missing_id_is_derived_rather_than_dropping_the_card() -> None:
    """`required` is advice to a model, not a constraint on it. Losing a real
    card over a bookkeeping field would be the wrong trade."""
    kept, dropped = valid_cards([GOOD], 3)
    assert dropped == 0
    assert kept[0]["id"] == "kestrel"


def test_a_supplied_id_is_kept_verbatim() -> None:
    kept, _ = valid_cards([{"id": "deploy-gate", **GOOD}], 3)
    assert kept[0]["id"] == "deploy-gate"


def test_the_derived_id_matches_what_the_model_would_supply() -> None:
    """The fallback has to agree with the model's own convention, or a turn
    where it omits the id would break continuity with turns where it does not."""
    without = valid_cards([GOOD], 3)[0][0]["id"]
    with_it = valid_cards([WITH_ID], 3)[0][0]["id"]
    assert without == with_it


def test_two_cards_cannot_claim_the_same_slot() -> None:
    """Same id in one batch would leave two cards fighting over one position."""
    kept, _ = valid_cards([GOOD, dict(GOOD)], 3)
    assert len({c["id"] for c in kept}) == len(kept)


def test_an_id_is_never_empty() -> None:
    """The display keys a Map on it; an empty key would collapse unrelated
    cards onto each other."""
    kept, _ = valid_cards([{"kind": "jargon", "label": "!!!", "detail": "x"}], 3)
    assert kept[0]["id"]


def test_slug_is_stable_and_bounded() -> None:
    assert cards.slug("Contract Testing!") == "contract-testing"
    assert cards.slug("  Kestrel  ") == "kestrel"
    assert len(cards.slug("word " * 40)) <= 40
