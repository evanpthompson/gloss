"""The local glossary: what it answers, and — mostly — what it refuses to.

This is the link that runs when no model is available to sanity-check it, so
its failure mode matters more than its hit rate. A wrong card here is worse
than no card: it appears on a screen someone is trusting mid-conversation, with
nothing above it to disagree.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from kb import Entry, Glossary, normalise

TRAFFIC = Entry(term="east-west traffic",
                definition="Service-to-service communication within a group of applications.")
SHORT = Entry(term="traffic", definition="Network exchanges.")


def test_a_known_term_produces_a_card() -> None:
    card = Glossary([TRAFFIC]).card("Most of our east west traffic goes through the mesh.")
    assert card["label"] == "east-west traffic"
    assert card["detail"].startswith("Service-to-service")


def test_an_unknown_term_produces_nothing() -> None:
    """None is the common and correct answer. Guessing would be worse than
    staying quiet."""
    assert Glossary([TRAFFIC]).card("Tell me about your background.") is None


def test_the_longest_matching_term_wins() -> None:
    """"east-west traffic" is the answer to a question about east-west traffic;
    "traffic" is a less useful answer to the same question."""
    card = Glossary([SHORT, TRAFFIC]).card("how does east west traffic work")
    assert card["label"] == "east-west traffic"


def test_matching_is_on_whole_words_not_substrings() -> None:
    """"API" inside "rapid" is not a mention of an API."""
    g = Glossary([Entry(term="API", definition="An interface.")])
    assert g.card("we made rapid progress on the therapist portal") is None
    assert g.card("the API contract changed") is not None


def test_punctuation_and_case_do_not_prevent_a_match() -> None:
    """The text comes from a speech recogniser, which is inconsistent about
    both."""
    g = Glossary([Entry(term="Conway's Law", definition="Systems mirror the org.")])
    assert g.card("have you heard of conways law") is not None
    assert g.card("CONWAY'S LAW, basically") is not None


def test_it_can_only_ever_produce_a_jargon_card() -> None:
    """A recall card claims the person's own notes say something. A book is not
    their notes, and the system prompt forbids inventing what is not written
    there."""
    assert Glossary([TRAFFIC]).card("east west traffic")["kind"] == "jargon"


def test_the_card_id_is_stable_for_the_same_term() -> None:
    """Identity is what lets the display update rather than flash."""
    g = Glossary([TRAFFIC])
    first = g.card("east west traffic here")
    second = g.card("back to east-west traffic")
    assert first["id"] == second["id"] == "east-west-traffic"


def test_the_fuller_definition_wins_a_collision() -> None:
    """Two books defining one term is common; the fuller one is likelier to
    stand alone in 25 words without its chapter."""
    g = Glossary([
        Entry(term="idempotent", definition="Safe to repeat."),
        Entry(term="Idempotent", definition="An operation that can be applied many times without changing the result."),
    ])
    assert "many times" in g.card("is that idempotent")["detail"]


def test_normalise_collapses_what_speech_varies() -> None:
    assert normalise("East-West Traffic") == normalise("east west traffic")
    # Deleted, not split on: a recogniser writes "conways law".
    assert normalise("Conway's Law") == "conways law"
    assert normalise("Conway\u2019s Law") == "conways law"


# --- loading ---------------------------------------------------------------


def test_a_missing_file_is_an_empty_glossary_not_an_error(tmp_path) -> None:
    """The knowledge base is optional. A deployment without one should run with
    the chain it has rather than refuse to start."""
    g = Glossary.load(tmp_path / "nothing.json")
    assert len(g) == 0
    assert g.card("anything") is None


def test_a_malformed_file_raises_rather_than_degrading(tmp_path) -> None:
    """The file is model output written by a build script, so it is exactly as
    trustworthy as model output. A bad build must be found at startup."""
    path = tmp_path / "glossary.json"
    path.write_text(json.dumps({"entries": [{"term": "x"}]}))   # no definition
    with pytest.raises(ValidationError):
        Glossary.load(path)


def test_a_built_file_round_trips(tmp_path) -> None:
    path = tmp_path / "glossary.json"
    path.write_text(json.dumps({"entries": [
        {"term": "HATEOAS", "definition": "Responses carry the actions possible next.",
         "source": "Mastering API Architecture"},
    ]}))
    g = Glossary.load(path)
    assert len(g) == 1
    assert g.card("do you use HATEOAS")["label"] == "HATEOAS"


def test_an_empty_term_cannot_swallow_every_turn(tmp_path) -> None:
    """A blank key would match every string and answer every question."""
    g = Glossary([Entry(term="  ", definition="d")])
    assert g.card("literally anything") is None
