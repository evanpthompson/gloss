"""The prep-pack index: what it answers, and — mostly — what it refuses to.

Like the glossary, this link runs when every vendor is out, so there is nothing
above it to disagree with a bad answer. Unlike the glossary, it matches on
overlapping words rather than exact phrases, which is a looser instrument — so
most of what is worth testing here is the set of turns it declines to answer.

The other invariant under test is that it only ever **quotes**. A recall card
asserts that the person wrote something down; a generated one would be a lie
told with their own voice, on a screen they are trusting mid-conversation.
"""

from __future__ import annotations

from recall import Notes

PACK = """\
These are the notes I scribbled before the call.

# Who I'm talking to

Company: Northwind Logistics, a freight brokerage.

# What they seem to care about from the posting

- Service-to-service integration at scale
- Latency work on read paths

# Latency on a hot read path

Situation: a customer-facing lookup sat at six seconds and was the top
complaint. What I did: traced it to an N+1 fan-out and added a coalescing
layer. Result: six seconds down to one and a half.

# Integration platform

Situation: partner integrations were hand-rolled and every one broke
differently. Result: onboarding went from weeks to days.

# Questions I want to ask them

- What does the on-call rotation actually look like week to week?
- Which decision in the last year would you make differently?

# Something that went badly

A migration I sequenced wrong: I moved reads before the write path was fully dual-writing, so a subset of records read stale for about forty minutes and I had to roll back.
"""

NOTES = Notes.from_pack(PACK)
FLAT = " ".join(PACK.split())


def test_a_restated_question_still_finds_the_note() -> None:
    """The reason this is not phrase matching. Nobody asks a question in the
    words you filed it under, so an exact matcher would stay silent on exactly
    the turn this exists for."""
    card = NOTES.card("So tell me about the latency work on your read path.")
    assert card["label"] == "Latency on a hot read path"


def test_an_unrelated_turn_produces_nothing() -> None:
    assert NOTES.card("Do you follow baseball at all?") is None


def test_filler_alone_produces_nothing() -> None:
    """"Tell me about a time" opens half the questions in an interview and
    says nothing about which note answers it."""
    assert NOTES.card("So, tell me about a time when, you know, basically.") is None


def test_one_shared_word_is_not_enough() -> None:
    """"Situation:" opens three of these notes. A word that could have come
    from any of them is evidence about none of them."""
    assert NOTES.card("What is the situation?") is None


def test_one_name_unique_to_a_section_is_enough() -> None:
    """The opposite case, and the strongest signal available: a name that
    appears in exactly one note can only have come from that note."""
    card = NOTES.card("How much do you know about Northwind?")
    assert card["label"] == "Who I'm talking to"


def test_an_ordinary_word_that_happens_to_appear_once_is_not_a_name() -> None:
    """Measured, not hypothetical. Before names were required, three of fifteen
    off-topic turns produced a card: "hand" out of "hand-rolled", "second" out
    of "a second path", "next" out of "next time" — each unique to one note, so
    each alone was enough. "Hand over to my colleague" then answered itself
    with a rehearsed anecdote about partner integrations.

    Rarity inside a pack is not rarity in English. Names are.
    """
    assert NOTES.card("So I will hand over to my colleague now.") is None


def test_the_default_constructor_takes_the_stricter_rule() -> None:
    """`Notes(sections)` without names switches the single-word rule off rather
    than guessing which words are names — the unrecognised case is refused."""
    from recall import Notes as Raw
    from recall import sections
    assert Raw(sections(PACK)).card("How much do you know about Northwind?") is None


def test_a_tie_produces_nothing_rather_than_a_coin_flip() -> None:
    """Two notes matching equally well means the turn does not distinguish
    them, and a wrong story on screen looks exactly as confident as a right
    one."""
    twins = Notes.from_pack(
        "# Kestrel rollout\n\nThe gate ran on Tuesday.\n\n"
        "# Kestrel rehearsal\n\nThe gate ran on Tuesday.\n\n"
        "# Sailing\n\nDinghies capsize.\n\n"
        "# Pottery\n\nKilns crack.\n"
    )
    assert twins.card("How did the Kestrel gate go?") is None


def test_a_word_common_across_the_pack_does_not_index() -> None:
    """The stoplist is derived from the pack, not maintained by hand — a word
    in more than half the notes cannot single one of them out."""
    both = Notes.from_pack("# Alpha\n\nThe widget bolts on.\n\n# Beta\n\nThe widget bolts on.\n")
    assert both.card("Tell me how the widget bolts on.") is None


def test_the_detail_is_quoted_from_the_pack_verbatim() -> None:
    """The invariant the whole module exists to hold. `detail` is a span copied
    out of the notes — never a summary, because summarising is generating."""
    card = NOTES.card("What would you ask us about the on-call rotation?")
    assert card["detail"].rstrip("…") in FLAT


def test_a_hard_wrapped_note_is_quoted_as_a_sentence_not_a_line() -> None:
    """Prep packs are wrapped at 80 columns, so a raw line is usually half a
    thought and a terrible card."""
    card = NOTES.card("So tell me about the latency work on your read path.")
    assert card["detail"] == (
        "Situation: a customer-facing lookup sat at six seconds and was the top complaint."
    )


def test_a_long_note_is_cut_rather_than_summarised() -> None:
    card = NOTES.card("Tell me about something that went badly.")
    assert card["detail"].endswith("…")
    assert len(card["detail"].split()) == 25  # the ellipsis rides the 25th word


def test_a_long_heading_is_trimmed_to_a_readable_label() -> None:
    card = NOTES.card("What do you think we care about, from the posting?")
    assert card["label"] == "What they seem to care about"


def test_the_id_is_stable_across_turns_about_the_same_note() -> None:
    """Card identity is what lets the display update in place instead of
    stacking a second copy every time the conversation returns to a story."""
    first = NOTES.card("So tell me about the latency work on your read path.")
    second = NOTES.card("You mentioned a hot read path — what was the fix?")
    assert first["id"] == second["id"] == "latency-on-a-hot-read-path"


def test_it_can_only_ever_produce_a_recall_card() -> None:
    """The mirror of kb.py's rule. A book cannot source a recall card; the
    person's own notes cannot source anything else."""
    assert NOTES.card("So tell me about the latency work on your read path.")["kind"] == "recall"


def test_text_before_the_first_heading_is_not_indexed() -> None:
    """A card needs a name and an unnamed preamble has none to give it."""
    assert len(NOTES) == 6
    assert NOTES.card("What did you scribble down before this?") is None


def test_a_pack_with_no_headings_indexes_to_nothing_and_does_not_crash() -> None:
    empty = Notes.from_pack("just some prose, nothing structured about it at all\n")
    assert len(empty) == 0
    assert empty.card("anything at all") is None


def test_an_empty_pack_is_benign() -> None:
    assert len(Notes.from_pack("")) == 0
    assert Notes.from_pack("").card("anything") is None
