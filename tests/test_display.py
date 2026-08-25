"""display.html, driven in a real browser.

This is the only part of gloss that is not Python, and it was the only part
with no test — which is how the first version of the card cap shipped a bug
that reading the code did not reveal: an error card evicted a real card, and
when the error expired twenty seconds later the topic did not come back.

A stubbed DOM would not have been worth much here. The behaviour under test is
DOM identity (is this the *same* node, in the same position?) and timer-driven
expiry, so the test drives the actual file in an actual browser.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

DISPLAY = Path(__file__).resolve().parent.parent / "display.html"

# Browsers are a large download, so a developer without them gets a skip. CI
# sets this, and there a missing browser is a failure: a check that could not
# run is not a pass, and a silent skip in the environment that gates would be
# indistinguishable from green.
REQUIRED = os.environ.get("GLOSS_REQUIRE_BROWSER_TESTS", "") not in ("", "0", "false")


@pytest.fixture(scope="module")
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        if REQUIRED:
            raise RuntimeError(f"playwright is required in this environment: {exc}") from exc
        pytest.skip("playwright not installed")
    with sync_playwright() as p:
        try:
            launched = p.chromium.launch()
        except Exception as exc:
            if REQUIRED:
                raise RuntimeError(
                    f"chromium is required in this environment but would not "
                    f"launch: {exc}. Run `playwright install chromium`."
                ) from exc
            pytest.skip(f"chromium unavailable ({type(exc).__name__}); "
                        "run `uv run playwright install chromium`")
        yield launched
        launched.close()


@pytest.fixture
def page(browser):
    """A fresh page per test. The websocket cannot connect and is not meant to —
    these tests drive `render()` directly, which is the unit of behaviour."""
    ctx = browser.new_context()
    p = ctx.new_page()
    p.goto(DISPLAY.as_uri())
    yield Display(p)
    ctx.close()


class Display:
    """Thin wrapper so the tests read as behaviour rather than as evaluate()."""

    FADE_MS = 300  # the removal transition is 220ms; wait past it before asserting

    def __init__(self, page) -> None:
        self.page = page

    def turn(self, *cards: dict, max_cards: int = 3) -> None:
        """One broadcast from the server. Zero cards is a quiet turn."""
        self.page.evaluate(
            "([list, max]) => render(list, max)", [list(cards), max_cards]
        )

    def settle(self) -> None:
        self.page.wait_for_timeout(self.FADE_MS)

    def wait(self, ms: int) -> None:
        self.page.wait_for_timeout(ms)

    @property
    def labels(self) -> list[str]:
        return self.page.evaluate(
            "() => [...document.querySelectorAll('.card')]"
            ".map(e => e.querySelector('.label').textContent)"
        )

    def detail(self, label: str) -> str:
        return self.page.evaluate(
            "(want) => [...document.querySelectorAll('.card')]"
            ".find(e => e.querySelector('.label').textContent === want)"
            "?.querySelector('.detail').textContent",
            label,
        )

    def kind(self, label: str) -> str:
        return self.page.evaluate(
            "(want) => [...document.querySelectorAll('.card')]"
            ".find(e => e.querySelector('.label').textContent === want)?.dataset.kind",
            label,
        )

    def tag(self, label: str) -> str:
        return self.page.evaluate(
            "(want) => [...document.querySelectorAll('.card')]"
            ".find(e => e.querySelector('.label').textContent === want)"
            "?.querySelector('.tag').textContent",
            label,
        )

    def stamp(self, label: str, mark: str) -> None:
        """Mark a DOM node so a later assertion can prove it is the same one."""
        self.page.evaluate(
            "([want, mark]) => [...document.querySelectorAll('.card')]"
            ".find(e => e.querySelector('.label').textContent === want)"
            ".dataset.probe = mark",
            [label, mark],
        )

    def stamped(self, mark: str) -> bool:
        return self.page.evaluate(f"() => !!document.querySelector('[data-probe=\"{mark}\"]')")

    def position_of_stamp(self, mark: str) -> int:
        return self.page.evaluate(
            f"() => [...document.querySelectorAll('.card')]"
            f".findIndex(e => e.dataset.probe === '{mark}')"
        )

    @property
    def idle(self) -> str | None:
        return self.page.evaluate("() => document.getElementById('idle')?.textContent ?? null")


def card(id_: str, label: str, detail: str = "detail", ttl: float = 90, kind: str = "jargon") -> dict:
    return {"id": id_, "kind": kind, "label": label, "detail": detail, "ttl": ttl}


# --- the plan's own acceptance criterion -----------------------------------


def test_a_topic_returned_to_updates_in_place_rather_than_flashing(page: Display) -> None:
    """PHASE-3-PLAN S4: "a topic raised on turn 1 and returned to on turn 4
    shows one card that updates, not two that flash"."""
    page.turn(card("kestrel", "Kestrel", "Deploy gate."))
    page.stamp("Kestrel", "turn1")

    page.turn()                                        # a quiet turn
    page.turn(card("contract", "Contract testing"))    # an unrelated topic
    page.turn(card("kestrel", "Kestrel", "Asked twice — a real constraint."))
    page.settle()

    assert page.labels == ["Kestrel", "Contract testing"]
    assert page.stamped("turn1"), "the card was replaced, not updated"
    assert page.position_of_stamp("turn1") == 0, "the card moved under the reader"
    assert page.detail("Kestrel") == "Asked twice — a real constraint."


def test_a_quiet_turn_leaves_the_screen_alone(page: Display) -> None:
    """Zero cards is the normal result. Blanking mid-glance would lose whatever
    the reader was part-way through."""
    page.turn(card("kestrel", "Kestrel"))
    page.turn()
    page.settle()
    assert page.labels == ["Kestrel"]


def test_a_new_topic_appends_without_moving_the_others(page: Display) -> None:
    page.turn(card("a", "First"))
    page.stamp("First", "first")
    page.turn(card("b", "Second"))
    page.settle()
    assert page.labels == ["First", "Second"]
    assert page.position_of_stamp("first") == 0


# --- the cap ----------------------------------------------------------------


def test_the_visible_cap_is_enforced(page: Display) -> None:
    for i in range(5):
        page.turn(card(f"t{i}", f"Topic {i}"), max_cards=3)
        page.wait(15)
    page.settle()
    assert len(page.labels) == 3


def test_eviction_drops_the_least_recently_mentioned_not_the_oldest(page: Display) -> None:
    """A thread the conversation keeps returning to should outlive a one-off
    raised earlier."""
    page.turn(card("kestrel", "Kestrel"));            page.wait(15)
    page.turn(card("contract", "Contract testing"));  page.wait(15)
    page.turn(card("bm25", "BM25"));                  page.wait(15)
    page.turn(card("kestrel", "Kestrel"));            page.wait(15)  # re-mentioned
    page.turn(card("a2dp", "A2DP"))                                   # pushes past the cap
    page.settle()

    assert "Kestrel" in page.labels, "the re-mentioned topic was evicted"
    assert "Contract testing" not in page.labels, "the stalest topic survived"
    assert len(page.labels) == 3


# --- errors -----------------------------------------------------------------


def test_an_error_card_does_not_cost_a_content_slot(page: Display) -> None:
    """The first implementation counted errors against the cap, so a transient
    outage evicted a real card — and the topic never returned, because the card
    that displaced it expired into nothing."""
    for i in range(3):
        page.turn(card(f"t{i}", f"Topic {i}"))
        page.wait(15)
    page.settle()
    content = page.labels

    page.turn({"id": "enrichment-error", "kind": "error",
               "label": "Provider out of credit", "detail": "staged", "ttl": 90})
    page.settle()

    assert len(page.labels) == 4
    assert all(label in page.labels for label in content), "an error evicted content"


def test_an_error_clears_itself_and_leaves_the_content_intact(page: Display) -> None:
    """A failure that has already recovered must not keep claiming the tool is
    down. The next good turn will not overwrite it, because a quiet turn
    broadcasts nothing at all."""
    page.turn(card("kestrel", "Kestrel"))
    page.turn({"id": "enrichment-error", "kind": "error",
               "label": "Model provider overloaded", "detail": "staged", "ttl": 0.3})
    page.settle()
    assert "Model provider overloaded" in page.labels

    page.wait(600)
    assert "Model provider overloaded" not in page.labels
    assert page.labels == ["Kestrel"]


def test_a_repeated_failure_updates_one_card_rather_than_stacking(page: Display) -> None:
    def err(text: str) -> dict:
        return {"id": "enrichment-error", "kind": "error",
                "label": text, "detail": "staged", "ttl": 90}

    page.turn(err("Rate limited"))
    page.turn(err("Provider out of credit"))
    page.settle()
    assert page.labels == ["Provider out of credit"]


# --- expiry -----------------------------------------------------------------


def test_a_card_expires_after_its_ttl(page: Display) -> None:
    page.turn(card("kestrel", "Kestrel", ttl=0.3))
    assert page.labels == ["Kestrel"]
    page.wait(600)
    assert page.labels == []


def test_recurrence_refreshes_the_clock(page: Display) -> None:
    """The TTL counts from the last turn that raised the topic, not the first."""
    page.turn(card("kestrel", "Kestrel", ttl=0.5))
    page.wait(300)
    page.turn(card("kestrel", "Kestrel", ttl=0.5))   # refreshes
    page.wait(300)
    assert page.labels == ["Kestrel"], "the card expired despite being re-mentioned"


def test_idle_returns_when_everything_expires(page: Display) -> None:
    """Empty is the normal state, not a blank screen."""
    page.turn(card("kestrel", "Kestrel", ttl=0.3))
    page.wait(700)
    assert page.idle == "listening"


# --- presentation -----------------------------------------------------------


@pytest.mark.parametrize(
    "kind,tag",
    [("recall", "your notes"), ("jargon", "unfamiliar term"), ("error", "not working")],
)
def test_kind_drives_the_tag_and_the_colour_hook(page: Display, kind: str, tag: str) -> None:
    page.turn(card("x", "Thing", kind=kind))
    assert page.kind("Thing") == kind
    assert page.tag("Thing") == tag


def test_a_card_with_no_id_still_renders(page: Display) -> None:
    """The server always fills one in, but the display must not depend on it —
    a card that vanished for want of a bookkeeping field would be worse than
    one keyed on its label."""
    page.turn({"kind": "jargon", "label": "Unkeyed", "detail": "d", "ttl": 90})
    assert page.labels == ["Unkeyed"]


def test_card_text_is_never_interpreted_as_markup(page: Display) -> None:
    """Card text is model output. It reaches the DOM through textContent, and
    has to stay that way."""
    page.turn(card("x", "<img src=x onerror=alert(1)>", detail="<b>bold</b>"))
    assert page.labels == ["<img src=x onerror=alert(1)>"]
    assert page.detail("<img src=x onerror=alert(1)>") == "<b>bold</b>"
    assert page.page.evaluate("() => document.querySelectorAll('.card img, .card b').length") == 0


def test_the_payloads_the_server_actually_sends_render(page: Display) -> None:
    """Guards against the display and b_server drifting apart on shape."""
    payload = json.loads(
        '{"type":"cards","cards":[{"id":"kestrel","kind":"jargon","label":"Kestrel",'
        '"detail":"Their deploy gate.","ttl":90}],"max":3}'
    )
    page.turn(*payload["cards"], max_cards=payload["max"])
    assert page.labels == ["Kestrel"]


# --- retracting an unconfirmed preview --------------------------------------


def test_a_named_card_can_be_taken_back_down(page: Display) -> None:
    """The server paints a glossary card the instant a turn ends, then retracts
    it if the model says something different. Without this the guess would sit
    beside the model's own card on the same topic — the duplication the whole
    lifecycle exists to prevent."""
    page.turn(card("kestrel", "Kestrel"), card("bm25", "BM25"))
    page.settle()
    assert page.labels == ["Kestrel", "BM25"]

    page.page.evaluate("() => remove('kestrel')")
    page.settle()
    assert page.labels == ["BM25"]


def test_expiring_an_unknown_id_is_harmless(page: Display) -> None:
    """The server retracts by id without tracking what each display has; one
    that connected late may never have seen the card."""
    page.turn(card("kestrel", "Kestrel"))
    page.page.evaluate("() => remove('never-existed')")
    page.settle()
    assert page.labels == ["Kestrel"]


def test_a_confirmed_preview_is_updated_not_duplicated(page: Display) -> None:
    """The glossary and the model agreeing on a topic id is the good case: one
    card, better text, full TTL, no flash."""
    page.turn(card("kestrel", "Kestrel", "A deploy gate.", ttl=12))     # preview
    page.stamp("Kestrel", "preview")
    page.turn(card("kestrel", "Kestrel", "Their gate; they raised it twice.", ttl=90))
    page.settle()
    assert page.labels == ["Kestrel"]
    assert page.stamped("preview"), "the model's card replaced the node instead of updating it"
    assert page.detail("Kestrel") == "Their gate; they raised it twice."
