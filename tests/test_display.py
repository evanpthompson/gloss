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

# Port 1 is reserved and never listening, so the page's websocket fails fast
# and stays failed. Any unreachable address would do; this one cannot collide
# with something a developer happens to be running.
DEAD_SOCKET = "ws://127.0.0.1:1/display"


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
    # Pinned at a port nothing listens on, so the page cannot reach a real
    # b_server. These tests drive render() directly and the socket is not part
    # of what they assert -- but display.html connects on load, and a server
    # running on the default 8765 (a developer testing the live pipe on the
    # same machine, which is exactly when these get run) would push real cards
    # into the page mid-test. That surfaced as one browser test failing in the
    # full run and passing in isolation, which is the most expensive kind of
    # flake: it looks like a real regression.
    p.goto(DISPLAY.as_uri() + f"?ws={DEAD_SOCKET}")
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

    def key(self, name: str) -> None:
        """A presenter clicker is an HID keyboard; this is what it sends."""
        self.page.keyboard.press(name)

    def click(self, label: str) -> None:
        """The MVP dismissal: a card leaves when it is clicked."""
        self.page.click(f".card:has(.label:text-is('{label}'))")

    @property
    def selected(self) -> str | None:
        return self.page.evaluate(
            "() => document.querySelector('.card.sel')?.querySelector('.label').textContent ?? null"
        )

    @property
    def pinned(self) -> list[str]:
        return self.page.evaluate(
            "() => [...document.querySelectorAll('.card.pin')]"
            ".map(e => e.querySelector('.label').textContent)"
        )

    @property
    def blanked(self) -> bool:
        return self.page.evaluate("() => document.getElementById('cards').classList.contains('blank')")

    @property
    def idle(self) -> str | None:
        return self.page.evaluate("() => document.getElementById('idle')?.textContent ?? null")


def card(id_: str, label: str, detail: str = "detail", ttl: float = 0, kind: str = "jargon") -> dict:
    """`ttl=0` is what the server now sends for content: no clock, stays until
    dismissed. Tests that are about expiry pass a TTL explicitly, which is also
    what a status card still carries."""
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
        '"detail":"Their deploy gate.","ttl":0}],"max":3}'
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


# --- clicker control (Phase 4a) ---------------------------------------------
#
# A Bluetooth presenter is an HID keyboard, so these drive real key events
# rather than calling the handlers. That is the whole reason 4a needs no local
# process: the clicker produces exactly what these tests produce.


def three(page: Display) -> None:
    for i, label in enumerate(("Kestrel", "BM25", "A2DP")):
        page.turn(card(f"t{i}", label))
        page.wait(15)


def test_the_first_press_selects_the_newest_card(page: Display) -> None:
    """Nothing is selected until the clicker is used, and the newest card is
    the one most likely to be the one being acted on."""
    three(page)
    assert page.selected is None
    page.key("ArrowRight")
    assert page.selected == "A2DP"


def test_selection_cycles_and_wraps(page: Display) -> None:
    three(page)
    page.key("ArrowRight")                      # A2DP (newest)
    page.key("ArrowRight")
    assert page.selected == "Kestrel", "did not wrap to the start"
    page.key("ArrowLeft")
    assert page.selected == "A2DP"


def test_the_keys_a_presenter_actually_sends(page: Display) -> None:
    """Presenters disagree about what they emit; PageDown/PageUp are as common
    as the arrows."""
    three(page)
    page.key("PageDown")
    first = page.selected
    page.key("PageUp")
    page.key("PageDown")
    assert page.selected == first


def test_escape_clears_the_selection(page: Display) -> None:
    three(page)
    page.key("ArrowRight")
    assert page.selected is not None
    page.key("Escape")
    assert page.selected is None


# --- pin --------------------------------------------------------------------


def test_a_pinned_card_outlives_its_ttl(page: Display) -> None:
    """The point of the feature: a topic is still live but the model has moved
    on, and the card would otherwise clear itself."""
    page.turn(card("kestrel", "Kestrel", ttl=0.3))
    page.key("ArrowRight")
    page.key("Enter")
    assert page.pinned == ["Kestrel"]
    page.wait(700)
    assert page.labels == ["Kestrel"], "the pinned card expired anyway"


def test_unpinning_restores_the_clock(page: Display) -> None:
    page.turn(card("kestrel", "Kestrel", ttl=0.3))
    page.key("ArrowRight")
    page.key("Enter")           # pin
    page.wait(500)
    page.key("Enter")           # unpin — the TTL starts again from here
    assert page.pinned == []
    page.wait(700)
    assert page.labels == []


def test_a_refreshed_pin_still_ignores_its_ttl(page: Display) -> None:
    """The server keeps sending the card while the topic recurs. A pin has to
    survive those updates, or it would silently come undone."""
    page.turn(card("kestrel", "Kestrel", ttl=0.3))
    page.key("ArrowRight")
    page.key("Enter")
    page.turn(card("kestrel", "Kestrel", "updated detail", ttl=0.3))
    page.wait(700)
    assert page.labels == ["Kestrel"]
    assert page.detail("Kestrel") == "updated detail"


def test_p_pins_too(page: Display) -> None:
    page.turn(card("kestrel", "Kestrel"))
    page.key("ArrowRight")
    page.key("p")
    assert page.pinned == ["Kestrel"]


# --- dismiss ----------------------------------------------------------------


def test_dismiss_removes_the_selected_card(page: Display) -> None:
    three(page)
    page.key("ArrowRight")          # A2DP
    page.key("Backspace")
    page.settle()
    assert "A2DP" not in page.labels
    assert len(page.labels) == 2


def test_dismiss_moves_the_selection_on(page: Display) -> None:
    """Otherwise the next press acts on nothing and the clicker feels dead."""
    three(page)
    page.key("ArrowRight")
    page.key("Backspace")
    page.settle()
    assert page.selected is not None


def test_a_dismissed_card_is_gone_even_if_pinned(page: Display) -> None:
    page.turn(card("kestrel", "Kestrel", ttl=90))
    page.key("ArrowRight")
    page.key("Enter")               # pin
    page.key("x")                   # dismiss
    page.settle()
    assert page.labels == []


# --- persistence: the MVP card stays until it is dismissed ------------------
#
# A clock cannot know when the call will let you look up. In use about half the
# cards in a session were never seen, so content cards ship with no TTL at all
# (`GLOSS_CARD_TTL_S=0`) and leave by click, by key, or by eviction. Status
# cards keep their clock, because a message about a failure that has already
# recovered goes stale whether or not anyone dismisses it.


def test_a_card_without_a_ttl_does_not_leave_on_its_own(page: Display) -> None:
    page.turn(card("kestrel", "Kestrel"))
    page.wait(700)
    assert page.labels == ["Kestrel"]


def test_a_ttl_of_zero_is_not_read_as_expire_immediately(page: Display) -> None:
    """The failure this guards is silent: `0` falling through a truthiness test
    into the old 90s default would look identical until a card vanished."""
    page.turn({"id": "kestrel", "kind": "jargon", "label": "Kestrel",
               "detail": "d", "ttl": 0})
    page.settle()
    assert page.labels == ["Kestrel"]


def test_a_click_dismisses_the_card_that_was_clicked(page: Display) -> None:
    """Not the selected one. The mouse and the clicker are different hands."""
    page.turn(card("kestrel", "Kestrel"), card("a2dp", "A2DP"))
    page.key("ArrowRight")          # select A2DP, the newest
    page.click("Kestrel")
    page.settle()
    assert page.labels == ["A2DP"]


def test_a_click_dismisses_a_pinned_card_too(page: Display) -> None:
    """Pinning is protection from eviction, not from the person."""
    page.turn(card("kestrel", "Kestrel"))
    page.key("ArrowRight")
    page.key("Enter")
    assert page.pinned == ["Kestrel"]
    page.click("Kestrel")
    page.settle()
    assert page.labels == []


def test_the_cap_still_bounds_a_screen_where_nothing_expires(page: Display) -> None:
    """With the clock gone the cap is the only thing between the display and a
    wall of stale topics, so it has to hold on cards that never expire."""
    page.turn(card("a", "Alpha"))
    page.turn(card("b", "Beta"))
    page.turn(card("c", "Gamma"))
    page.turn(card("d", "Delta"))
    page.settle()
    assert page.labels == ["Beta", "Gamma", "Delta"]


def test_a_status_card_still_clears_itself(page: Display) -> None:
    """The one thing that must keep a clock: nobody dismisses a message about a
    vendor outage that has already ended."""
    page.turn(card("kestrel", "Kestrel"))
    page.turn({"id": "err", "kind": "error", "label": "Model provider overloaded",
               "detail": "staged", "ttl": 0.3})
    assert "Model provider overloaded" in page.labels
    page.wait(700)
    assert page.labels == ["Kestrel"]


# --- blank ------------------------------------------------------------------


def test_blank_hides_everything_and_restores_it(page: Display) -> None:
    """Someone walks up. One button, nothing on screen, state kept."""
    three(page)
    page.key("b")
    assert page.blanked
    page.key("b")
    assert not page.blanked
    assert len(page.labels) == 3, "blanking lost the cards"


def test_blanking_does_not_stop_the_clock(page: Display) -> None:
    """A blanked screen is hidden, not paused — otherwise unblanking shows a
    conversation that has moved on."""
    page.turn(card("kestrel", "Kestrel", ttl=0.3))
    page.key("b")
    page.wait(700)
    page.key("b")
    assert page.labels == []


# --- pinning and the cap ----------------------------------------------------


def test_the_cap_evicts_an_unpinned_card_first(page: Display) -> None:
    three(page)
    page.key("ArrowRight")          # A2DP, the newest
    page.key("ArrowRight")          # wrap to Kestrel, the stalest
    page.key("Enter")               # pin the stalest
    assert page.pinned == ["Kestrel"]

    page.turn(card("new", "Service mesh"), max_cards=3)
    page.settle()
    assert "Kestrel" in page.labels, "the pinned card was evicted first"
    assert len(page.labels) == 3


def test_the_cap_is_absolute_even_when_everything_is_pinned(page: Display) -> None:
    """Pinning everything is a choice to fill the screen, not a way to make it
    bigger. A display that grows without limit stops being readable, which is
    the one thing it cannot afford to be."""
    three(page)
    for _ in range(3):
        page.key("ArrowRight")
        page.key("Enter")
    assert len(page.pinned) == 3

    page.turn(card("new", "Service mesh"), max_cards=3)
    page.settle()
    assert len(page.labels) == 3
    assert "Service mesh" in page.labels


# --- HUD mode (Phase 4b, ?mode=wheel) ---------------------------------------
#
# The HUD is not a second display. It is the same card nodes, the same
# lifecycle and the same selection model, painted as a wheel — so what these
# tests guard is that the fold did not fork any of that, plus the two things
# the prototype measured rather than reasoned about: the focus row holds one
# screen height, and nothing but the column moves.


class Hud(Display):
    """Same wrapper, pointed at the same file with the mode flag on."""

    def d(self, label: str) -> str | None:
        return self.page.evaluate(
            "(want) => [...document.querySelectorAll('.card')]"
            ".find(e => e.querySelector('.label').textContent === want)?.dataset.d",
            label,
        )

    def hidden(self, label: str) -> bool:
        return self.page.evaluate(
            "(want) => [...document.querySelectorAll('.card')]"
            ".find(e => e.querySelector('.label').textContent === want)"
            "?.style.display === 'none'",
            label,
        )

    def opacity(self, label: str) -> float:
        return float(self.page.evaluate(
            "(want) => getComputedStyle([...document.querySelectorAll('.card')]"
            ".find(e => e.querySelector('.label').textContent === want)).opacity",
            label,
        ))

    @property
    def detail_shown(self) -> str | None:
        el = self.page.locator("#hud-detail")
        return el.text_content() if "on" in (el.get_attribute("class") or "") else None

    def rows_overlapping_detail(self) -> list[str]:
        """Labels of visible rows whose box intersects the detail's box.

        Geometry, not styling: two elements can each be perfectly styled and
        still occupy the same pixels. Rows faded to zero are excluded — an
        invisible row sharing space with the detail costs nothing."""
        return self.page.evaluate(
            """() => {
              const det = document.getElementById('hud-detail').getBoundingClientRect();
              return [...document.querySelectorAll('.card')]
                .filter(c => parseFloat(getComputedStyle(c).opacity) > 0.01)
                .filter(c => {
                  const r = c.getBoundingClientRect();
                  return !(r.right < det.left || r.left > det.right ||
                           r.bottom < det.top || r.top > det.bottom);
                })
                .map(c => c.querySelector('.label').textContent);
            }"""
        )

    @property
    def focus_centre_y(self) -> float:
        box = self.page.locator(".card.sel").bounding_box()
        return box["y"] + box["height"] / 2

    def wheel(self, delta: float) -> None:
        self.page.mouse.move(640, 400)   # the wheel lands where the pointer is
        self.page.mouse.wheel(0, delta)
        self.page.wait_for_timeout(60)


@pytest.fixture
def hud(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    p = ctx.new_page()
    p.goto(DISPLAY.as_uri() + f"?mode=wheel&dwell=200&ws={DEAD_SOCKET}")
    yield Hud(p)
    ctx.close()


def test_the_hud_always_has_a_focus(hud: Hud) -> None:
    """On the second screen no selection is the normal state. On the HUD it
    would mean no row is at full size — the row the eye is meant to land on
    without looking would not exist."""
    three(hud)
    assert hud.selected == "A2DP", "the newest card should take the focus"


def test_the_second_screen_still_starts_with_nothing_selected(page: Display) -> None:
    """The guard on the fold: adding a mode must not change the mode that ships."""
    three(page)
    assert page.selected is None
    assert page.page.evaluate("() => document.getElementById('cards').style.transform") == ""
    assert page.page.evaluate(
        "() => [...document.querySelectorAll('.card')].every(e => e.dataset.d === undefined)"
    ), "distance styling leaked into the second screen"


def test_distance_from_the_focus_is_the_only_thing_that_sets_weight(hud: Hud) -> None:
    three(hud)                       # focus lands on A2DP, the last row
    assert (hud.d("A2DP"), hud.d("BM25"), hud.d("Kestrel")) == ("0", "1", "2")

    hud.key("ArrowLeft")             # step back one
    assert (hud.d("A2DP"), hud.d("BM25"), hud.d("Kestrel")) == ("1", "0", "1")


def test_the_focused_row_holds_one_fixed_screen_height(hud: Hud) -> None:
    """The measured property, and the reason the column is anchored rather than
    laid out in flow: in flow the focus drifted 16px at the ends of the list,
    and animating font-size added 12px more. 5px of sub-pixel rounding is what
    is left, so the tolerance is deliberately tight."""
    three(hud)
    hud.wait(200)
    first = hud.focus_centre_y

    for _ in range(4):               # walk the list, wrapping past both ends
        hud.key("ArrowLeft")
        hud.wait(200)
        assert abs(hud.focus_centre_y - first) <= 6, "the focus row moved under the reader"


def test_detail_waits_for_dwell_and_leaves_the_instant_the_focus_moves(hud: Hud) -> None:
    """Progressive disclosure by dwell — § 4b Direction 2. A detail line that
    survives a move is a line read about the wrong card."""
    hud.turn(card("kestrel", "Kestrel", "Deploy gate."), card("bm25", "BM25", "Ranking."))
    assert hud.detail_shown is None, "detail was shown before the dwell elapsed"

    hud.wait(300)                    # fixture sets dwell=200ms
    assert hud.detail_shown == "Ranking."

    hud.key("ArrowLeft")
    assert hud.detail_shown is None, "detail survived a move"

    hud.wait(300)
    assert hud.detail_shown == "Deploy gate."


def test_the_detail_never_draws_through_a_row(hud: Hud) -> None:
    """Geometry, which nothing here asserted before 2026-08-30.

    The detail used to sit at `top: 50%; margin-top: 7vh` — a fixed offset that
    assumes the next row is further down than that. In `tools/wheel_hud.html`,
    which renders five rows, a detail long enough to wrap was drawn straight
    through two of them; measured, before and after.

    **This page was not affected**, and saying so matters more than a tidier
    story: at `GLOSS_MAX_CARDS=3` the column is short enough that the old
    offset cleared the last row, measured at 1280x720 and 1680x1050 with an
    error card added. Reverting the fix does not fail this test. What it
    guards is the invariant rather than the incident — if the cap rises or the
    type grows, this is the check that notices, and every other HUD test would
    still pass because they assert opacity, distance and font size and never
    ask where anything is.
    """
    long_detail = (
        "One logical write causes many physical writes, wearing the device out "
        "faster than the write volume alone suggests. Theirs runs about 12x."
    )
    hud.turn(
        card("kestrel", "Kestrel", detail="Their deploy gate."),
        card("tail", "Tail latency", detail="The slowest requests, not the average."),
        card("write-amp", "Write amplification", detail=long_detail),
        max_cards=3,
    )
    hud.key("d")          # detail now, without waiting out the dwell
    hud.settle()
    assert hud.detail_shown == long_detail, "the detail did not render"
    assert hud.rows_overlapping_detail() == []


def test_d_shows_the_detail_without_waiting(hud: Hud) -> None:
    hud.turn(card("kestrel", "Kestrel", "Deploy gate."))
    hud.key("d")
    assert hud.detail_shown == "Deploy gate."


def test_the_wheel_moves_the_focus(hud: Hud) -> None:
    """The wheel is one more input into the selection model 4a already has, not
    a second way to move. How far one notch travels is a sensitivity setting and
    is not asserted here; that it moves at all is the wiring under test."""
    three(hud)
    hud.wheel(140)
    assert hud.selected in ("Kestrel", "BM25"), "the wheel did not move the focus"


def test_an_expiring_focus_hands_the_focus_on(hud: Hud) -> None:
    """A TTL can take the focused row out from under the reader. The HUD has to
    land somewhere; leaving no focus is the one outcome it cannot have."""
    hud.turn(card("kestrel", "Kestrel"), card("bm25", "BM25", ttl=0.3))
    assert hud.selected == "BM25"
    hud.wait(700)
    assert hud.selected == "Kestrel"


def test_an_error_row_is_never_dimmed_or_hidden_by_distance(hud: Hud) -> None:
    """Fail loud. "Down" and "scrolled out of view" must not look the same, so
    the error row is exempt from both the opacity gradient and the cull."""
    hud.turn(card("err", "not working", kind="error"))
    three(hud)                                  # pushes the error three rows away

    assert hud.d("not working") == "3", "the error should be outside the visible band"
    assert not hud.hidden("not working"), "an error card was culled by distance"
    assert hud.opacity("not working") == 1.0, "an error card was dimmed by distance"
