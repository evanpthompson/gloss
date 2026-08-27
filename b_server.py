"""
Phases 1-2: the pipe, plus Tier 1 enrichment.

Accepts two labeled raw-PCM WebSocket streams from Laptop A
(ws://<this-machine>:8765/interviewer and .../user) and relays each to its own
Deepgram streaming connection.

On each end-of-turn (`speech_final`) from the *interviewer* channel, sends the
recent transcript plus the prep pack to an LLM and broadcasts at most three
glanceable cards to any display client on .../display. See SPEC.md "Phase 2
design" for the trigger, card schema, cache shape and noise-control rules.

Tier 2 post-call research export is Phase 3 and is not here.
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # must run before importing deepgram — it reads DEEPGRAM_API_KEY
# via os.getenv() as a default *argument value*, evaluated once at import time.

import websockets
from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.environment import DeepgramClientEnvironment
from deepgram.listen.v1.types import ListenV1Results
from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda

import failures
import kb
import keyterms
import providers
import recall
from cards import card_schema, valid_cards

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("b_server")

HOST = os.environ.get("B_SERVER_HOST", "0.0.0.0")
PORT = int(os.environ.get("B_SERVER_PORT", "8765"))
SAMPLE_RATE = int(os.environ.get("B_SERVER_SAMPLE_RATE", "48000"))
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY")

# Testability hook: point the Deepgram client at a mock endpoint instead of
# production, e.g. ws://mock-deepgram:9010, for offline/CI end-to-end
# testing. Unset in normal use — real Deepgram is the default.
DEEPGRAM_WS_URL = os.environ.get("DEEPGRAM_WS_URL")
if DEEPGRAM_WS_URL:
    _http_url = DEEPGRAM_WS_URL.replace("ws://", "http://").replace("wss://", "https://")
    DEEPGRAM_ENVIRONMENT = DeepgramClientEnvironment(
        base=_http_url,
        production=DEEPGRAM_WS_URL,
        agent=DEEPGRAM_WS_URL,
        agent_rest=_http_url,
    )
else:
    DEEPGRAM_ENVIRONMENT = DeepgramClientEnvironment.PRODUCTION

CHANNELS = {"interviewer", "user"}

# --- Tier 1 enrichment (Phase 2) -------------------------------------------

# Provider-agnostic on purpose: nothing below this line knows which vendor is
# answering. Swapping to Claude, Bedrock or a local Ollama model is two env
# vars and a `uv add` of that provider package — no code change. Note Gemini is
# NOT available via Bedrock, so "bedrock" and "google_genai" are alternatives
# to each other, not layers.
# The model id, the kwargs that model takes, whether it needs its cache prefix
# marked explicitly, and the size below which it stops caching all have to
# agree. They live as one row in providers.py rather than as four independent
# environment variables, because four variables drift: setting
# GLOSS_PROVIDER=anthropic while an old GLOSS_MODEL_KWARGS still held Gemini's
# thinking_level produced a 400 from a vendor that has never heard of it.
#
# resolve() refuses rather than defaults — an unknown provider, or a kwargs
# override that does not also pin a model. Both refusals are explained where
# they are raised.
PROVIDER, PROFILE, _profile_warnings = providers.resolve(os.environ)
MODEL = PROFILE.model
for _w in _profile_warnings:
    log.warning("%s", _w)
SESSION_DIR = Path(os.environ.get("GLOSS_SESSION", "sessions/example"))
MIN_CHARS = int(os.environ.get("GLOSS_MIN_CHARS", "25"))
MAX_CARDS = int(os.environ.get("GLOSS_MAX_CARDS", "3"))
# How long a card stays up after the last turn that raised its topic. Cards
# used to be replaced wholesale every turn, so a topic discussed across four
# turns flashed four times and nothing stayed readable long enough to be read.
# Now a card persists and its clock is refreshed whenever the topic recurs.
#
# 90s is about two or three conversational turns: long enough that a glance
# away and back still finds the card, short enough that the screen does not
# accumulate a wall of things nobody is talking about any more.
CARD_TTL_S = float(os.environ.get("GLOSS_CARD_TTL_S", "90"))
# Errors clear themselves much faster. A transient failure that has already
# recovered must not sit on screen claiming the tool is down — the next
# successful turn will not necessarily overwrite it, because zero cards is the
# normal result and broadcasts nothing at all.
ERROR_TTL_S = float(os.environ.get("GLOSS_ERROR_TTL_S", "20"))
# A provisional card painted from the glossary the instant a turn ends, before
# any model has been asked. It carries a short TTL so an unconfirmed guess
# fades on its own even if the enrichment pass never returns at all.
PREVIEW_TTL_S = float(os.environ.get("GLOSS_PREVIEW_TTL_S", "12"))
# Set empty to turn the instant preview off and wait for the model every turn.
PREVIEW = os.environ.get("GLOSS_PREVIEW", "1") not in ("", "0", "false")
# The transcript is no longer windowed. A rolling window was the amnesia: a
# term defined in minute three was gone by minute five, and a glossary that
# cannot connect a thing said now to the thing that defined it earlier is not
# doing its job. Every turn is sent, every turn.
#
# Bounded rather than unbounded, though. Haiku 4.5's window is 200K and speech
# runs ~250 tokens a turn, so this is roughly 400 turns of headroom — far past
# any real conversation, but "far past" is not "cannot". On overflow the oldest
# turns are dropped and the drop is logged, because evicting the front of the
# transcript also invalidates every cache read after it and the cost profile
# would otherwise change silently.
MAX_TRANSCRIPT_TOKENS = int(os.environ.get("GLOSS_MAX_TRANSCRIPT_TOKENS", "100000"))
# Anthropic only. 5m is the API default and a cache write does NOT refresh an
# earlier breakpoint's clock, so a five-minute lull mid-conversation evicts the
# prefix and the next turn pays full input price instead of a tenth of it. "1h"
# costs 2x on the one write rather than 1.25x — a fraction of a cent on a 4k
# prefix — and removes the cliff. Drop to "5m" only with a measured reason.
CACHE_TTL = os.environ.get("GLOSS_CACHE_TTL", "1h")
# Caching fails silently everywhere: under the provider's minimum prefix size
# nothing caches, no error is raised, and the counters just read zero. Set this
# to watch every turn rather than only the first.
LOG_CACHE = os.environ.get("GLOSS_LOG_CACHE", "") not in ("", "0", "false")

# Frozen. Nothing per-request may be interpolated into this string or into the
# prep pack below it — both sit in the cached prefix, and one timestamp here
# means the cache is rewritten every turn and never read. See SPEC.md.
INSTRUCTIONS = f"""\
You support someone who is in a live conversation right now. They can spare
about one second to look at their second screen. You never speak to them and
you never answer the question for them.

You emit at most {MAX_CARDS} cards about the MOST RECENT interviewer turn only.

Two kinds, and nothing else:

- "recall": the notes below already cover what was just asked. Quote or
  tightly compress what the notes actually say. Never invent a fact, a
  number, or an anecdote that is not written there. If the notes do not
  cover it, there is no recall card — that is the whole point of this kind.
- "jargon": the interviewer used a term, tool, or acronym the person may not
  know. Name it and define it in one line, so they can ask a good question
  instead of bluffing past it.

Fields: `label` is at most 6 words and is the only part that gets read at a
glance. `detail` is at most 25 words. `id` is a short lowercase hyphenated
slug naming the topic — "kestrel", "contract-testing". Reuse the SAME id
every time the conversation returns to that topic; the card then updates in
place instead of appearing a second time.

Returning an empty list is the correct answer for most turns. Small talk,
logistics, a question already covered by the previous card, an utterance
that is not really a question — all of these get zero cards. Do not
manufacture a card to seem useful; a screen that always has something on it
is a screen that stops being looked at.

The notes below are the person's own preparation, written before the call.
--- NOTES ---
"""

# Derived from the pydantic model in cards.py rather than written out here.
# The same model validates what comes back, so the contract cannot drift
# between what is asked for and what is accepted.
CARD_SCHEMA = card_schema(MAX_CARDS)


def load_prep_pack(directory: Path) -> str:
    """Concatenate the prep pack in sorted filename order.

    Sorted, not glob order: the bytes must be identical across restarts or the
    cached prefix changes for no visible reason.
    """
    if not directory.is_dir():
        log.warning("No prep pack at %s — recall cards will be impossible", directory)
        return "(no notes were provided for this conversation)"
    files = sorted(directory.glob("*.md"))
    if not files:
        log.warning("No .md files in %s — recall cards will be impossible", directory)
        return "(no notes were provided for this conversation)"
    log.info("Prep pack: %s", ", ".join(f.name for f in files))
    return "\n\n".join(f.read_text(encoding="utf-8").strip() for f in files)


PREP_PACK = load_prep_pack(SESSION_DIR)
SYSTEM_PROMPT = INSTRUCTIONS + PREP_PACK

# Prime the recogniser with the pack's own vocabulary.
#
# Everything downstream reads the transcript, so a term Nova-3 mishears is a
# term no later stage can recover: a jargon card fires on what was heard, and a
# recall card matches notes against the words that came back. This is the one
# place in the pipeline where accuracy can still be bought, and the corpus was
# already sitting here — the prep pack is by definition the vocabulary this
# conversation will contain.
#
# Nova-3 caps keyterms at 500 tokens per request and errors above that, so the
# list is filled in rank order and the overflow is reported rather than dropped
# quietly. See keyterms.py for what counts as a term and why.
# The local knowledge base, if one has been built. Loaded once at startup: it
# is a plain dict and the live path never touches disk or the network for it.
GLOSSARY = kb.Glossary.load(Path(os.environ.get("GLOSS_KB", str(kb.DEFAULT_PATH))))
if len(GLOSSARY):
    log.info("Glossary: %d terms loaded", len(GLOSSARY))

# The other half of the local floor. The glossary knows what words mean; this
# knows what the person wrote about them before the call, which is the half
# that was unreachable during an outage — see recall.py.
NOTES = recall.Notes.from_pack(PREP_PACK)
if len(NOTES):
    log.info("Notes: %d section(s) indexed for local recall", len(NOTES))
else:
    log.warning("Notes: no headed sections in the pack — local recall is unavailable")

KEYTERM_BUDGET = int(os.environ.get("GLOSS_KEYTERM_BUDGET", str(keyterms.DEFAULT_BUDGET_TOKENS)))
KEYTERMS, _keyterms_dropped = keyterms.from_pack(PREP_PACK, KEYTERM_BUDGET)
if KEYTERMS:
    log.info(
        "Keyterms: priming Nova-3 with %d term(s)%s — %s%s",
        len(KEYTERMS),
        f", {_keyterms_dropped} over budget and dropped" if _keyterms_dropped else "",
        ", ".join(KEYTERMS[:8]),
        "…" if len(KEYTERMS) > 8 else "",
    )
else:
    # Not an error: a pack can legitimately contain no distinctive vocabulary.
    # Said out loud anyway, because silence here is indistinguishable from the
    # extractor being broken.
    log.warning("Keyterms: none found in %s — Nova-3 runs unprimed", SESSION_DIR)

class _FakeLLM:
    """Offline stand-in for the enrichment model, for the E2E pipe test.

    The E2E suite exists to prove the audio pipe, and mocks every external
    service — there is no LLM provider in it any more than there is a real
    Deepgram. This is the same shape as DEEPGRAM_WS_URL: unset means real,
    set means mocked.

    It is reached only by an explicit GLOSS_PROVIDER=fake, and it still
    requires a key to be configured — the E2E stack supplies a dummy one, the
    same way it supplies DEEPGRAM_API_KEY. Letting the fake run keyless would
    make it the one path where credential plumbing goes untested, which is
    exactly the path you want tested: a deployment that forgot the key should
    fail in CI, not in a live conversation.
    """

    async def ainvoke(self, messages: list) -> dict:
        return {
            "raw": None,
            "parsed": {
                "cards": [
                    {
                        "kind": "jargon",
                        "label": "fake provider",
                        "detail": "GLOSS_PROVIDER=fake — canned card, no model was called.",
                    }
                ]
            },
            "parsing_error": None,
        }


class _Local:
    """The local knowledge base, shaped like a model so the chain can hold it.

    It reads the newest interviewer turn rather than the rendered prompt — a
    dictionary has no use for a system prompt, a transcript or a cache
    breakpoint — and answers from two indexes that are already in memory: the
    prep pack for `recall`, the glossary for `jargon`.

    **Recall comes first** on the wire and therefore first on screen. When both
    fire, the person's own note about this specific call is the one worth the
    glance; a definition is useful, but they wrote the note for this.

    **It raises when it knows nothing**, rather than answering with zero cards.
    Zero cards is how a *quiet turn* looks, and this link only ever runs
    because every vendor above it has already failed. Reporting "nothing to
    say" there would turn a total outage into a normal-looking screen, which is
    the one thing enrich() exists to prevent.
    """

    async def ainvoke(self, _messages: list) -> dict:
        text = transcript[-1][1] if transcript else ""
        cards = [c for c in (NOTES.card(text), GLOSSARY.card(text)) if c is not None]
        if not cards:
            raise LookupError("nothing local matched this turn")
        return {"raw": None, "parsed": {"cards": cards[:MAX_CARDS]}, "parsing_error": None}


# Every provider states its own credential variables, including the fake. The
# fake does not call out, but it must not be the one path where missing
# credentials go unnoticed — a deployment that forgot the key should fail in CI
# rather than in a live conversation. The variables are named per provider in
# providers.py rather than matched on a "*_API_KEY" suffix, because
# DEEPGRAM_API_KEY is always set here and would satisfy any such pattern, so a
# suffix guard would never once fire.
if (_no_key := providers.missing_key(PROFILE, os.environ)) is not None:
    raise SystemExit(f"Cannot start on {PROVIDER}:{MODEL} — {_no_key}")


# --- The fallback chain ----------------------------------------------------
#
# One vendor is a single point of failure for a conversation that is happening
# right now and cannot be rescheduled. `Runnable.with_fallbacks()` puts a second
# vendor behind the first in-process, so an Anthropic outage costs a slower turn
# instead of the rest of the call. See PHASE-3-PLAN.md § "Fallback chain —
# outage resilience, not quota-dodging".
#
# Caches are model-scoped, so the fallback link starts cold. Fine for an outage,
# useless as a cost strategy — this is not a quota dodge.

# The fake provider is the mocked E2E path. Giving it a live fallback would make
# CI reach the internet on the one path built never to.
FALLBACK_NAMES: list[str] = (
    []
    if PROVIDER == "fake"
    else [
        name.strip()
        for name in os.environ.get("GLOSS_FALLBACKS", "deepseek,local").split(",")
        if name.strip() and name.strip() != PROVIDER
    ]
)

# Fallbacks take their provider row verbatim. GLOSS_MODEL / GLOSS_MODEL_KWARGS
# are scoped to the provider you selected and are NOT carried across — a kwarg
# that follows you onto another vendor is the exact drift providers.py exists to
# stop, and a chain is the easiest place for it to happen unnoticed.
LINK_PROFILES: list[tuple[str, providers.Profile]] = [(PROVIDER, PROFILE)]
for _name in FALLBACK_NAMES:
    if _name not in providers.PROFILES:
        raise SystemExit(
            f"Unknown provider {_name!r} in GLOSS_FALLBACKS.\n"
            f"Known providers: {', '.join(sorted(providers.PROFILES))}.\n"
            "Add a row to providers.py before naming it in a chain."
        )
    if _name == "local" and not len(GLOSSARY) and not len(NOTES):
        # Empty on both halves is a benign, expected state — the glossary file
        # is optional and kb.Glossary.load() returns empty rather than raising,
        # and a pack with no headings indexes to nothing. Dropping the link
        # with a warning is right here, where refusing to boot would punish
        # every deployment that has not run the builder. A glossary that exists
        # but is malformed is a different matter: that raises out of load()
        # above, before this point.
        #
        # Either half is enough to keep the link: a pack alone still answers
        # recall during an outage, which is the whole reason recall.py exists.
        log.warning(
            "Local link requested but neither the glossary nor the pack holds "
            "anything — dropping it. Build a glossary with: "
            "uv run python tools/build_kb.py <books-dir>"
        )
        continue
    _fallback = providers.PROFILES[_name]
    # A fallback that cannot authenticate is not a fallback, and you would find
    # that out during the outage it exists for. Refuse at startup instead, and
    # name the thing that would change the answer.
    if (_missing := providers.missing_key(_fallback, os.environ)) is not None:
        raise SystemExit(
            f"Fallback link {_name}:{_fallback.model} has no credential — {_missing}\n"
            "Set the key, or set GLOSS_FALLBACKS= (empty) to run deliberately "
            "on a single provider."
        )
    LINK_PROFILES.append((_name, _fallback))


class ProviderUnavailable(Exception):
    """A link could not answer, so the next one should try.

    Carries the classified `reason` rather than just the original exception:
    the reason decides both whether the chain moves on and whether this link is
    worth trying again later in the same conversation. See failures.py.
    """

    def __init__(self, link: str, reason: failures.Reason, detail: str) -> None:
        super().__init__(f"{link} {reason}: {detail}")
        self.link = link
        self.reason = reason
        self.detail = detail


def system_message_for(profile: providers.Profile) -> SystemMessage:
    """The frozen system prefix, in the shape this provider caches.

    Built once per link, so the prompt prefix is byte-identical on every turn.
    Every provider worth using caches on a prefix match, and none of them can do
    it if the prefix is rebuilt per request — this is why SYSTEM_PROMPT is
    frozen.

    Anthropic is the one provider that needs the cache boundary named out loud:
    a `cache_control` block marks where the reusable prefix ends. Everything
    else caches implicitly on a prefix match and takes no marker, so they get
    the plain string.
    """
    if profile.cache_breakpoint:
        return SystemMessage(
            content=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral", "ttl": CACHE_TTL},
                }
            ]
        )
    return SystemMessage(SYSTEM_PROMPT)


def _configure(name: str, profile: providers.Profile):
    try:
        return init_chat_model(
            profile.model,
            model_provider=name,
            temperature=0.3,
            max_tokens=512,  # three short cards; a bigger ceiling only adds latency
            # Fail fast rather than retrying. Measured 2026-08-23: a quota 429
            # on the Gemini free tier sent the SDK into four backoff retries and
            # one call took 40.4s, the last retry asking to wait another 58s.
            # Mid-conversation a blocked call is worse than a failed one — the
            # next turn supersedes it anyway, and with a chain behind it a fast
            # failure is what lets the next vendor get a turn inside the budget.
            # 10s is Gemini's enforced minimum deadline — it rejects anything
            # shorter with a 400, so this is a ceiling on a stuck call, not a
            # target; the design budget is still 1-2s.
            max_retries=int(os.environ.get("GLOSS_MAX_RETRIES", "1")),
            timeout=float(os.environ.get("GLOSS_TIMEOUT_S", "10")),
            **profile.kwargs,
        )
    except Exception as exc:  # missing key, unknown provider, bad kwarg
        raise SystemExit(
            f"Cannot configure {name}:{profile.model} — {type(exc).__name__}: {exc}\n"
            f"Set one of {', '.join(profile.key_env)} (from {profile.key_url}), "
            "or change GLOSS_PROVIDER / GLOSS_FALLBACKS. Known providers and "
            "their pinned models are in providers.py."
        ) from exc


def make_link(name: str, profile: providers.Profile):
    """One vendor, rendering its own prompt shape at invoke time.

    `.with_fallbacks()` hands every link the *same* input, but the two vendors
    need different message shapes — Anthropic wants `cache_control` blocks and
    DeepSeek takes no marker at all. So a link is not a model, it is
    render-then-call: the input is ignored and each link builds its own messages
    from the shared transcript when it runs. That is what keeps the two-shape
    branch confined to `system_message_for()` and `build_turn_message()` instead
    of leaking into enrich(), which § "Fallback chain" named as the price of the
    hybrid and the place to pay it.

    The answering provider is stamped onto the result. `with_fallbacks()` does
    not report which link won, and a chain that has silently degraded for an
    entire conversation is otherwise invisible.

    A link that fails for a reason that cannot resolve itself — a revoked key, a
    dead balance — is retired for the rest of the session. Retrying it every turn
    cannot succeed and costs a round trip of latency on the one path with none to
    spare. Transient reasons never retire a link: a rate limit clears in seconds,
    and demoting the primary for an hour over a blip would be worse than the
    blip. See failures.TERMINAL.
    """
    if name == "fake":
        log.warning("GLOSS_PROVIDER=fake — enrichment is canned, no model will be called")
        model = _FakeLLM()
    elif name == "local":
        model = _Local()
    else:
        # include_raw keeps the underlying message alongside the parsed cards,
        # so the token/cache counters in enrich() stay observable. Without it
        # the parsed dict is all you get and the cache diagnostics never fire.
        model = _configure(name, profile).with_structured_output(
            CARD_SCHEMA, include_raw=True
        )
    system = system_message_for(profile)

    label = f"{name}:{profile.model}"
    retired: dict[str, failures.Reason] = {}

    async def run(_ignored: object) -> dict:
        if reason := retired.get("reason"):
            # Skipped, not called. Raised rather than returned so the chain
            # moves straight to the next link.
            raise ProviderUnavailable(label, reason, "retired earlier this session")
        try:
            result = await model.ainvoke([system, build_turn_message(profile)])
        except LookupError as exc:
            # The glossary simply does not cover this turn. Not a vendor
            # failure, and not this link's reason to report — the chain fell
            # this far because something above it broke, and that is the thing
            # worth putting on screen.
            failure = ProviderUnavailable(label, failures.Reason.UNKNOWN, str(exc))
            _turn_failures.append(failure)
            raise failure from exc
        except Exception as exc:
            reason = failures.classify(exc)
            detail = str(getattr(exc, "message", "") or exc)[:200]
            if reason not in failures.FAILOVER:
                raise
            if failures.is_terminal(reason):
                # Loud, and once. This one does not fix itself and the person
                # running gloss is the only one who can fix it.
                retired["reason"] = reason
                log.error(
                    "%s retired for this session — %s: %s",
                    label,
                    reason,
                    detail,
                )
            else:
                log.warning("%s unavailable — %s: %s", label, reason, detail)
            failure = ProviderUnavailable(label, reason, detail)
            _turn_failures.append(failure)
            raise failure from exc
        return {**result, "provider": name, "model": profile.model}

    return RunnableLambda(run, name=f"link:{name}")


LINKS = [(name, profile, make_link(name, profile)) for name, profile in LINK_PROFILES]
PRIMARY = LINKS[0][0]
chain = LINKS[0][2]
if len(LINKS) > 1:
    chain = chain.with_fallbacks(
        [link for _, _, link in LINKS[1:]],
        exceptions_to_handle=(ProviderUnavailable,),
    )
    log.info("Chain: %s", " → ".join(f"{n}:{p.model}" for n, p, _ in LINKS))


# Failures raised by each link during the current turn, oldest first.
#
# `with_fallbacks()` re-raises only the LAST link's exception, which is the
# wrong one to show: the chain reaches its final link precisely because
# something above it broke, and "the glossary does not cover this turn" is not
# what a person needs to read when both vendors are down.
_turn_failures: list[ProviderUnavailable] = []

# Ids of cards painted from the glossary before the model answered. Held so the
# ones the model does not confirm can be taken back down: a provisional card
# left standing beside the model's own card on the same topic is the
# duplication the card lifecycle exists to prevent.
_provisional: set[str] = set()

transcript: list[tuple[str, str]] = []
displays: set[websockets.ServerConnection] = set()
_inflight: asyncio.Task | None = None
# Held because asyncio keeps only a weak reference to a running task: a
# fire-and-forget preview can be garbage collected mid-send.
_preview_task: asyncio.Task | None = None
_logged_cache_stats = False
# Which link answered last, so a change of provider mid-conversation is
# reported once rather than every turn or not at all.
_answered_by: str | None = None


async def broadcast(payload: dict) -> None:
    if not displays:
        return
    message = json.dumps(payload)
    # Snapshot: a send failure mutates `displays` via the handler's finally.
    await asyncio.gather(
        *(ws.send(message) for ws in list(displays)), return_exceptions=True
    )


ASK = "Cards for the final [interviewer] turn only."


def evict_if_needed() -> None:
    """Drop the oldest turns if the transcript outgrows its ceiling.

    Oldest-first, because the newest turn is the one cards are about. Logged at
    warning level rather than debug: this both loses conversation the tool was
    built to remember *and* invalidates the cached prefix from that point on,
    so the next turn is slower and dearer for a reason nothing else would show.
    """
    def size() -> int:
        return providers.estimate_tokens("\n".join(t for _, t in transcript))

    # `len(transcript) > 1`, not `transcript`. The newest turn is the question
    # the cards are about, and the earlier form would evict it too: a single
    # turn larger than the whole ceiling emptied the transcript, and the model
    # was then asked for "cards for the final [interviewer] turn" with no turn
    # in the prompt at all. That produces nothing, costs a call, and says
    # nothing about why.
    while len(transcript) > 1 and size() > MAX_TRANSCRIPT_TOKENS:
        who, text = transcript.pop(0)
        log.warning(
            "Transcript over %d tokens — dropped oldest turn [%s] %.40s… "
            "(cache prefix invalidated from here)",
            MAX_TRANSCRIPT_TOKENS,
            who,
            text,
        )

    # Degrade by carrying fewer turns, never by carrying none. One turn over
    # budget is kept and reported: the pass is worth making on an oversized
    # turn, and the operator needs to know the ceiling is not doing what they
    # set it to do.
    if transcript and size() > MAX_TRANSCRIPT_TOKENS:
        log.warning(
            "A single turn is ~%d tokens, over the %d-token ceiling on its own "
            "— keeping it, because a pass with no turn in it can only produce "
            "nothing. Raise GLOSS_MAX_TRANSCRIPT_TOKENS if this recurs.",
            size(),
            MAX_TRANSCRIPT_TOKENS,
        )


def build_turn_message(profile: providers.Profile):
    """The user half of the prompt, shaped so the conversation itself caches.

    One content block per turn, appended and never re-merged, with the ASK in a
    block of its own at the end. That shape is not stylistic — it is the only
    one that caches, and two shapes that looked equivalent were measured
    failing before this one:

    * **ASK glued to the newest turn.** When that turn rolls into history on
      the next request it no longer carries the suffix, so the bytes differ and
      the prefix match dies at that point.
    * **History re-joined into one growing block.** Turn N sends
      `[history_N][newest_N]`; turn N+1 sends `[history_N + newest_N][newest]`.
      The text is nearly identical and the *block structure* is not, and the
      cache key covers structure.

    Both failed the same way and it is a quiet failure: every lookup falls
    through to the system breakpoint, so `cache_read` sits at exactly the
    prefix size looking entirely healthy. The only thing that distinguishes
    "caching the conversation" from "caching the prep pack and nothing else"
    is whether that number *moves* as the conversation grows.

    Breakpoints ride the last two turns. At turn N+1 the second-to-last block
    is turn N, whose prefix is byte-identical to what turn N marked as its
    last, so it hits. Older breakpoints are dropped rather than accumulated —
    Anthropic allows four and the system prefix already holds one.
    """
    evict_if_needed()
    if not transcript:
        return HumanMessage(ASK)

    if not profile.cache_breakpoint:
        history = "\n".join(f"[{who}] {text}" for who, text in transcript)
        return HumanMessage(f"{history}\n\n{ASK}")

    blocks: list[dict] = [
        {"type": "text", "text": f"[{who}] {text}"} for who, text in transcript
    ]
    for block in blocks[-2:]:
        block["cache_control"] = {"type": "ephemeral", "ttl": CACHE_TTL}
    # Constant, and last on purpose: it sits after every breakpoint, so it
    # never enters a cached prefix and never invalidates one.
    blocks.append({"type": "text", "text": ASK})
    return HumanMessage(content=blocks)


async def preview(text: str) -> None:
    """Paint what is already known locally, before asking anyone.

    The chain answers in 1.1-1.8s. Two in-memory indexes answer in
    microseconds. On a live call that gap is the difference between reading a
    card while the interviewer is still talking and reading it after you have
    already had to respond.

    **An earlier version of this docstring said a `recall` card was "the one
    only a model can write", and that is no longer true.** It was true while
    the only local corpus was a shelf of books, where a retrieved passage would
    have been a plausible lie about the person's own notes. Indexing the prep
    pack instead removes the objection rather than tolerating it — a recall
    card quotes what the person wrote, and recall.py can only ever quote. So
    both kinds paint here now.

    **This still does not replace the model call.** The local indexes answer
    from words that literally appear; the model answers from what was meant.
    So the pack paints first and the model supersedes it: same slot, better
    text, no flash. That is what the card lifecycle was built for.

    It buys latency, not tokens. The model is asked either way.
    """
    if not PREVIEW:
        return
    # Recall first, for the same reason it is first in `_Local`: during the
    # 1.5s before the model answers, the note the person wrote for this call is
    # what they most want to have already been reading.
    cards = [c for c in (NOTES.card(text), GLOSSARY.card(text)) if c is not None]
    if not cards:
        return
    cards = cards[:MAX_CARDS]
    _provisional.update(c["id"] for c in cards)
    await broadcast(
        {
            "type": "cards",
            "cards": [{**c, "ttl": PREVIEW_TTL_S} for c in cards],
            "max": MAX_CARDS,
        }
    )


async def enrich() -> None:
    """One Tier 1 pass over the whole conversation so far."""
    global _logged_cache_stats, _answered_by

    started = time.monotonic()
    _turn_failures.clear()
    try:
        result = await chain.ainvoke(None)
        # Inside the try on purpose: a schema mismatch is a failure that has to
        # reach the screen like any other, not an exception that escapes into
        # the task and leaves the display showing a normal quiet turn.
        if result.get("parsing_error"):
            raise ValueError(f"schema mismatch: {result['parsing_error']}")
        # Validated per card, not per batch. `required` in a schema is advice
        # to a model rather than a constraint on it — asked for zero cards,
        # Gemini 3.6 returns [{}] — and zero cards is the correct answer for
        # most turns. Failing the whole batch would put "Enrichment is down"
        # on screen during the most ordinary case there is. See cards.py.
        raw_cards = (result.get("parsed") or {}).get("cards", [])
        cards, dropped = valid_cards(raw_cards, MAX_CARDS)
        if dropped:
            log.debug("Dropped %d malformed card(s)", dropped)
        # Which link answered, named on every turn. A chain that has silently
        # degraded for an entire conversation is otherwise invisible: the cards
        # keep arriving and only the bill and the cache counters change. The
        # transition itself gets a warning, because it happens once and is the
        # moment someone would want to know.
        answered = result.get("provider", "unknown")
        if answered != _answered_by:
            log.warning(
                "Enrichment now answered by %s:%s (was %s)",
                answered,
                result.get("model", "?"),
                _answered_by or "nothing yet",
            )
            _answered_by = answered
    except asyncio.CancelledError:
        raise  # superseded by a newer turn — expected, not an error
    except Exception as exc:
        # A failed call must never look like a quiet turn. Zero cards is the
        # normal state here, so a silent failure is invisible for the rest of
        # the call — the display has to say so out loud.
        log.exception("Tier 1 enrichment failed")
        # `with_fallbacks` re-raises the LAST link's exception, so when the whole
        # chain is out this is the final vendor's reason — which is the one worth
        # showing. A person glancing at a second screen mid-conversation can act
        # on "out of credit"; they cannot act on "APIStatusError".
        if isinstance(exc, ProviderUnavailable):
            # The first link to fail for a recognisable reason is the one that
            # explains the turn. Falling back to the raised exception covers
            # the case where every reason was unrecognised.
            named = next((f for f in _turn_failures if f.reason is not failures.Reason.UNKNOWN), exc)
            reason, detail = named.reason, named.detail
        else:
            reason = failures.classify(exc)
            detail = f"{type(exc).__name__}: {exc}"
        await broadcast(
            {
                "type": "cards",
                "cards": [
                    {
                        # One stable id for every enrichment failure: a second
                        # failure updates the card in place rather than stacking
                        # a pile of them, and a changed reason rewrites it.
                        "id": "enrichment-error",
                        "kind": "error",
                        "label": failures.HEADLINE[reason],
                        "detail": detail[:120],
                        "ttl": ERROR_TTL_S,
                    }
                ],
                "max": MAX_CARDS,
            }
        )
        return

    if LOG_CACHE or not _logged_cache_stats:
        # Once per run by default, every turn under GLOSS_LOG_CACHE=1. Caching
        # is silent on every provider — a prep pack under the provider's
        # minimum never caches and says nothing about it (see
        # sessions/README.md), so these counters are the only evidence it is
        # working. A run with cache_read stuck at 0 is a failed run even when
        # every card is correct. Shapes differ by provider, so read defensively
        # rather than assuming any field exists.
        usage = getattr(result.get("raw"), "usage_metadata", None) or {}
        if usage:
            details = usage.get("input_token_details") or {}
            log.info(
                "Tokens: %s in=%s out=%s cache_read=%s cache_write=%s",
                answered,
                usage.get("input_tokens"),
                usage.get("output_tokens"),
                details.get("cache_read", "n/a"),
                details.get("cache_creation", "n/a"),
            )
        _logged_cache_stats = True

    elapsed = time.monotonic() - started
    log.info("[cards +%4.1fs %s] %s", elapsed, answered, cards if cards else "(none)")
    if not cards and _provisional:
        # The model read the same turn and had nothing to say about it. The
        # preview was a guess from a keyword match; the model's silence is the
        # better judgement, so the guess comes down.
        await broadcast({"type": "expire", "ids": sorted(_provisional)})
        _provisional.clear()
    if cards:
        # TTL travels with each card and the cap travels with the batch, so the
        # display holds no policy of its own. A second screen opened halfway
        # through a call is then configured by the first message it receives.
        # A preview the model did not corroborate comes down. Same id means it
        # was confirmed and is simply updated in place, with the full TTL.
        stale = _provisional - {c["id"] for c in cards}
        _provisional.clear()
        if stale:
            await broadcast({"type": "expire", "ids": sorted(stale)})
        await broadcast(
            {
                "type": "cards",
                "cards": [{**c, "ttl": CARD_TTL_S} for c in cards],
                "max": MAX_CARDS,
            }
        )


def on_turn_end(text: str) -> None:
    """Interviewer finished a turn — supersede any in-flight pass with this one."""
    global _inflight, _preview_task
    if len(text) < MIN_CHARS:
        return  # "mm-hm", "right, yeah" — turn-taking, not a question
    if _inflight and not _inflight.done():
        _inflight.cancel()  # a card about the previous question is worse than none
    # Painted first and awaited by nobody: a dict lookup and a websocket send,
    # so the model call starts in the same tick.
    _preview_task = asyncio.create_task(preview(text))
    _inflight = asyncio.create_task(enrich())


async def bridge(a_ws: websockets.ServerConnection, label: str) -> None:
    client = AsyncDeepgramClient(api_key=DEEPGRAM_API_KEY, environment=DEEPGRAM_ENVIRONMENT)
    started = time.monotonic()
    # The Deepgram SDK invokes `on_message` as a plain callback; capturing the
    # loop and going through call_soon_threadsafe is correct whether or not it
    # happens to run on the loop thread.
    loop = asyncio.get_running_loop()

    async with client.listen.v1.connect(
        model="nova-3",
        # Nova-3 only. This replaced the older `keywords` parameter and takes
        # plain terms with no weighting syntax.
        keyterm=KEYTERMS or None,
        encoding="linear16",
        sample_rate=SAMPLE_RATE,
        channels=1,
        endpointing=300,
        smart_format=True,
        tag=label,
    ) as dg:

        def on_message(message: object) -> None:
            if not isinstance(message, ListenV1Results):
                return
            alternatives = message.channel.alternatives if message.channel else []
            text = alternatives[0].transcript if alternatives else ""
            if not text:
                return
            tag = "FINAL" if message.speech_final else "final" if message.is_final else "interim"
            elapsed = time.monotonic() - started
            log.info("[%s/%s +%5.1fs] %s", label, tag, elapsed, text)

            if not message.speech_final:
                return
            transcript.append((label, text))
            # Only the interviewer's turns trigger: cards about what you just
            # said yourself are noise. The user channel still lands in the
            # window above, because it is context for the next question.
            if label == "interviewer":
                loop.call_soon_threadsafe(on_turn_end, text)

        dg.on(EventType.OPEN, lambda _: log.info("[%s] Deepgram connection open", label))
        dg.on(EventType.MESSAGE, on_message)
        dg.on(EventType.ERROR, lambda err: log.error("[%s] Deepgram error: %s", label, err))
        dg.on(EventType.CLOSE, lambda _: log.info("[%s] Deepgram connection closed", label))

        listen_task = asyncio.create_task(dg.start_listening())
        try:
            async for chunk in a_ws:
                if isinstance(chunk, bytes):
                    await dg.send_media(chunk)
        finally:
            await dg.send_close_stream()
            await asyncio.sleep(1.0)  # let trailing results arrive
            listen_task.cancel()


async def handler(a_ws: websockets.ServerConnection) -> None:
    label = a_ws.request.path.strip("/")

    if label == "display":
        log.info("[display] second screen connected")
        displays.add(a_ws)
        try:
            await a_ws.wait_closed()
        finally:
            displays.discard(a_ws)
            log.info("[display] second screen disconnected")
        return

    if label not in CHANNELS:
        log.warning("Rejecting connection on unknown path: %s", a_ws.request.path)
        await a_ws.close(code=4404, reason="unknown channel")
        return

    log.info("[%s] Laptop A connected", label)
    try:
        await bridge(a_ws, label)
    except Exception:
        log.exception("[%s] bridge failed", label)
    finally:
        log.info("[%s] Laptop A disconnected", label)


async def main() -> None:
    if not os.environ.get("DEEPGRAM_API_KEY"):
        raise SystemExit("DEEPGRAM_API_KEY is not set — put it in .env (see .env.example)")

    # Prove credentials, model id and structured-output support before the
    # conversation starts rather than on the first question. It costs one tiny
    # call per link. Refusing to start is the right failure here: discovering
    # any of this mid-conversation is the expensive case, and structured output
    # is exactly the thing most likely to differ between providers.
    #
    # EVERY link, not just the primary. An unproven fallback is not a fallback —
    # it is a second failure waiting to happen at the worst moment, and it would
    # be discovered during the outage it exists for. This is also what licenses
    # 401/402/403 to appear in FAILOVER_STATUS: once a live call has proved each
    # credential here, a credential error later can only be one that died
    # mid-conversation.
    #
    # The links are invoked exactly as enrich() invokes them, transcript and
    # all — which is empty at startup, so each sends the frozen system prefix
    # plus the constant ASK and nothing else.
    for name, profile, link in LINKS:
        if name == "local":
            # Asking it a question here would fail by design: the transcript is
            # empty, so it knows nothing, and it reports not-knowing as a
            # failure. What proves this link is that it has terms in it.
            log.info(
                "Preflight OK: local holds %d glossary term(s) and %d note section(s)",
                len(GLOSSARY),
                len(NOTES),
            )
            continue
        try:
            await link.ainvoke(None)
        except Exception as exc:
            role = "primary" if name == PRIMARY else "fallback"
            raise SystemExit(
                f"Cannot reach {role} link {name}:{profile.model} — "
                f"{type(exc).__name__}: {exc.__cause__ or exc}\n"
                f"Set one of {', '.join(profile.key_env)} (from {profile.key_url}), "
                "or change GLOSS_PROVIDER / GLOSS_FALLBACKS. Set GLOSS_FALLBACKS= "
                "(empty) to run deliberately on a single provider."
            ) from exc
        log.info(
            "Preflight OK: %s:%s answered and matched the card schema",
            name,
            profile.model,
        )

    async with websockets.serve(handler, HOST, PORT, max_size=None):
        log.info("Listening on ws://%s:%s/{interviewer,user,display}", HOST, PORT)
        log.info(
            "Tier 1: %s, notes from %s, max %s cards",
            " → ".join(f"{n}:{p.model}" for n, p, _ in LINKS),
            SESSION_DIR,
            MAX_CARDS,
        )
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
