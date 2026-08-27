"""The fallback chain, end to end through the real SDKs.

These run b_server against `tests/fake_vendor.FakeVendor` — a local HTTP server
speaking both wire formats — rather than against a mocked `ainvoke`. The layer
being tested *is* the SDK boundary: how a status code becomes an exception, how
that exception is classified, and what the chain does next. Mocking the model
would skip exactly the part that has been wrong.

No keys, no network, no spend, so this gates in CI.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.fake_vendor import FakeVendor

CARD = {"kind": "jargon", "label": "Kestrel", "detail": "Their deploy gate."}
TURN = ("interviewer", "We gate every deploy through Kestrel. How do you feel about that?")


@pytest.fixture
def chained(build_server, vendor: FakeVendor):
    """A two-link chain pointed at the fake vendor, plus captured broadcasts."""
    base = vendor.start()
    server = build_server(
        GLOSS_PROVIDER="anthropic",
        GLOSS_FALLBACKS="deepseek",
        ANTHROPIC_BASE_URL=base,
        ANTHROPIC_API_URL=base,
        DEEPSEEK_API_BASE=base,
    )
    sent: list[dict] = []

    async def capture(payload: dict) -> None:
        sent.append(payload)

    server.broadcast = capture
    server.sent = sent
    return server


def only_card(server) -> dict:
    assert server.sent, "nothing reached the display"
    return server.sent[-1]["cards"][0]


def card_of_kind(server, kind: str) -> dict:
    """The one card of a given kind in the last batch.

    The local link answers with both kinds when both match, so a positional
    index no longer identifies a card. Asserting on the kind you meant is also
    what makes an ordering change show up as a readable failure.
    """
    assert server.sent, "nothing reached the display"
    matching = [c for c in server.sent[-1]["cards"] if c["kind"] == kind]
    assert len(matching) == 1, f"expected one {kind} card, got {server.sent[-1]['cards']}"
    return matching[0]


# --- the ordinary case -----------------------------------------------------

async def test_primary_answers_and_the_fallback_is_never_called(chained, vendor) -> None:
    vendor.cards(CARD)
    chained.transcript.append(TURN)
    await chained.enrich()
    assert only_card(chained)["label"] == "Kestrel"
    assert vendor.calls_to("deepseek") == 0


async def test_zero_cards_is_a_quiet_turn_not_a_failure(chained, vendor) -> None:
    """The most common outcome. It must broadcast nothing at all — an error
    card here would put "Enrichment is down" on screen during small talk."""
    vendor.cards()
    chained.transcript.append(TURN)
    await chained.enrich()
    assert chained.sent == []


# --- failing over ----------------------------------------------------------

async def test_a_revoked_key_falls_through_and_a_card_still_lands(chained, vendor) -> None:
    vendor.fail(401, "authentication_error", "invalid x-api-key").cards(CARD)
    chained.transcript.append(TURN)
    await chained.enrich()
    assert only_card(chained)["label"] == "Kestrel"
    assert vendor.calls_to("deepseek") == 1


async def test_the_answering_provider_is_reported(chained, vendor) -> None:
    """`with_fallbacks()` does not say which link won. A chain that has quietly
    degraded for a whole conversation is otherwise invisible."""
    vendor.fail(529, "overloaded_error", "overloaded").cards(CARD)
    chained.transcript.append(TURN)
    result = await chained.chain.ainvoke(None)
    assert result["provider"] == "deepseek"


async def test_an_overloaded_vendor_falls_through(chained, vendor) -> None:
    vendor.fail(529, "overloaded_error", "overloaded").cards(CARD)
    chained.transcript.append(TURN)
    await chained.enrich()
    assert only_card(chained)["kind"] == "jargon"


# --- refusing to fail over -------------------------------------------------

async def test_our_own_bad_request_is_not_hidden_by_the_fallback(chained, vendor) -> None:
    """A malformed request is a bug in this code. A second vendor quietly
    succeeding would bury it behind a card that looks fine."""
    vendor.fail(400, "invalid_request_error", "messages.0.content: unexpected field")
    chained.transcript.append(TURN)
    await chained.enrich()
    assert only_card(chained)["kind"] == "error"
    assert vendor.calls_to("deepseek") == 0


async def test_a_spend_limit_400_does_fall_over(chained, vendor) -> None:
    """Anthropic returns a spend limit as a 400 with the same error type as a
    malformed request. Only the message separates them."""
    vendor.fail(400, "invalid_request_error",
                "Your credit balance is too low to access the API").cards(CARD)
    chained.transcript.append(TURN)
    await chained.enrich()
    assert only_card(chained)["label"] == "Kestrel"
    assert vendor.calls_to("deepseek") == 1


# --- knowing why, and acting on it -----------------------------------------

async def test_a_dead_balance_retires_the_link_for_the_session(chained, vendor) -> None:
    """Money does not come back mid-conversation. Retrying the primary every
    turn cannot succeed and costs a round trip on the one path with none to
    spare."""
    vendor.fail(402, "billing_error", "out of credit").cards(CARD, times=3)
    chained.transcript.append(TURN)
    await chained.enrich()
    assert vendor.calls_to("anthropic") == 1

    for _ in range(2):
        chained.transcript.append(TURN)
        await chained.enrich()
    assert vendor.calls_to("anthropic") == 1, "primary was called again after being retired"
    assert vendor.calls_to("deepseek") == 3


async def test_a_rate_limit_does_not_retire_the_link(chained, vendor) -> None:
    """It clears in seconds. Demoting the primary for an hour over a blip would
    be worse than the blip."""
    vendor.fail(429, "rate_limit_error", "slow down",
                headers={"retry-after": "1"}).cards(CARD).cards(CARD)
    chained.transcript.append(TURN)
    await chained.enrich()
    assert vendor.calls_to("anthropic") == 1

    chained.transcript.append(TURN)
    await chained.enrich()
    assert vendor.calls_to("anthropic") == 2, "primary was retired over a transient failure"


async def test_the_error_card_says_what_is_wrong_not_which_class_threw(chained, vendor) -> None:
    """The person reading a second screen mid-call can act on "out of credit".
    They cannot act on "APIStatusError"."""
    vendor.fail(402, "billing_error", "out of credit", times=2)
    chained.transcript.append(TURN)
    await chained.enrich()
    assert only_card(chained)["label"] == "Provider out of credit"


async def test_when_the_whole_chain_is_out_the_screen_says_so(chained, vendor) -> None:
    """Zero cards is the normal state, so a silent failure would be invisible
    for the rest of the call."""
    vendor.fail(503, "server_overloaded", "overloaded", times=2)
    chained.transcript.append(TURN)
    await chained.enrich()
    card = only_card(chained)
    assert card["kind"] == "error"
    assert card["label"] == "Model provider overloaded"


# --- startup gates ---------------------------------------------------------

def test_a_fallback_without_a_credential_refuses_the_whole_boot(build_server) -> None:
    """A fallback you have not proved is one you discover during the outage it
    exists for."""
    with pytest.raises(SystemExit) as caught:
        build_server(GLOSS_PROVIDER="anthropic", GLOSS_FALLBACKS="deepseek", DEEPSEEK_API_KEY="")
    assert "DEEPSEEK_API_KEY" in str(caught.value)
    assert "GLOSS_FALLBACKS=" in str(caught.value)  # names the way out


def test_an_unknown_fallback_name_refuses_the_boot(build_server) -> None:
    with pytest.raises(SystemExit) as caught:
        build_server(GLOSS_PROVIDER="anthropic", GLOSS_FALLBACKS="grok")
    assert "grok" in str(caught.value)


def test_an_empty_fallback_list_runs_single_provider(build_server) -> None:
    server = build_server(GLOSS_PROVIDER="anthropic", GLOSS_FALLBACKS="", DEEPSEEK_API_KEY="")
    assert [name for name, _, _ in server.LINKS] == ["anthropic"]


def test_the_fake_provider_never_builds_a_live_chain(build_server) -> None:
    """CI's mocked path must not reach the internet — it is the one path built
    never to."""
    server = build_server(GLOSS_PROVIDER="fake", GLOSS_FALLBACKS="deepseek")
    assert [name for name, _, _ in server.LINKS] == ["fake"]


async def test_the_fake_provider_still_produces_a_card(build_server) -> None:
    server = build_server(GLOSS_PROVIDER="fake")
    sent: list[dict] = []

    async def capture(payload: dict) -> None:
        sent.append(payload)

    server.broadcast = capture
    server.transcript.append(TURN)
    await server.enrich()
    assert sent[-1]["cards"][0]["label"] == "fake provider"


# --- the preflight ---------------------------------------------------------

async def test_the_preflight_is_a_live_call_per_link(chained, vendor) -> None:
    """Not a key-shape check. It is what proves a credential is funded and
    unrevoked before the conversation starts — and it is what licenses treating
    a later 401 as an outage rather than a typo."""
    for _, _, link in chained.LINKS:
        await link.ainvoke(None)
    assert vendor.calls_to("anthropic") == 1
    assert vendor.calls_to("deepseek") == 1


def test_a_dead_link_refuses_the_boot_rather_than_starting_degraded(chained, vendor) -> None:
    vendor.fail(401, "authentication_error", "invalid key", times=4)
    with pytest.raises(SystemExit) as caught:
        asyncio.run(chained.main())
    assert "Cannot reach" in str(caught.value)


# --- the local glossary as the chain's floor --------------------------------

GLOSSARY = {"entries": [
    {"term": "Kestrel", "definition": "A deploy gate that changes must clear before shipping.",
     "source": "test"},
]}


@pytest.fixture
def with_glossary(build_server, vendor: FakeVendor, tmp_path):
    """A three-link chain whose last link is the local glossary."""
    base = vendor.start()
    path = tmp_path / "glossary.json"
    path.write_text(json.dumps(GLOSSARY))
    server = build_server(
        GLOSS_PROVIDER="anthropic",
        GLOSS_FALLBACKS="deepseek,local",
        GLOSS_KB=str(path),
        ANTHROPIC_BASE_URL=base,
        ANTHROPIC_API_URL=base,
        DEEPSEEK_API_BASE=base,
    )
    sent: list[dict] = []

    async def capture(payload: dict) -> None:
        sent.append(payload)

    server.broadcast = capture
    server.sent = sent
    return server


def test_the_glossary_joins_the_chain_as_its_last_link(with_glossary) -> None:
    assert [name for name, _, _ in with_glossary.LINKS] == ["anthropic", "deepseek", "local"]


async def test_the_glossary_answers_when_every_vendor_is_out(with_glossary, vendor) -> None:
    """The floor under the chain: no network, no tokens, still a card."""
    vendor.fail(503, "server_overloaded", "overloaded", times=4)
    with_glossary.transcript.append(TURN)
    await with_glossary.enrich()
    assert card_of_kind(with_glossary, "jargon")["label"] == "Kestrel"


async def test_the_notes_answer_when_every_vendor_is_out(with_glossary, vendor) -> None:
    """The half that was missing. A total outage used to leave the person's own
    notes — the highest-value words in the system, and already in memory —
    unreachable, while a definition mined from a book still got through."""
    vendor.fail(503, "server_overloaded", "overloaded", times=4)
    with_glossary.transcript.append(TURN)
    await with_glossary.enrich()
    card = card_of_kind(with_glossary, "recall")
    assert card["detail"] == "Kestrel is the deploy gate."


async def test_recall_is_painted_before_jargon(with_glossary, vendor) -> None:
    """Order on the wire is order on screen. When both fire, the note the
    person wrote for this call is the one worth the glance."""
    vendor.fail(503, "server_overloaded", "overloaded", times=4)
    with_glossary.transcript.append(TURN)
    await with_glossary.enrich()
    assert [c["kind"] for c in with_glossary.sent[-1]["cards"]] == ["recall", "jargon"]


async def test_the_glossary_costs_nothing_when_the_vendors_are_healthy(with_glossary, vendor) -> None:
    """It is a floor, not a first pass. A healthy primary means it never runs.

    The local link can now write a recall card of its own, so the reason has
    narrowed: the model reads what was *meant*, the indexes only match what was
    literally said. The better answer still wins when it is available.
    """
    vendor.cards(CARD)
    with_glossary.transcript.append(TURN)
    await with_glossary.enrich()
    assert only_card(with_glossary)["label"] == "Kestrel"
    assert vendor.calls_to("deepseek") == 0


async def test_a_glossary_miss_reports_the_vendor_failure_not_its_own(with_glossary, vendor) -> None:
    """`with_fallbacks` re-raises only the LAST link's exception, and the last
    link is the glossary. "No glossary term in this turn" is not what someone
    needs to read when both vendors are down."""
    vendor.fail(402, "billing_error", "out of credit", times=4)
    with_glossary.transcript.append(("interviewer", "Tell me about a time you disagreed with a manager."))
    await with_glossary.enrich()
    card = only_card(with_glossary)
    assert card["kind"] == "error"
    assert card["label"] == "Provider out of credit", "the glossary's miss masked the real failure"


def test_an_unbuilt_glossary_still_leaves_a_local_link_worth_having(build_server, tmp_path) -> None:
    """Either half is enough. The books are optional and most deployments will
    never run the builder, but every deployment has a prep pack — so an unbuilt
    glossary must not take recall down with it."""
    server = build_server(GLOSS_PROVIDER="anthropic", GLOSS_FALLBACKS="deepseek,local",
                          GLOSS_KB=str(tmp_path / "not-built.json"))
    assert [name for name, _, _ in server.LINKS] == ["anthropic", "deepseek", "local"]
    assert len(server.GLOSSARY) == 0 and len(server.NOTES) > 0


def test_an_empty_local_link_is_dropped_rather_than_blocking_the_boot(build_server, tmp_path) -> None:
    """Both halves empty is benign, and still must not refuse the boot. A pack
    with no headings indexes to nothing: recall.py needs a heading to name a
    card with."""
    pack = tmp_path / "headless"
    pack.mkdir()
    (pack / "00-notes.md").write_text("Just some prose with no heading anywhere in it.\n")
    server = build_server(GLOSS_PROVIDER="anthropic", GLOSS_FALLBACKS="deepseek,local",
                          GLOSS_SESSION=str(pack), GLOSS_KB=str(tmp_path / "not-built.json"))
    assert [name for name, _, _ in server.LINKS] == ["anthropic", "deepseek"]


def test_a_malformed_glossary_refuses_the_boot(build_server, tmp_path) -> None:
    """A file that exists but is wrong is a build that went wrong."""
    path = tmp_path / "glossary.json"
    path.write_text(json.dumps({"entries": [{"term": "x"}]}))
    with pytest.raises(Exception, match=r"alidation|definition"):
        build_server(GLOSS_PROVIDER="anthropic", GLOSS_FALLBACKS="local", GLOSS_KB=str(path))


async def test_the_glossary_cannot_be_preflighted_by_being_asked(with_glossary) -> None:
    """Which is why main() proves it by counting its terms instead.

    On an empty transcript the glossary knows nothing, and it reports
    not-knowing as a failure — so a preflight that asked it a question would
    refuse every boot.
    """
    local = next(link for name, _, link in with_glossary.LINKS if name == "local")
    with pytest.raises(with_glossary.ProviderUnavailable):
        await local.ainvoke(None)


async def test_the_boot_survives_a_glossary_that_cannot_answer_yet(with_glossary, vendor) -> None:
    """main() preflights every link, and asking the glossary a question would
    refuse every boot. Reaching the serve loop is what proves it was skipped.
    """
    vendor.cards(times=4)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(with_glossary.main(), 3)   # got past preflight, now serving


async def test_the_boot_still_refuses_when_a_vendor_link_is_dead(with_glossary, vendor) -> None:
    """The glossary being exempt must not exempt anything else."""
    vendor.fail(401, "authentication_error", "invalid key", times=4)
    with pytest.raises(SystemExit, match="Cannot reach"):
        await asyncio.wait_for(with_glossary.main(), 5)


# --- the instant preview ----------------------------------------------------

JARGON_TURN = ("interviewer", "We gate every deploy through Kestrel. Thoughts on that?")


async def test_the_glossary_paints_before_any_model_is_asked(with_glossary, vendor) -> None:
    """The chain answers in 1.1-1.8s; a dict answers in microseconds. On a live
    call that gap is the difference between reading a definition while the
    interviewer is still talking and reading it afterwards."""
    with_glossary.transcript.append(JARGON_TURN)
    await with_glossary.preview(JARGON_TURN[1])
    assert card_of_kind(with_glossary, "jargon")["label"] == "Kestrel"
    assert vendor.calls == [], "the preview called out to a vendor"


async def test_the_notes_paint_before_any_model_is_asked(with_glossary, vendor) -> None:
    """The pack is parsed at startup and indexed in memory, so a recall card no
    longer has to wait 1.5s for a vendor to write it."""
    with_glossary.transcript.append(JARGON_TURN)
    await with_glossary.preview(JARGON_TURN[1])
    assert card_of_kind(with_glossary, "recall")["detail"] == "Kestrel is the deploy gate."
    assert vendor.calls == [], "the preview called out to a vendor"


async def test_the_preview_does_not_replace_the_model_call(with_glossary, vendor) -> None:
    """The indexes match words that were literally said; the model reads what
    was meant, and that is still the better card. The preview buys latency, not
    tokens."""
    vendor.cards(CARD)
    with_glossary.transcript.append(JARGON_TURN)
    await with_glossary.preview(JARGON_TURN[1])
    await with_glossary.enrich()
    assert vendor.calls_to("anthropic") == 1


async def test_a_preview_the_model_confirms_is_not_retracted(with_glossary, vendor) -> None:
    """Same topic id means the model corroborated it: one card, updated in
    place, with the full TTL."""
    vendor.cards(
        {"id": "kestrel", "kind": "jargon", "label": "Kestrel",
         "detail": "Their gate; raised twice, likely a real constraint."},
        {"id": "notes", "kind": "recall", "label": "Notes",
         "detail": "You wrote that Kestrel is the deploy gate."},
    )
    with_glossary.transcript.append(JARGON_TURN)
    await with_glossary.preview(JARGON_TURN[1])
    await with_glossary.enrich()

    assert not any(m.get("type") == "expire" for m in with_glossary.sent)
    assert card_of_kind(with_glossary, "jargon")["detail"].startswith("Their gate")


async def test_only_the_uncorroborated_half_of_a_preview_is_retracted(with_glossary, vendor) -> None:
    """The preview now paints two cards, and the model may confirm one of them.

    Retraction is per id and always was, which is what makes this work without
    a change: the confirmed card updates in place and the other comes down.
    """
    vendor.cards({"id": "kestrel", "kind": "jargon", "label": "Kestrel",
                  "detail": "Their gate; raised twice, likely a real constraint."})
    with_glossary.transcript.append(JARGON_TURN)
    await with_glossary.preview(JARGON_TURN[1])
    await with_glossary.enrich()

    expires = [m for m in with_glossary.sent if m.get("type") == "expire"]
    assert expires[-1]["ids"] == ["notes"], "the confirmed card was retracted too"
    assert card_of_kind(with_glossary, "jargon")["detail"].startswith("Their gate")


async def test_a_preview_the_model_contradicts_is_retracted(with_glossary, vendor) -> None:
    """Left standing, the guess would sit beside the model's own card on a
    different topic — the duplication the card lifecycle exists to prevent."""
    vendor.cards({"id": "contract-testing", "kind": "jargon",
                  "label": "Contract testing", "detail": "CDC between services."})
    with_glossary.transcript.append(JARGON_TURN)
    await with_glossary.preview(JARGON_TURN[1])
    await with_glossary.enrich()

    expires = [m for m in with_glossary.sent if m.get("type") == "expire"]
    assert expires, "the unconfirmed preview was left standing"
    assert expires[-1]["ids"] == ["kestrel", "notes"]


async def test_a_quiet_turn_retracts_the_preview(with_glossary, vendor) -> None:
    """The model read the same turn and had nothing to say. Its silence is the
    better judgement — the preview was a keyword match."""
    vendor.cards()                      # zero cards: the normal turn
    with_glossary.transcript.append(JARGON_TURN)
    await with_glossary.preview(JARGON_TURN[1])
    await with_glossary.enrich()

    expires = [m for m in with_glossary.sent if m.get("type") == "expire"]
    assert expires and expires[-1]["ids"] == ["kestrel", "notes"]


async def test_a_turn_the_glossary_does_not_know_paints_nothing(with_glossary) -> None:
    quiet = "Tell me about a time you disagreed with a manager."
    with_glossary.transcript.append(("interviewer", quiet))
    await with_glossary.preview(quiet)
    assert with_glossary.sent == []


async def test_the_preview_can_be_turned_off(build_server, vendor, tmp_path) -> None:
    """Off means the screen waits for the model every turn, which is the
    behaviour before this existed."""
    base = vendor.start()
    path = tmp_path / "glossary.json"
    path.write_text(json.dumps(GLOSSARY))
    server = build_server(GLOSS_PROVIDER="anthropic", GLOSS_FALLBACKS="deepseek,local",
                          GLOSS_KB=str(path), GLOSS_PREVIEW="",
                          ANTHROPIC_BASE_URL=base, ANTHROPIC_API_URL=base,
                          DEEPSEEK_API_BASE=base)
    sent: list[dict] = []

    async def capture(payload: dict) -> None:
        sent.append(payload)

    server.broadcast = capture
    await server.preview(JARGON_TURN[1])
    assert sent == []


async def test_no_glossary_means_no_preview_and_no_crash(build_server, vendor, tmp_path) -> None:
    base = vendor.start()
    server = build_server(GLOSS_PROVIDER="anthropic", GLOSS_FALLBACKS="deepseek",
                          GLOSS_KB=str(tmp_path / "absent.json"),
                          ANTHROPIC_BASE_URL=base, ANTHROPIC_API_URL=base,
                          DEEPSEEK_API_BASE=base)
    sent: list[dict] = []

    async def capture(payload: dict) -> None:
        sent.append(payload)

    server.broadcast = capture
    await server.preview("anything at all")
    assert sent == []
