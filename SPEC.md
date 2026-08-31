# Gloss — Spec

Personal tool. Not a product. Supersedes the `unravel` hackathon app and the
`reunravel/docs/REVIVAL_PLAN.md` 68-session plan — both are considered dead;
see "Superseded work" below.

## Name

A *gloss* is a short explanation of an unfamiliar term, written alongside the
text it explains. That is the whole product in one word.

The tool was called `interview-copilot` until 2026-08-23. The name was wrong in
both halves: "copilot" implies it answers for you, which it does not, and
"interview" named one application as if it were the category. What it actually
is, is a distributed rebuild of `unravel` — whose own README described it as
"an aid for understanding meetings and presentations... lots of jargon and
unfamiliar concepts."

## Problem

Any conversation dense with unfamiliar terms is cognitively saturating —
technical presentations, vendor calls, domain hand-offs, interviews. Two things
would help live: (1) instant recall of your own notes — stories, facts, open
questions — matched to whatever was just said, and (2) a flag when the other
side uses a term you don't recognize, so you can ask instead of bluff. Both
have to cost about one second of glance, or they're worse than not having them.

Neither one generates an answer for you. Tier 1 surfaces material you wrote
yourself, before the call, and names jargon you should ask about. The design
constraint below — no live web search — is partly what keeps it that way.

Non-goal: a live transcript panel. You'd read it instead of listening.
Non-goal: web-researched enrichment inside the live loop — see "Why not
live web search" below.

## Shape

Two machines, because the call and the assistant shouldn't compete for the
same screen or the same attention.

```
Laptop A (Windows, hosts the call, BT headphones)     Laptop B (Mac, this machine)
┌──────────────────────────┐                          ┌───────────────────────────────┐
│ WASAPI loopback ──────────────── PCM/WS ────────────→│ Deepgram (interviewer channel)│
│   (interviewer, via BT out)│                          │        ↓ text                 │
│                            │                          │   turn-end trigger            │
│ built-in mic ──────────────────── PCM/WS ────────────→│ Deepgram (user channel)       │
│   (user; keeps headset on  │                          │        ↓ text                 │
│    A2DP, not HFP)          │                          │   Tier 1: LLM + prep pack     │
└──────────────────────────┘                          │        ↓ cards (1–2s)         │
   ~40 lines, no API keys                              │        ↓ WebSocket            │
                                                        │   display.html (2nd screen)   │
                                                        └───────────────────────────────┘
```

Three files plus per-interview prep:

```
a_listener.py     ~40 lines, runs on Windows, no secrets
b_server.py       ~375 lines, runs on the Mac, holds all API keys
display.html      ~80 lines, the glanceable second screen
sessions/<name>/  prep pack for one specific interview (markdown)
```

## Why this shape (decisions already made, with reasoning)

**Two independent captures instead of a diarization model.** Loopback =
interviewer only, mic = user only. Perfect speaker separation for free,
structurally — no diarization model needed, nothing to get wrong.

**Two mono streams end-to-end, not one multichannel stream.** The loopback
device and the mic are independent clocks and drift apart over an hour;
interleaving them means fighting that drift. Two independent Deepgram
connections have no alignment problem — each is timestamped independently.
Doubles STT cost to ~$1/hr, not worth engineering around.

**Windows for Laptop A, WASAPI loopback, no virtual audio driver.** You tap
the render endpoint before it goes out over Bluetooth, so BT output doesn't
degrade the capture. Per-process loopback (Win10 2004+) can scope to just
Teams/Zoom, keeping Slack pings out of the transcript. macOS is the platform
that needs a virtual driver here — the reverse of the usual pattern — which
is one reason A is Windows.

**That is a choice about loopback, not about portability.** `a_listener.py`
runs anywhere `soundcard` does — Windows, macOS and Linux — and Laptop A is a
role rather than a platform: B's requirement is two websocket streams of 16-bit
mono PCM, so an Android phone or anything else that can capture and send that
can fill the role without running this file. What varies is only how the
interviewer's voice is obtained: native on Windows, a PulseAudio monitor source
on Linux, a virtual device such as BlackHole on macOS, and whatever the
platform offers elsewhere.

**The Bluetooth trap.** The instant anything opens the headset's mic, the
whole link drops to HFP and output collapses to ~16 kHz mono — degrading
transcription of the interviewer too, not just the user. Dodge: laptop
built-in mic for the user's voice, Bluetooth for output only. The headset
stays on A2DP.

**Hit live on the first two-laptop run, 2026-08-30, and it looked like success.**
`a_listener.py` calls `sc.default_microphone()` when `A_LISTENER_MIC_NAME` is
unset, and the default was a Bluetooth headset. The `user` channel transcribed
flawlessly the entire time — which is exactly why this is worth a tool rather
than a paragraph: the channel that degrades is the *interviewer's*, and nothing
in the run says so.

`tools/listener_setup.py` now picks both devices, flags anything whose name
looks like a headset, and requires an explicit override to use one. Name
matching is crude and is what there is: no OS exposes "opening this device will
renegotiate your link codec" as a property. It also writes
`A_LISTENER_SPEAKER_NAME`, added the same day, because the loopback output had
been hardcoded to `sc.default_speaker()` — and on a machine with a headset, a
monitor and built-in speakers, the default is whatever was plugged in last.

**Python capture script on A, not browser capture.** `getDisplayMedia`
needs an active screen-share to get Windows system audio, which lights the
"you are sharing your screen" indicator for the whole interview. A
background process is invisible.

**Laptop A ships raw PCM; Laptop B brokers every cloud call.** Keeps A as
light as possible and keeps every secret on B. Changing STT or LLM
providers never touches A.

**Cloud STT (Deepgram Nova-3) over local Whisper, on this hardware.**
- Jargon is the entire product, and it's Whisper's weakest spot at sizes
  that fit the latency budget on an i9 with no useful GPU. `base.en` is
  the largest model inside budget (~1.5–2s for a 15s utterance;
  `small.en` runs 5–7s, too slow) and mangles proper nouns — "GRIFFON" →
  "griffin", "SGP4" → "SGP four".
- Deepgram supports **keyterm prompting** — feed it domain terms up front,
  it biases recognition toward them. No local equivalent at this speed.
- Whisper is architecturally batch (fixed 30s windows); "streaming
  Whisper" is always sliding-window-and-stitch, and it hallucinates on
  short/silent segments — which a turn-segmented design feeds constantly.
- Cost is nil either way: ~$1/hr for two channels of STT, ~$1–2/hr for the
  LLM tiers.
- **This is hardware-contingent, not a fixed position.** On an M-series
  Mac, on-device `parakeet-mlx` or `whisper-large-v3-turbo` under MLX
  would likely flip this. Revisit if the laptop changes.

**Trigger on the interviewer's turn-end, not continuously.** Deepgram's
`speech_final` on their channel only. That's the moment a question has
landed and there's a beat before answering — exactly when a card is useful
and exactly when there's a second to glance. ~15–25 calls/hour instead of
hundreds.

**Tier 0 (prepared-context retrieval) is not a separate tier.** Originally
planned as an instant lookup layer against your prep docs. Collapsed into
Tier 1: put the whole prep pack in the cached system prompt and let the
model do the matching as part of its normal job. No
retrieval layer, no index, no embeddings — and it handles paraphrase
("tell me about influencing without authority" → `Story 5`), which a
keyword index can't. ~$0.0015/call on cached reads.

**Suppress rendering while the user's own mic channel is active.** Queue
and release when they stop talking, so a card never lights up mid-answer
and pulls focus.

**Why not live web search (Tier 2 research).** A 5–30s LLM-with-web-search
call blows the "topic stays live for 30–90 seconds" budget outright. Live
enrichment is capped at Tier 1 (fast model, no tools, prep pack + light
jargon flagging). Web-grounded research becomes a **post-call export**
instead — the model emits search queries, the backend resolves real URLs,
never trust model-invented links (the one idea kept from the old
`REVIVAL_PLAN.md`). A study-guide artifact generated after the call, not
during it.

## Display

Three content cards maximum, each staying until it is dismissed — clicked, or
`Backspace`/`Delete`/`x` on the clicker — or until the cap pushes it off. No
clock, and no transcript.

New topics **append**; a topic already on screen updates **in place**, keeping
its position, so the eye does not have to re-find a card it has already located.
(The original sketch pinned the newest to the top; per-card stable position
turned out to serve the same goal better — see § Card lifecycle.)

```
┌────────────────────────────────────┐
│ your notes                         │
│ Integration platform               │  recall — quoted from the prep pack
│ Camel/MuleSoft buy-vs-build        │
├────────────────────────────────────┤
│ unfamiliar term                    │
│ GRIFFON                            │  jargon — define it, don't answer it
│ their internal deploy gate         │
└────────────────────────────────────┘
```

**The card contract is defined once, in `cards.py`, as a pydantic model.** The
JSON schema sent to the provider is derived from it and the same model validates
what comes back, so there is nothing to keep in sync — and this document is not
the source of truth for it. As of 2026-08-25:

```json
{"cards": [{
  "id":     "kestrel",              // topic slug; optional, derived from label
  "kind":   "recall | jargon",      // `error` is server-authored, never a model's
  "label":  "≤ 6 words",
  "detail": "≤ 25 words"
}]}
```

On the wire to the display each card also carries `ttl` (seconds) and the
batch carries `max` — so lifecycle is the server's policy and a display opened
mid-call is configured by the first message it receives. **A `ttl` of `0` means
no clock: the card stays until it is dismissed**, which is what content cards
now send. Status and preview cards still send a real one.

Phase 4 § HUD mode revisits this display as a heads-up surface rather than a
second screen — form and position carrying meaning before any word is read, and
ultimately a transparent overlay rather than a monitor across the desk. The
rules below survive that change; they are about what earns a place on screen,
not about where the screen is.

**Noise-control rules** (these decide whether the tool gets used or
ignored, so treat them as load-bearing, not polish):
- Empty is the normal answer. The Tier 1 prompt must say this explicitly,
  or it will invent something to say on every turn.
- Never repeat a label already shown this session (normalized seen-set).
- One card per turn, maximum.
- Suppress while the user's own channel is active (above).

## Phasing

**Phase 1 — prove the pipe. Nothing else.** User's own scope, quoted
verbatim: *"The app that runs on laptop a and sends audio without
interrupting it for processing and text to laptop b... Then with that
what kind of processing and such I will figure out what needs to happen
from that."*

1. `a_listener.py` (Windows) — two WASAPI capture loops via `soundcard`
   (`include_loopback=True` on the default render device for the
   interviewer; built-in mic for the user), float32→int16, each shipping
   raw PCM over its own WebSocket to B. No API keys on A.
2. `b_server.py` (Mac) — accept both PCM sockets, relay each to its own
   Deepgram streaming connection (`nova-3`, `linear16`, mono,
   `endpointing`), emit labeled transcript lines. Sink is stdout or a
   plain text log — enough to prove the pipe, no display yet.
3. Prove: interviewer audio and user audio arrive on B as two
   correctly-labeled text streams, with measured end-to-end latency.

**Open question to settle before writing Phase 1 code:** stand up a real
Deepgram account, or build Phase 1 against a stub/local transcriber first
so the pipe is provable before signing up for anything?

**Phase 2 — Tier 1 enrichment.** Turn-end trigger → LLM + prep pack →
at most 3 cards → `display.html`. Specced in detail below.

**Phase 3 — make it survive a real call. Complete 2026-08-25.** Caching so cost
does not grow with the conversation, a provider chain so one vendor being out is
not the end of the call, cards that persist while their topic is live, a local
glossary as the chain's floor, and an instant preview in front of it. Sessions
S1–S6 and their measured results are in `PHASE-3-PLAN.md`.

**Phase 4 — HUD mode and a hardware control. Complete 2026-08-27.** 4a
(clicker control) built 2026-08-25; 4b (HUD mode) measured 2026-08-27 and then
built as a mode of `display.html`, behind `?mode=wheel`.

Phase 4 deliberately ends on the screen it started on. The HUD is a **tightened
proof of concept on the current second screen** — the display it was measured
on, the display it ships on, and enough to answer whether the form works at all
before anything is spent on the window it eventually wants. The transparent
overlay that § 4b argues toward is real work in a different discipline (a native
window shell, not a page) and is **Phase 5**, below.

**Phase 5 — the overlay window. Not started.** Take the same
`display.html?mode=wheel` and give it a window that can sit over the call
instead of beside it: transparent, frameless, above a full-screen call,
click-through, and excluded from screen capture. Nothing in the page changes;
the whole phase is the shell around it. Specced in § Phase 5 below, including
why capture exclusion is the requirement that decides which shell, and the
prerequisite that a glance-test run first produce a readable fact column —
runs 3 and 4 have both been run by a human and both are void (§ 4b). Phase 5 is
a bet on the HUD form, and that bet should not be placed on an unmeasured
display.

#### 4a. Hardware control — built 2026-08-25

A Bluetooth presenter clicker as a card controller. During a call, reaching for
a keyboard is more disruptive than the glance itself.

**No local process, contrary to what this section originally said.** A presenter
is an HID keyboard — it sends PageUp/PageDown or the arrow keys and usually one
"blank screen" key. `display.html` listens for `keydown`, which is all a clicker
was ever going to produce. No new transport, no OS permissions, nothing to
install.

| key | effect |
|---|---|
| `→` `PageDown` `space` | select next card |
| `←` `PageUp` | select previous card |
| `Enter` `p` | pin / unpin the selected card |
| `Backspace` `Delete` `x` | dismiss the selected card |
| `b` `.` `F5` | blank / unblank the whole display |
| `Esc` | clear the selection |

Presenters disagree about what they emit, so several keys map to each action.
The first press selects the newest card, which is the one most likely to be
acted on.

**Pin protects a card from eviction.** It was written when cards cleared
themselves on a 90-second clock, and it meant "ignore the clock"; since cards
now stay until dismissed, what is left for it to protect against is the cap —
a pinned card is the last thing pushed off when a fourth topic arrives. A card
that still carries a TTL (status, preview) also has it suspended while pinned,
and a pin that came undone because the server sent the same card again would be
worse than no pin.

**Blank is the second most useful and was not in the original sketch.** Someone
walks up; one button and the screen is empty. It hides rather than pauses:
timers keep running, so unblanking does not reveal a conversation that has moved
on.

**The cap stays absolute.** Pinned cards are evicted last, but a screen that
grows without limit stops being readable, which is the one thing it cannot
afford to be. Pinning everything is a choice to fill the screen, not a way to
make it bigger.

One bug worth recording because it was caught by a test rather than by reading:
preferring unpinned cards for eviction isolated the card that had *just
arrived* when everything else was pinned, so a fully pinned screen silently
stopped showing anything new — fine-looking and stale. Eviction now never
removes a card from the batch being rendered.

**The limitation.** This works because the display is a dedicated second screen
with nothing else focused. On a machine where the video call has focus, the keys
go to the call instead. That case needs a real global-hotkey listener — an
accessibility permission on macOS — and is deliberately not built: the dedicated
second screen is the documented setup, and a keystroke that lands in someone
else's video call is a worse failure than a feature that is absent.

#### 4b. HUD mode — glanceable, not readable

**The framing.** A heads-up display exists so information can be taken in
*without leaving the primary task*. Aviation and automotive HUDs are not about
reading faster; they are about not looking away. gloss's primary task is the
conversation, and the design target is the same: what can be integrated into the
visual field closely enough that consulting it is not a departure from the call.

That reframing changes the question. "How do I read this faster" is the wrong
one and has a poor answer. **"How much of this needs reading at all"** is the
right one, and has several.

**Direction 1 — encode in form, not words.** A card already carries meaning
before a word is read: `kind` is colour-coded, and Phase 3 fixed cards in place
so position is stable. That is unexploited. Recency, confidence, and whether a
term is from the prep pack or the glossary could all be positional or chromatic.
A glance that yields "there is a jargon card and I already know what it says"
costs nothing.

**Direction 2 — one word beats six.** The `label` budget is six words. For a
HUD, the target is one or two: the term itself, with `detail` available only if
the eye lingers. Progressive disclosure by dwell rather than by layout.

**Direction 3 — motion as a cue, not as a reading mode.** Movement is excellent
at recruiting attention and poor as a way to deliver text. A card that *arrives*
with motion and then holds still uses motion for what it is good at.

**What is known before building, so it is not rediscovered:**

- **Moving text is harder to read than static text.** Marquee-style horizontal
  scrolling measurably degrades comprehension. The better-studied variant is
  RSVP — one word at a time in a fixed position — which preserves reading speed
  but removes the ability to re-read. Re-reading is what a one-second glance
  depends on, so RSVP trades away the thing that makes a card work. This is why
  Direction 3 above uses motion for arrival rather than for delivery.
- **A card is already close to one fixation.** Six words is one or two. There is
  little traversal left to remove, which caps the benefit available from any
  scrolling scheme and is the strongest argument for Direction 2 instead.
- **Cards already hold position.** The Phase 3 lifecycle updates a card in place
  rather than rebuilding the list, so the eye does not re-find a card it has
  already located. That was the larger source of eye movement and it is gone.
- **Duration off-task matters more than eye path.** What makes consulting a
  second screen a departure from the call is how long attention is elsewhere,
  not the shape the eye traces while it is there. Every direction above shortens
  duration; scrolling text lengthens it.

**Measure before building — the instrument exists, built 2026-08-27.**
`tools/glance_test.html`, opened fullscreen on the second screen at the
distance you actually sit. No server and no dependencies; it is one file.

Four conditions, each rendered with the card CSS copied out of `display.html`
rather than approximated, because the size on that screen is the variable
being measured:

| condition | what is shown |
|---|---|
| `today` | six-word label + detail line — what ships now |
| `one` | the term alone, at label size |
| `scroll` | label + detail as one marquee line, 160 px/s |
| `rsvp` | label + detail, one word at a time in place, 300 wpm |

**Two probes per trial, and the second is the one that decides 4b.** Press
SPACE when the card has been taken in, then answer *which term was on screen*
and *what did it say*. Without them this measures when somebody pressed a key,
which is self-report rather than comprehension. `one` should be fastest on
reaction time and at chance (50%) on content — that trade is the result, and an
instrument that only timed the glance would hide it and make one-word look
free.

Fatigue and anticipation are designed out rather than hoped about: four
practice trials that are not recorded, a jittered fixation so the onset cannot
be predicted, conditions balanced by construction and then the whole sequence
shuffled, and every trial drawn from a different one of the 32 items so nothing
is answerable from having seen the same card under another condition.

**Reading the result.** If `one` is fastest *and* holds content accuracy near
chance, that is not a failure — it says the term alone is enough to answer
"there is a jargon card and I already know what it says", and detail belongs
behind dwell (Direction 2). If `one` is fastest *and* content stays high, the
detail line was never being read anyway and can go. If `today` wins outright,
Direction 2 is wrong and the six-word label stays. `scroll` and `rsvp` are in
the set to be ruled out on this screen rather than on the literature alone.

The instrument was driven end to end in Chromium before anyone was asked to
sit through it — balanced trials, distinct items, both probes, parseable JSON
out. What it has not yet had is a human running it, which is the point.

**Result — run 1, 2026-08-27, one participant, 7 trials per condition, 71 cm,
1680×1050 at dpr 2.**

| condition | responded | median RT | median, censored | identity | content |
|---|---|---|---|---|---|
| `one` | 6/7 | 2880 ms | **3011 ms** | 100% | 100% |
| `rsvp` | 6/7 | 4884 ms | 5036 ms | **57%** | 71% |
| `today` | 4/7 | 6553 ms | 7999 ms | 100% | 86% |
| `scroll` | **0/7** | — | ≥8000 ms | 86% | 86% |

*Censored* holds a timeout at the 8s ceiling instead of dropping it. Dropping
flatters the slow conditions, because the trials that could not be finished are
exactly the ones that made them slow — `today` loses 3 of 7 that way and
`scroll` loses all 7.

**`one` wins, by 2.3× on responders and 2.7× censored, with no accuracy cost.**
That is the largest effect in the run and it is the one 4b turns on. Direction 2
is confirmed: the term alone is what the card is for, and detail belongs behind
dwell.

**`scroll` did not lose on time — it never finished. 0 of 7.** And yet identity
and content both scored 86%, so the text was being taken in. The participant
could answer about the card and still never reached a moment of having taken it
in, because a marquee loops and so has no end. Motion as a *delivery* mode
removes the endpoint that a glance depends on. Direction 3 stands exactly as
written: motion for arrival, never for delivery.

**`rsvp` fails on the one thing a card must do — it loses the name.** Identity
57% against a 50% chance floor, while content held 71%. The term is one word in
thirty with no chance to re-read, so the term is what gets lost. That is a
sharper reason to drop RSVP than "slower", and it is a reason that would not
have appeared without the identity probe.

**Two things the run said that were not being asked.**

*The one-second glance is not achievable at this size and distance.* The fastest
condition's median was 2.9 seconds. Some of that is the instrument — SPACE is
pressed when the participant is *confident* they have taken it in, which is
later than when they had — so treat these as a ceiling rather than an estimate
of perception. But no condition came close to a second, and § 4b's framing
("about one second of glance, or they're worse than not having them") should be
read as a design aspiration that is currently unmet, not a describing figure.

*The content probe is confounded for vocabulary the participant already knows.*
`one` scored 100% on content having displayed no content at all. It shows the
term; the probe then offers "allowed unreliability" against "measured failure
count", and anyone who knows what an error budget is answers correctly from
their own knowledge. So content accuracy is evidence about the card **only for
terms the participant does not already know** — which is the case gloss exists
for, and the case this run did not measure. The prediction recorded above that
`one` would sit at chance on content was wrong for this reason rather than
because one word delivers detail.

**Confidence, stated honestly.** n=7 per condition, one participant, one
session. `one` over `today` (2.7×) and `scroll`'s 0/7 completion cliff are large
enough to act on. `rsvp`'s identity result is 4/7 and is one or two trials from
noise — directionally believed, not established. Direction 1 (form, colour and
position) is untouched by this instrument and neither confirmed nor refuted.

**Run 2, 2026-08-28 — a second participant with no engineering background, who
took the terms to be invented.** 7 trials per condition, 75 cm, same screen.
This is the participant run 1 could not be: someone for whom every term really
is unfamiliar, which is the case gloss exists for.

| condition | responded | median RT | median, censored | identity | content |
|---|---|---|---|---|---|
| `one` | 6/7 | 3160 ms | **3230 ms** | 100% | 86% |
| `rsvp` | 7/7 | 4860 ms | 4860 ms | 86% | 86% |
| `today` | 1/7 | 5174 ms | 8000 ms | 100% | 100% |
| `scroll` | **0/7** | — | 8000 ms | 100% | 100% |

Across both participants:

| condition | responded | censored median | ordering |
|---|---|---|---|
| `one` | 12/14 | 3011 / 3230 ms | fastest, both |
| `rsvp` | 13/14 | 5036 / 4860 ms | second, both |
| `today` | 5/14 | 7999 / 8000 ms | third, both |
| `scroll` | **0/14** | ≥8000 ms | never completed, both |

**What replicated.** `one` is fastest for both people and the two medians are
within 7% of each other — 3011 ms and 3230 ms — from two people with completely
different domain knowledge. `scroll` was completed **zero times out of
fourteen**. The full ordering is identical across participants. Direction 2 and
the death of marquee-as-delivery are as established as this instrument can make
them.

**Retraction 1: "`rsvp` loses the name" does not replicate.** Run 1 measured
57% identity; run 2 measured 86%, and the naive participant responded on 7 of 7
with no timeouts. Run 1 labelled that result "one or two trials from noise,
directional not established", and it was right to. It is withdrawn. RSVP is
still second-slowest in both runs, which is reason enough not to build it, but
not for the reason previously given.

**Retraction 2, and it is the important one: the accuracy probes do not measure
comprehension, because a timeout grants maximum exposure.** On timeout the card
stays on screen for the full 8-second ceiling, so a trial nobody could finish is
also the trial with the longest look at the card. The naive participant's
*highest* scores — 100% identity and 100% content — are on `today` and `scroll`,
the two conditions she completed 1 of 7 and 0 of 7 times. Accuracy runs
**inversely** to response rate, which is what a metric does when it is measuring
exposure rather than legibility.

That withdraws run 1's reading of `scroll` — "identity and content both scored
86%, so the text was being taken in". It was being taken in over eight seconds.
The 0-of-14 completion result stands on its own; the inference about
comprehension does not.

**New finding: the gist probe is answerable from the term alone.** A
participant who believed the vocabulary was invented still scored 6 of 7 on
content in the `one` condition, which displays no content. "Leader election"
implies "one node holds a lease" without the card saying anything. So the probe
measures inference from the term plus recognition memory — not what the card
delivered. Recorded in `tools/cards_corpus.js` beside the data it describes.

**What this leaves standing.** `one` is the fastest and among the most
completable conditions for both participants; `scroll` is unusable; `today`
times out for 9 of 14 trials. Those rest on response rate and reaction time,
neither of which is affected by the exposure confound. Everything that rested
on the accuracy columns is withdrawn.

**Run 3 needs a different instrument, not more trials — built 2026-08-28,
`tools/exposure_test.html`.** Self-terminated exposure was the flaw: it
conflates "how long until you had it" with "how long you chose to look", and it
hands the slowest conditions the longest look. So exposure is now fixed. The
card is shown for exactly N ms, removed, masked, and then probed. Every
condition gets the same N on a given trial, there is no timeout category, and
accuracy is a statement about legibility again. It also asks the question the
HUD actually poses — *how long does this have to be on screen* — rather than
*how long did you choose to look*.

| | run 1–2 (`glance_test.html`) | run 3 (`exposure_test.html`) |
|---|---|---|
| exposure | participant decides, 8s ceiling | fixed: 150 / 350 / 800 / 2000 ms |
| after the card | straight to the probe | 250 ms glyph mask |
| conditions | today, one, scroll, rsvp | today, one, **column** |
| detail probe | gist pair | an arbitrary specific — a number, a schedule |
| headline metric | reaction time | % correct at each exposure |

**Three design decisions inside it, each answering a way the last one failed.**

*Only static conditions.* `scroll` and `rsvp` are gone, and not because they
lost. Exposure duration is not a meaningful independent variable for a display
whose content unfolds over time: a marquee at 150 ms is a fragment of one word
and RSVP at 150 ms is one word of thirty. That is not a shorter version of the
condition, it is a truncation of it. Both were also settled well enough by runs
1–2 — `scroll` was completed 0 times in 14.

*A mask after every exposure.* Without one, a 150 ms presentation is still
readable off the afterimage, and the short durations would measure iconic
memory rather than the display.

*A built-in control on the probe.* `one` and `column` display **no detail at
all**, so both must land near 50% on the detail probe. If either is well above
chance, the probe is leaking and the detail numbers cannot be read. That is
precisely the failure that ended the previous instrument, and it is now
detectable from inside the results rather than by noticing it a run later.

**The corpus changed with it.** `gist` probes are retired — run 2 proved them
answerable by inference — and replaced with `fact` probes whose answer is
arbitrary: *raised twice this quarter* against *raised once last year*, *3 of 5*
against *2 of 7*. No amount of domain knowledge recovers those; the only way to
have one is to have read it. Each `detail` line was rewritten to carry one such
specific, which means **`today`'s stimulus changed after run 2 and run 3 is not
comparable to runs 1–2 on detail-dependent measures.** A valid probe was worth
more than comparability with a condition that was already losing.

**Verified before anyone runs it,** the same way the last one was: 24 trials
across 12 cells at n=2, 24 distinct items, masks shown, and — measured —
exposure drift of 0–17 ms with a median of one frame at 60 Hz and no exposure
more than two frames late. Two bugs were caught doing it. `column` was
rendering a detail line, which would have made it *`today` plus neighbours*
rather than *`one` plus neighbours* and destroyed the control described above.
And neighbouring rows were drawn from the whole corpus, where three of the
term distractors (`Backfill`, `Backpressure`, `Load shedding` — deliberately
confusable near-misses) are themselves corpus terms, so a neighbour could have
displayed the wrong answer and turned the identity probe into a coin flip about
which row a word had been on. Neighbours now exclude the trial's own
distractor; 0 leaks in 6,400 draws.

**What run 3 can and cannot settle.** At 2 repeats per cell it is 24 trials and
a pilot, and single cells are noise — the per-condition column is the readable
part. What it *can* do that neither previous run could is say whether a number
is a measurement at all, because the control conditions have to sit at chance
for the rest of the table to mean anything.

**What run 2 would fix, if 4b needs firmer ground:** raise the ceiling to ~15s
so `today` stops being censored at 3 of 7; add unfamiliar terms with
call-specific distractors so the content probe measures the card rather than the
participant; and add a second participant, since one person's reading speed is
currently the entire dataset. *(Written after run 1. Run 2 supplied the second
participant and made the middle item urgent rather than optional; the ceiling
question was overtaken by dropping the ceiling entirely — see run 3 below.)*

**What was fixed in the instrument because of this run.** `scroll` returned a
median of `0 ms` — the median of an empty list, rendered as though it were the
fastest result in the table, produced by the one condition nobody completed. The
summary now reports `—` with a responded count, holds timeouts at the ceiling,
and excludes conditions with no responses from "fastest". An instrument that
renders *no data* as *instant* fails in the direction of looking fine, which is
the direction that gets believed.

**What both participants preferred, unprompted — and why it is not the
condition that just died.** Shown the four, both picked "the single word, but
scrollable": one term per row stacked vertically, the column driven by a wheel,
about five rows visible with the focus at full opacity, its neighbours at ~85%
and the outer pair at ~45%. The stated appeal was control and discretion — the
whole surface can be dialled down to a faint column.

`tools/wheel_hud.html` is that, as a prototype. It is deliberately **not** the
`scroll` condition, and the difference is the entire design: a marquee moves on
its own and loops, so it has no endpoint and nobody ever finished one — 0 of
14. A wheel-driven column is **still until you move it, and stops where you
stop**. Motion is a cue you initiate, which is what Direction 3 asked for in the
first place.

It also sits on the intersection of what the runs support: one word per row is
the winning condition, motion is for seeking rather than reading, and the
opacity gradient is Direction 1 — **which neither run touched, so the gradient
is an untested idea in a prototype, not a result**. Detail appears under the
focus on dwell and hides the instant the focus moves, because a detail line
that survives a move is a line read about the wrong card.

Two things were measured while building it, both about holding position. The
focused row must sit at one fixed screen height: laid out in flow it drifted
16px at the ends of the list as rows stopped rendering, and animating
`font-size` between rows left the centring transform computing against a layout
still in flight, worth another 12px. Anchoring the column and dropping the size
transition leaves 5px of sub-pixel rounding. This matters more here than it
looks — a HUD whose focus row moves reintroduces exactly the "re-find the card"
cost that the Phase 3 lifecycle was built to delete.

**Where this could go.** Nothing above requires a screen at the far end of a
desk. The same cards on a transparent display — smart glasses, a monitor overlay
with the video call composited behind it — is the honest endpoint of the HUD
framing, and it makes the "looking away" problem disappear rather than disguise
it. Out of scope for Phase 4; worth knowing it is the direction of travel,
because it argues for keeping cards small, positional and form-encoded rather
than investing in cleverness about reading long text.

**Run 3 — ran 2026-08-28, 75cm, 24 trials. THE FACT COLUMN IS VOID, for the
third time and for a third distinct reason.** Raw JSON:
`tools/results/exposure-run3-2026-08-28.json`.

**The instrument's timing is finally sound**, and that is the one clean pass:
exposure drift min 0 ms, max 17 ms, median 0 — no exposure more than one frame
late at 60 Hz. Fixed exposure works. Everything below is about the probe, not
the timer.

**The pre-registered control did not clear.** § 4b's own gate was that `one` and
`column` display no detail at all, so both must land near 50% on the fact probe.
They landed at 63% and 75%; combined 11/16 = 69%, one-sided binomial p = 0.105
against chance. Underpowered, so this is neither a clean pass nor a proven leak
— and under this project's own rule a check that could not answer is not a pass.

**What killed it is visible without statistics: `today` scored 75% on fact and
`column` scored 75% on fact.** The condition that displays the detail line and
the condition that displays nothing but a term scored identically. Whatever the
participant was answering from, it was not the card.

**The mechanism, found by re-reading the corpus rather than by more trials.**
Most `factDistractor` values are absurd by an order of magnitude or contradict a
value the field already knows — `adds 40MB per pod` vs `400MB`, `relayed every
200 milliseconds` vs `every 30 seconds`, `500 rows a second` vs `50,000`,
`canary starts at five percent` vs `twenty-five`, `43 minutes a month` (99.9%)
vs `four hours`, `keys expire after 24 hours` (the Stripe default) vs `30 days`.
Classifying each pair as *asymmetric* (distractor ≥5× off, or contradicting a
known default) or *symmetric* (both operationally ordinary) splits the run
almost perfectly, **and the split does not vary by condition**:

| | asymmetric pair | symmetric pair |
|---|---|---|
| `one` (no detail shown) | 5/5 = 100% | 0/3 = 0% |
| `column` (no detail shown) | 5/6 = 83% | 1/2 = 50% |
| **controls combined** | **10/11 = 91%** | **1/5 = 20%** |
| `today` (detail shown) | 6/6 = 100% | 0/2 = 0% |

**The controls sit at 20% on symmetric pairs — which is what a control is
supposed to look like — and at 91% the moment one option is implausible.** The
probe is measuring "which of these two numbers sounds like a real engineering
number", answered from domain sense, with the card contributing nothing.

**This is the `gist` disease one level down.** `gist` was retired after run 2
because it was answerable by inference from the term's *meaning*.
`cards_corpus.js` states the replacement rule in as many words — *"the answer
must be arbitrary… Both options are the same shape and equally plausible"* — and
then does not meet it. Arbitrariness was checked against the term and not
against the distractor.

**Honesty about the classification:** asymmetric/symmetric was assigned *after*
seeing the results, which makes the 91%/20% split a hypothesis rather than a
finding. It is a strong one — the mechanism is legible by reading the pairs, and
it predicts the condition-independence that is the run's most damning number —
but the next run must pre-register the classification, not derive it.

**Identity cannot rank the conditions either: it is at ceiling.** `today` 8/8
and `column` 8/8, including both 150 ms trials each. `one` scored 6/8, and both
misses were at 150 ms, on `Consistent hashing` and on `Backfill` — the latter
being one of the corpus's deliberately confusable near-misses (for
`Backpressure`). Two trials, one of them a known trap. Not a finding, and
specifically not evidence that a bare word is harder to lock than a word in a
column, tempting as that reading is.

**So run 3 settles nothing about 4b, and settles one thing about the
instrument.** The `one` vs `today` vs `column` question is still open. The
symmetric-pair subset is a working instrument in miniature — 7 trials, controls
at chance — so **the fix is better pairs, not more trials.** Rewrite all 32
`fact`/`factDistractor` pairs so both options are equally plausible, pre-register
that judgement, and re-run the same 24 trials. Until then nothing in § 4b's
detail column may be cited, including in the story bank.

**The rewrite was done 2026-08-29, and run 4 ran that evening — its own
section follows.** All 32 pairs in
`tools/cards_corpus.js` were rewritten under one rule applied before the run —
*would an experienced engineer reject one of these two out of hand?* — and the
six symmetric pairs run 3 already contained were kept untouched as the
template. Three cards needed their `detail` moved rather than just their
distractor, because the correct answer was the field's own canonical value and
so was recoverable without the card: `Idempotency` (24 hours is Stripe's
default → 72 hours), `Canary` (five percent is the canonical first slice →
three percent, label included) and `Error budget` (43 minutes a month *is*
99.9% → 90 minutes). Run 4 is therefore not comparable to run 3 on those three
items, which costs nothing, because run 3's fact column is void. **All 32 pairs
are pre-registered symmetric in the corpus header**, so there is no post-hoc
subset this time — the control is the whole run, and the falsifier is that
`one` and `column` must land near 50%. If either clears roughly 70% the probe
is still leaking, the fact column is void for a fourth time, and Phase 5 does
not start. The corpus header also records what nothing checks: plausibility
itself is a human judgement living in a comment, not an assertion — which is
the failure mode that produced this rewrite.

**Run 4 — ran 2026-08-29, 75cm, 24 trials, rewritten corpus. THE
PRE-REGISTERED FALSIFIER TRIPPED — AND THE GATE ITSELF WAS BADLY BUILT.** Raw
JSON: `tools/results/exposure-run4-2026-08-29.json`.

| condition | shows detail? | identity | fact |
|---|---|---|---|
| `one` | no | 7/8 = 88% | **6/8 = 75%** |
| `today` | **yes** | 7/8 = 88% | 3/8 = 38% |
| `column` | no | 6/8 = 75% | 2/8 = 25% |
| **controls pooled** | no | — | **8/16 = 50.0%** |

**The falsifier as written was: if either control clears roughly 70%, the probe
is still leaking. `one` scored 75%. It tripped, so the fact column is void for a
fourth time.** That is the pre-registered reading and it stands. Writing the
threshold down before the run was precisely so it could not be argued away
after, and it is being obeyed on the run where obeying it is inconvenient.

**But the gate was under-designed, and that is this run's real finding.** At 8
trials per control a *sound* probe produces 6/8 or better 14.5% of the time, so
across two controls **the falsifier fires on a perfect instrument roughly once
in four runs** (26.8%). A threshold whose false-alarm rate was never computed is
not a gate. It fails in the other direction too: at n=8 the control check has
**37% power** to detect a real 75% leak, so a control that *passes* at this size
proves nothing either. Under this project's own rule — a check that could not
run is not a pass — this check could not answer in either direction, and the
pre-registration should have carried a trial count as well as a threshold.

**What the pairs did do.** The run-3 mechanism is absent. There, both controls
rose *together* on the asymmetric pairs (91% pooled) — which is what a leak
looks like, because it lifts every condition that can guess. Here the controls
sit on opposite sides of chance, 75% and 25%, pooling to exactly 8/16 = 50.0%.
That is the signature of noise, not of a leak, and it is the first time the
controls have pooled to chance across a whole run. Evidence for the rewrite —
but at n=16, weak evidence.

**Nothing separates the conditions.** 6/8, 3/8 and 2/8 across `one`, `today` and
`column` is χ² = 4.4 on 2 df, p ≈ 0.11. `today` at 3/8 is not distinguishable
from chance either (p = 0.36, one-sided). **The condition that displays the
detail did not beat the two that display none** — and with 8 trials per cell the
run could not have shown it if it had. No reading of the detail column is
available, including the tempting one.

**Timing, for the record: exposure drift median 16 ms, max 17.** Every exposure
ran exactly one frame long at 60 Hz, because the `requestAnimationFrame` loop
can only stop on a frame boundary. That is a systematic floor, not drift, and
run 3's median of 0 was the anomaly. At 150 ms it is +11%; it changes no reading
here, but it should be reported as what it is.

**The corpus size is now the binding constraint, and it is the thing to fix.**
`exposure_test.html` fails closed rather than reusing a card, so a session can
never exceed 32 − 3 = 29 trials, and `reps` is capped at 2. Three conditions ×
four durations therefore pins every run at 24 trials and 8 per cell, in
perpetuity. A control needs **n ≈ 28** to detect a 75% leak at 80% power
(reject at ≥ 19/28); separating conditions needs more still. **§ 4b cannot be
answered by this instrument at this corpus size, however many times it is run.**

**So run 4 settles nothing about 4b and one thing about the method:** the next
run must not be another 24-trial three-way. Split the two questions the
instrument has been asked to answer at once. First validate the probe with a
**control-only run** — `one` and `column`, one duration, 28 trials, pooled n=28,
pre-registered to reject at ≥19/28 — which needs the `reps` cap lifted and no
new cards. Only once the probe is known sound is it worth spending a
three-way comparison on it, and that comparison needs a corpus of roughly 64
cards to reach 16 trials a cell.


**Folded into `display.html` behind `?mode=wheel`, 2026-08-27.** The prototype
is now a mode of the real display rather than a file beside it. No flag is the
second screen, unchanged.

It is one page and one DOM, not two. The wheel HUD is a *rendering* of the
selection model 4a already had — `selected` is the focused row, `step()` is what
moves it, and the wheel is one more input that calls it — over the same card
nodes the Phase 3 lifecycle creates, updates in place, expires and evicts. That
lifecycle is the expensive, load-bearing part and it is orthogonal to how a card
is painted; forking it into a second display was the fastest available way to
lose it. What changes between modes is CSS, a distance attribute, and where the
detail line lives.

**The prototype's sliders did not come with it.** They existed to find the
numbers. The numbers now arrive as query params (`stealth`, `near`, `far`,
`dwell`, `wheel`), so a value can still be tried without a rebuild, but the HUD
does not ship a control panel it should never show. A parameter that will not
parse falls back to its default rather than to `NaN` — a HUD rendered invisible
because `?far=abc` became nothing is precisely the fail-open this display
exists not to have.

**Three decisions the fold forced, none of them measured.** They are choices
about a display no run has scored, and they are recorded here so they are
argued with rather than inherited:

1. **A new card takes the focus.** The HUD renders exactly one row legibly. A
   focus that stays where it was would leave the card for the question just
   asked as the dim one, and every turn would open with a wheel — which makes
   the HUD strictly worse than the second screen it replaces, where all three
   cards are readable at once. The wheel takes the focus straight back. The
   alternative (arrival is visible at `d=1` and never steals) is the more
   faithful reading of Direction 3, and is one line to switch to.
2. **The kind mark shrinks to one word** on the HUD — `term`, `yours` — because
   "UNFAMILIAR TERM" beside a one-word term is more text than the term, which is
   Direction 2 pointed backwards. Kind is carried by the border colour instead.
3. **An error row is exempt from the gradient and from the cull.** Colour is the
   first thing `--stealth` takes away, and a row hidden for being far from the
   focus looks exactly like a row that is not there. "Down" and "scrolled out of
   view" must not be the same picture, so an error row is never dimmed, never
   hidden by distance, and keeps its words. The status dot is likewise exempt
   from `--stealth`: a HUD you cannot see is a choice, a HUD that hides that it
   is disconnected is a fault.

**Nine browser tests cover the mode**, including the two properties that were
measured rather than reasoned about — the focused row holds one screen height
across a walk that wraps both ends of the list (tolerance 6px, against 5px of
known sub-pixel residual), and nothing but the column moves. One of the nine
guards the other direction: the second screen must still start with nothing
selected and no distance styling, because the cost of a mode flag is the mode
that was already working.

**What the fold did not deliver, and cannot.** Not the transparent overlay
above. A web page cannot make its own window transparent: `background:
transparent` in a tab is composited against the browser's own opaque base, and
there is no web API for window transparency, always-on-top or click-through.
`--stealth` fades content against a solid background. On a second screen that is
the intended effect; over a video call it would be a grey rectangle.

**That is a window problem, not a page problem, and it is the whole of Phase 5.**
The page is the part that survives the change — every candidate shell renders
this same file unchanged — so Phase 4 stops here on purpose, as a tightened
proof of concept on the screen the HUD was measured on. § Phase 5 below carries
the shell comparison and the requirement that decides it.

#### Phase 5. The overlay window — not started

The HUD's own argument ends at a display that sits *in* the visual field rather
than beside it. Phase 4 deliberately did not go there, and Phase 5 is that work
written down rather than started.

**Scope: the window only.** `display.html?mode=wheel` is the content and does
not change. Everything here is the shell around it, which is why the phase can
wait without holding anything else up, and why nothing built in Phase 4 is
wasted if the shell choice turns out wrong.

**What the shell has to provide:**

| | Electron | Tauri v2 | pywebview |
|---|---|---|---|
| transparent, frameless | yes | yes, needs `macos-private-api` | yes (Cocoa) |
| above a full-screen call | `setAlwaysOnTop(…, 'screen-saver')`, `setVisibleOnAllWorkspaces` | yes | `on_top=True` |
| click-through | `setIgnoreMouseEvents(true, {forward: true})` | `set_ignore_cursor_events` | **no** |
| excluded from screen capture | `setContentProtection(true)` | `set_content_protected(true)` | **no** |
| cost | ~150MB, Node in a Python repo | ~10MB, a Rust toolchain | `uv add pywebview` |

**Capture exclusion is the row that decides it, not transparency.** An overlay
without it puts the prep pack into the screen share, which inverts the tool
rather than degrading it — the failure is silent, it looks fine from this side,
and it is discovered by the other party. Under this project's own rule that is a
refusal to ship, not a caveat. It maps to `NSWindow.sharingType = .none` on
macOS. pywebview is otherwise the natural fit for a Python repo and loses on
exactly the two rows that matter, which is worth stating plainly because it is
the one that would otherwise be reached for first.

**Electron is the recommendation for the spike**, on the grounds that all four
rows are documented one-liners and it also closes § 4a's open hole:
`globalShortcut` gives the clicker a listener that works while the video call
holds focus. One honest caveat, because it is the kind that gets discovered
late: presenters send bare `PageUp`/`PageDown`, and registering those globally
would break every slide deck on the machine. 4a still needs a modifier combo or
a real HID listener; the shell does not make that free.

**A tension to resolve inside the spike, not before it:** click-through and "the
wheel moves my column" are opposites. The standard resolution is
`forward: true` — the window stays click-through, still receives mouse-move, and
flips interactive while the pointer is over the column.

**Prerequisites, in order:**

1. **A glance-test run that can actually answer.** Runs 3 and 4 have both been
   run by a human and both fact columns are void — run 3 because the probe
   leaked, run 4 because the pre-registered gate tripped on a control check too
   small to answer in either direction. The next step is a **control-only
   validation run** (`one` and `column`, 28 trials, pooled n=28, reject at
   ≥19/28), which needs the `reps` cap in `tools/exposure_test.html` lifted and
   no new cards. A three-way comparison needs roughly 64 corpus cards. Phase 5
   is a bet on the HUD form and that bet is still unmeasured.
2. **A decision on whether a new card takes the focus** (§ 4b, decision 1). On a
   second screen a wrong answer costs a wheel flick. On an overlay held in the
   visual field for a whole call it is the difference between a tool and a
   distraction, and it is one line either way.

**What would prove it, not just demo it.** The gate is not "the window is
see-through". It is: start a real screen share, confirm the overlay is absent
from the capture, and confirm a click at the overlay's coordinates reaches the
application underneath. A capture exclusion that is believed to work and does
not is worse than no overlay, because it gets budgeted against.

#### Card interaction — the backlog, and what is already built

**Cards stay until they are dismissed — decided 2026-08-29, and it stops the
measurement track.** A content card has no TTL (`GLOSS_CARD_TTL_S=0`); it
leaves when it is clicked, when `Backspace`/`Delete`/`x` dismisses it, or when
the cap pushes it off. The 90-second clock is gone.

The reason is a use report, not a run: *"half of the cards I couldn't see, and
it depends on what is happening."* A card cannot know when the call will let
you look up. Sometimes that is two seconds after it appears and sometimes it is
after the answer you were mid-way through giving, and a clock set from the
model's turn is set from the wrong event entirely. The failure was silent in
the worst way — the card was there, correct, and gone before the moment to read
it arrived.

**This retires the question § 4b was built to answer.** Four runs asked how much
a card can deliver in a fixed exposure of 150 ms to 2 s. That framing assumed
the *display* chooses the exposure. It does not; the conversation does, and the
answer to "how long is the card up" is now "until you are done with it". The
control-validation run is not worth building, the corpus does not need growing
to 64 cards, and the fact column stays void and uncited rather than being
chased a fifth time. What survives from all four runs is the identity result —
a term is locked in ~150 ms, 20/24 across run 4's conditions — and that is the
number the HUD's one-legible-row design actually rests on.

**What still carries a clock, and why.** Status cards (`GLOSS_ERROR_TTL_S`,
20 s) and unconfirmed previews (`GLOSS_PREVIEW_TTL_S`, 12 s). Nobody dismisses
a message about a vendor outage that has already ended, so a status card that
waits to be dismissed is a display asserting a problem that no longer exists —
the one failure mode this project treats as worse than showing nothing.

**And the cap is now load-bearing on its own.** With no clock, `GLOSS_MAX_CARDS`
is the only thing standing between the display and a wall of stale topics,
which is why eviction stays least-recently-mentioned and why pinning had to be
re-read as protection from *eviction* rather than from a clock.


Raised 2026-08-28: cards should tile so a new one arrives alongside a few
existing ones and old topics fade off as they are displaced; a key should pin a
card; and a different interaction should take a topic deeper. Written down here
rather than started, with an honest note on which of it is new — because most of
it is not, and a backlog that re-proposes shipped behaviour hides the one item
that is real work.

**Already built — MVP, and it is the MVP.**

| asked for | where it lives |
|---|---|
| tile, N visible, older topics fade off | cards mode, `GLOSS_MAX_CARDS` (default 3) |
| replaced rather than cleared | eviction by *least-recently-mentioned*, not oldest-created: a topic the conversation returns to outlives a one-off from earlier |
| fade rather than snap | 220 ms opacity transition on removal. No TTL on content cards since 2026-08-29 (`GLOSS_CARD_TTL_S=0`) — they leave when dismissed |
| pin a card | `Enter` / `p` on the clicker — last to be evicted when the cap is reached |
| dismiss a card | **click the card**, or `Backspace` / `Delete` / `x` |

Covered by `tests/test_eviction.py` and `tests/test_display.py`. Nothing to do.

**What the request actually surfaces, which is worth more than the features.**
The tiling described — *a new card visible alongside several existing ones* — is
something the second screen does better than the wheel HUD, which makes exactly
one row legible and fades the rest. That is evidence against § 4b decision 1
("a new card takes the focus"), and possibly against the wheel's premise for a
call with several live topics. It is not evidence to act on yet: run 3 is the
thing that would settle it, and it has not been run. Recorded so the HUD's
default is revisited on data rather than defended on having been built.

**Not built, and the only new work here: expand / go deeper on a card.**

Verdict: **not essential to MVP.** Four reasons, in the order they bite.

1. **The clicker has no free button.** A four-button presenter sends
   `PageUp`/`PageDown` and usually one blank key; next, previous, pin, dismiss
   and blank already claim everything it emits. An expand action needs a key
   that no presenter sends, a long-press, or a reach for the keyboard — and
   reaching for a keyboard mid-call is the disruption § 4a exists to remove.
2. **A mid-call vendor round trip is a new failure mode on the interactive
   path.** Everything on that path today is either cached, local, or fails into
   a card that says so. "Press expand, wait, nothing happens" is a worse moment
   than not having the button.
3. **It has a recurring cost per press**, on a project whose standing
   constraint is no paid services.
4. **Tier 2 already covers "go deeper" where it is free to be slow.** The
   deferred post-call export (§ Phasing, Phase 3 deferred) does a research pass
   over the saved transcript with grounded citations, with no in-call latency
   and no risk to the call.

**The version that would be worth building, if it is built: local-only
expansion.** Both card kinds already have a local source — `recall` cards come
from a section of the prep pack, `jargon` cards can be backed by the book-built
glossary — and both indexes only ever quote. So *expand* could mean **show more
of the source this card came from**, not *ask a model for more*. Zero vendor
call, zero added cost, no new failure mode, and it degrades correctly: a card
with no local source simply has nothing to expand, which is a silent no-op
rather than a spinner. That is the shape to spec if this is picked up; the
model-backed version is not.

**Sequencing.** ~~After run 3 and after § 4b decision 1 is settled.~~ That
precondition is gone: the measurement track was stopped 2026-08-29 and no fifth
run is coming, so nothing further is going to settle what expand attaches to by
measurement. What replaced it is below.

#### Two axes, flagging, and a configurable input map — proposed 2026-08-30

Raised by Evan during the first live two-laptop run, at the wheel prototype:
*"you can traverse thru the topics and such, i like the idea of expanding out
info to the side… each navigation to the right would move over to increasing
context and depth into the topic. then you could highlight a topic for further
follow up or flag to breakout."* Written down here as a proposal, not started.

**The wheel is not the input that works, and that is the first finding.**
Observed, not measured: on a real wireless presenter at `wheel=4`, the wheel
took a lot of travel for imprecise movement while the arrow keys were exact.
That is one person on one device and it is not a result — but § 4b already
holds that the wheel is *one input calling `step()`*, not the mechanism, so
nothing rests on it. If the wheel is demoted from "the interaction" to "an
optional input", the wheel axis is free for something else, which is what makes
the proposal below cheap rather than a redesign.

**Two axes.** Vertical moves between topics; horizontal moves into one.

| degree | what is shown | where it comes from | exists today |
|---|---|---|---|
| 0 | the term, one row legible | the card's `label` | yes |
| 1 | the detail line | the card's `detail` | yes — dwell, or `d` |
| 2 | more of the source the card came from | prep-pack section (`recall`) or glossary entry (`jargon`) | no |
| 3 | the whole section | the same source, untrimmed | no |

Degrees 2 and 3 are **local-only**, which is the shape § "expand / go deeper"
already landed on: both indexes only ever quote, so this is showing more of
something already in memory. No vendor call, no per-press cost, no new
mid-call failure mode — the three reasons the model-backed version was refused.
Three degrees is a cap, not a target: a prep-pack section is finite, and past
its end there is nothing further to show.

**Availability has to be visible, not silent.** The earlier note said a card
with no local source is "a silent no-op rather than a spinner", and that is
wrong for an axis. A no-op is fine for a button pressed by mistake; an axis
that sometimes moves and sometimes does not teaches the reader nothing and
costs a press mid-call to discover. Error cards have no source at all, a
`jargon` card with no glossary hit has no degree 2, and a `recall` card always
has a section. So depth availability is part of the card's state and the row has
to carry it — a mark on the kind label is enough, and it is static, which the
HUD requires. Evan's framing, kept as the rule: **if it is not populated, the
feature is not enabled.**

**Flagging is a different verb from pinning, and must not be folded into it.**
Pin means *do not evict this while the call runs*. Flag means *I am done with
this now, come back to it after*. Overloading one key with both would make the
common action ambiguous at the exact moment there is no attention to spare.
A flag is small and concrete: the card's id and the transcript turn it was
flagged on, appended server-side. That is the input the deferred **Tier 2
post-call export** wants — it already plans a research pass over the saved
transcript, and a list of moments the person marked is a better starting set
than the whole transcript. Flagging is only worth building alongside that
export; on its own it writes a file nothing reads.

**The input budget is the constraint, again, and it is the reason this may not
ship as specced.** § 4a documents what a presenter clicker actually emits:
`PageUp`/`PageDown`, sometimes arrows, and usually one blank-screen key. Next,
previous, pin, dismiss and blank already claim all of it. Two axes plus a flag
needs six distinct inputs from a device that reliably sends three. The honest
options, none of them free:

1. **Mouse-and-keyboard only**, with the clicker limited to the vertical axis.
   Depth becomes a thing you do when a hand is free, which is most of a call
   but not the moments that matter most.
2. **A modifier or a long-press** on the clicker. Long-press is invisible to
   `keydown` alone and needs timing logic that fires on a held key, which
   presenters repeat rather than hold.
3. **A mode toggle** — one button switches the axis the wheel and arrows act
   on. Cheapest in inputs, worst in state: a HUD where the same press does two
   different things depending on invisible state is the failure this display
   avoids everywhere else.

**A configurable input map runs into a decision that already said no.**
`display.html` removed the prototype's sliders on purpose — *"a HUD does not
ship a control panel it should never show"* — and tuning became query params
for exactly that reason. A settings surface inside the window contradicts it,
and the contradiction is not resolved by making the panel small.

What resolves it is *when*, not *how big*: configuration belongs to the state
where there is no call — the display before it is connected, or a separate page
— and what it produces is the same query string the HUD already reads. That
keeps one mechanism, adds no in-call surface, and has a working precedent in
this repo: `tools/listener_setup.py` does exactly this for Laptop A, asking the
questions once and writing a launcher rather than adding runtime settings.
Persisted per-machine, `localStorage` is the obvious store for the display's
own copy, with the query string still winning so a shared link behaves the same
for everyone.

**What would settle whether any of this is right.** Not a lab run — that track
is closed. The acceptance test is a real call, or a recorded one played through
Laptop A, with three questions answered afterwards: was the depth axis reached
for at all; did a flag get set that was worth having after; and did anyone hit
the wrong axis. If depth is never reached for in a whole call, it is not a
missing feature, and building it would have been the second most expensive kind
of mistake — the kind that looks like progress.

**Sequencing.** Behind the first real interview, deliberately. Everything here
is a reaction to a prototype rather than to use, and one call will say more
about which of the three is real than any amount of further design. Phase 5 is
unaffected either way: the overlay is a window shell, and none of this changes
the page.

### Phase 2 design

**Trigger: `speech_final` on the interviewer channel only.** The user's own
channel is transcribed (Phase 3 needs it, and it's on screen for context)
but never triggers enrichment — you don't need cards about what you just
said. `speech_final` is Deepgram's end-of-turn signal, which is the point
where the question is complete and a card is still useful.

**Two card kinds, both derived from material that already exists:**

| `kind` | Source | Answers |
|--------|--------|---------|
| `recall` | the prep pack you wrote | "you already have a story for this" |
| `jargon` | the model's own knowledge | "you may not know this term — ask" |

A `recall` card must quote your prep pack, not paraphrase it into new
claims. A `jargon` card names the term and gives a one-line definition, so
the move is to *ask a better question*, not to bluff. Nothing here composes
an answer for you — that is the line this design does not cross, and the
"no live web search" decision below is what keeps it enforceable.

**Card schema** — `{kind, label, detail}`, at most 3 per turn:
- `label`: ≤ 6 words. This is the part you actually read at a glance.
- `detail`: ≤ 25 words, the reminder underneath.
- Returning **zero** cards is correct and expected for most turns. The
  system prompt says so explicitly, because a model that always finds
  something to say trains you to stop looking at the screen.

**Prompt shape, built for the cache:**

```
system  = [ frozen instructions + card rules + prep pack ]  ← cache_control
messages = [ rolling window of the last N transcript turns ]  ← volatile
```

The prep pack is the whole point of the cache: it is the same bytes on
every turn of a one-hour call. Nothing per-request may appear in `system` —
no timestamp, no turn counter, no session id — or the prefix changes every
turn and the cache never reads. Prep-pack files are concatenated in
**sorted filename order** so the bytes are deterministic across restarts.

**Cache minimums are a real constraint, and they fail silently.** Every
provider has a floor below which a prefix is simply never cached, and none
of them raise an error about it — the counters just stay at zero. Gemini 3.x
Flash and Claude Haiku 4.5 both need ~4096 tokens. (Gemini 2.5 Flash's floor
was 2048, but 2.5 is closed to new API users as of 2026-08 — the API says so
in the 404 and names 3.6 Flash as the replacement.) A short prep pack therefore gets
no caching at all, whoever is answering. `b_server.py` logs the token
counters on the first enrichment of each run so this is visible rather than
assumed.

Caching here buys latency more than cost. At Flash/Haiku input prices an
hour of turns is worth cents either way, but a cache read is materially
faster than reprocessing the pack, and the whole design budget is 1–2
seconds.

### Provider abstraction

Nothing below the config block in `b_server.py` knows which vendor is
answering. The model is built through LangChain's `init_chat_model`, and the
card schema through `.with_structured_output()`.

Which provider, which model, the kwargs that model takes, whether it needs its
cache prefix marked explicitly, and the prefix size below which it stops
caching are **one row in `providers.py`**, not five settings. They were five
settings until 2026-08-24, and they drifted: `GLOSS_PROVIDER=anthropic` with a
stale `GLOSS_MODEL_KWARGS` still holding Gemini's `thinking_level` produced a
400 from a vendor that has never heard of that parameter. Selecting a provider
now selects the whole row.

```
GLOSS_PROVIDER=anthropic      # DEFAULT — claude-haiku-4-5, cache breakpoint, floor 4096
GLOSS_PROVIDER=deepseek       # deepseek-v4-flash, implicit caching, floor 128 (measured)
GLOSS_PROVIDER=ollama         # llama3.2, nothing leaves the machine
GLOSS_PROVIDER=fake           # canned cards, mocked E2E suite only
GLOSS_PROVIDER=google_genai   # no longer a target; kept working, unmaintained
```

The list is an **allow-list**: an unnamed provider is refused at startup rather
than attempted, because "unrecognised" and "unsupported" are the same thing
from a live conversation's point of view and the failure would otherwise land
mid-call. Overriding the pinned model is allowed but must pin its kwargs too —
an unpinned kwargs override follows you onto the next provider and fails there.

#### Priming the recogniser — `keyterms.py`

Nova-3 accepts `keyterm` prompting: domain vocabulary supplied at connect time,
which it then recognises far more reliably. gloss already had the ideal corpus
and was not using it — **the prep pack is by definition the vocabulary this
conversation will contain.**

This sits upstream of everything else, which is why it earns its place. Every
later stage reads the transcript: a jargon card fires on a term the recogniser
heard, and a recall card matches notes against words the recogniser produced. A
term rendered wrong is a term no prompt and no retrieval can recover, and the
failure is silent — the transcript still reads plausibly, the match simply never
happens.

#### Measured 2026-08-25, Nova-3, identical audio, primed vs unprimed

Synthesised speech containing six terms from the `cachetest` pack, streamed
twice. Same bytes, same model, one variable.

| term | unprimed | primed |
|---|---|---|
| `A2DP` | `a two d p` | **`A2DP`** |
| `WASAPI` | `Wasapi` | **`WASAPI`** |
| `parakeet` | `Parakeet` | `parakeet` |
| `HFP` | `HFP` | `HFP` |
| `PCM` | `PCM` | `PCM` |
| `GRIFFON` | `Griffin` | `Griffin` |
| **recovered** | **4 / 6** | **5 / 6** |

`A2DP` is the result that matters: unprimed it came back as three separate
words, which no downstream matcher could recover under any amount of care. The
casing corrections matter for the same reason, since retrieval matches exactly.

`GRIFFON` → `Griffin` failed **both** times. Priming does not beat a homophone.
That is a real limit of this technique, not a tuning problem, and anything built
on top of the transcript has to assume some terms arrive wrong.

**What counts as a term is not what counts as a heading.** Prep packs are prose.
In this repo's own packs `**bold**` emphasises whole sentences, so bold is a
noisy source while backticks and acronyms are nearly pure signal. Sources are
ranked rather than pooled, and three classes are excluded outright because they
cost budget without being sayable:

* **Code fragments** — `deque(maxlen=6)`, `b_server.py:154`, commit hashes.
  Backticked spans are also stripped before the later passes run, or the acronym
  scan mines `PLAN` out of a file path.
* **Sentence-initial capitals** — position, not a word list, is the primary
  test: a word capitalised *mid-sentence* is a name, one opening a heading or
  sentence is not. A colon is deliberately not a boundary, because
  "Company: Example Corp" is exactly the shape prep packs use for names.
* **Section titles** — "Something that went badly" is not vocabulary. A heading
  qualifies only if every word reads as part of a name.

Nova-3 caps keyterms at 500 tokens per request and errors above that, so the
list fills in rank order to a conservative default of 400 and the overflow is
logged rather than dropped quietly.


#### Measured 2026-08-25, two real recorded interviews (91 minutes)

The synthetic-audio result above says priming works. Real interview audio says
**it works when the pack's terms actually come up, and is not free when they do
not.**

Two of Evan's own recorded screens, run through Nova-3 twice each — once bare,
once primed with a plausible pack vocabulary for the first call.

| | recruiter screen, 22 min | technical, 69 min |
|---|---|---|
| terms in the pack that appeared | most | few |
| real fixes from priming | **6** | 1 (casing) |
| regressions from priming | 0 | 2 |
| residual homophone errors | 0 | 0 |

On the first call priming recovered the employer name three times, including one
place the bare run produced **"our solutions"** — not a mishearing of the name
so much as its absence. `Rx State` → `RX Savings`, `Bodo` → `boto`,
`fast API` → `FastAPI`, `Firemon` → `FireMon`.

On the second call the same vocabulary was largely irrelevant to what was
discussed, and priming produced one casing fix, two regressions (`auth` → `off`,
`React` → `react`) and one sentence that exists in no other run:
*"I can listen to radio on my socks."*

**Half of that difference was not priming at all.** Two *identical* unprimed
passes over the same audio differ in 8 places — all punctuation and filler
(`"And so"`/`"So"`, `"things, and"`/`"things. And"`). Nova-3 is not
deterministic, and any before/after comparison that skips this control is
measuring noise. The primed run's 17 differences net out to roughly 9 real ones.

**What follows from it:** keep prep packs targeted at the specific call.
`keyterms.py` already extracts from *that call's* pack rather than a standing
vocabulary, which is the right shape — but a stale or generic pack can make
transcription slightly worse, and nothing currently warns about that.

#### Homophone correction — measured, NOT BUILT 2026-08-25

Nova-3 transcribed a synthetic "GRIFFON" as "griffin", and priming did not fix
it. That looked like a gap worth closing: a term the recogniser gets confidently
wrong is a term no later stage can recover, because everything downstream
matches on exact strings.

**It was not built, because on real audio the failure does not occur.** 91
minutes of recorded interviews, through Nova-3 with priming, produced **zero**
residual homophone errors of that kind. The remaining mistakes were `EC two` for
`EC2` — letter/digit rendering, which no homophone map addresses.

**The premise came from the wrong transcript.** The failures that motivated this
were Plaud's, not Deepgram's. Plaud rendered the same audio as "jot token" for
JWT, "a docker or PDF file" for DOCX, and the employer name three different
wrong ways. Nova-3 got `JWT` and `RX Savings` right *unprimed*. gloss does not
read Plaud's transcripts, so those failures are not gloss's failures.

Three designs were built or tested on the way, and the reasoning is worth
keeping because the first two look plausible:

1. **Confidence gating** — only correct words the recogniser was unsure about.
   **Dead.** Measured per-word confidences on the failing clip: the wrong word
   (`griffin`) came back at **0.942**, higher than four words it got right,
   while correct words `hfp` (0.830) and `loopback` (0.862) scored lower. A
   homophone is acoustically unambiguous by construction — the recogniser heard
   exactly what was said and picked a real word. Confidence measures acoustic
   certainty, not lexical correctness, and for this failure the two are
   anti-correlated.

2. **Backward fuzzy matching** — heard word searched against the vocabulary
   phonetically. **Dead.** Soundex alone matched 19 of 115 common English words
   to pack terms (`was`→`WS`, `there`→`Tier`, `system`→`SYSTEM_PROMPT`). Gating
   on "not a common word" removed all of those but left `endpoint` →
   `endpointing`: ordinary technical speech rewritten to a Deepgram parameter,
   silently, in the text every later stage reads.

3. **Forward prediction, offline** (Evan's design, and the correct one) — for
   each pack term, precompute what the recogniser would plausibly return
   instead, audit the map at build time, then match exactly at runtime.
   Measured: a model predicted `GRIFFON`→`griffin` and `A2DP`→`a two d p`
   verbatim, with zero collisions against common speech, for $0.0003 across 12
   terms. It also predicted `endpointing`→`"end pointing"` rather than
   `"endpoint"` — the direction of the search is what avoids the false positive
   in (2). Its weakness is recall: it missed `WASAPI`→`wasapi`, which priming
   already covers.

So the design is sound and the problem is absent. **Revisit only with a real
call's transcript showing the failure**, not synthetic audio. If it is ever
built, build (3): offline map, build-time collision audit, exact match at
runtime, corrections logged.

A trained classifier (OpenNLP-class maxent, not embeddings — CPU-cheap, no GPU)
would be the layer above it, for cases where the mis-transcription is plausible
in context. The blocker there is labelled examples of domain-specific
mis-transcription, which do not exist. The map in (3) is how they would be
generated: every correction it makes, with its surrounding turn, is one.

#### The fallback chain

The chain's last link is **not a model at all**. `local` answers from two
indexes already in memory — no network, no tokens, microseconds. It is the
floor under the chain: it runs only when every vendor above it has failed.

| index | built from | emits | module |
|---|---|---|---|
| glossary | books on disk, read once offline | `jargon` | `kb.py` |
| notes | this call's prep pack, indexed at startup | `recall` | `recall.py` |

The split is a correctness rule, not a preference. A `recall` card claims the
person's own notes say something, and a book is not their notes — so the
glossary can only ever emit `jargon`, and the pack can only ever emit `recall`.
Each index is confined to the claim its corpus can actually support.

**Recall is first on the wire and therefore first on screen.** When both fire,
the note the person wrote for this specific call is the one worth the glance.

**It is a floor, not a first pass.** Putting it in front of the models would
trade the better card for a faster one: the indexes match words that were
literally said, the model reads what was meant.

**A local miss reports the vendor's failure, not its own.**
`with_fallbacks()` re-raises only the last link's exception, and the last link
is `local` — so without care, "nothing local matched this turn" is what
reaches the screen during a total outage. Each link records its failure for the
turn, and the error card names the first one with a recognisable reason.

**Either half alone keeps the link.** The glossary is optional and most
deployments will never run the builder; every deployment has a prep pack. The
link is dropped only when both indexes are empty.

#### The instant preview — latency, not tokens

The chain answers in 1.1–1.8s. The glossary answers in **6µs on a hit and 13µs
on a miss**. So when a turn ends, the glossary paints immediately and the model
supersedes it when it arrives.

Terms are bucketed by first word, so a turn is matched against the handful that
could start at each of its words rather than against all of them. The first cut
scanned every term — correct, and 200µs/680µs at 5,622 terms, but linear in the
glossary. Measured across a 10× corpus:

| glossary | hit | miss |
|---|---|---|
| 5,622 terms | 6.1µs | 12.8µs |
| 55,622 terms | 6.6µs | 13.7µs |

Cost tracks the length of the turn, not the size of the corpus, which is what
makes growing the knowledge base free at query time.

"Longest match" counts **words** before characters: a longer phrase is the more
specific reading, so "api gateway" beats "internationalization" even though the
single word is longer. Ties break by length then alphabetically, so the card
shown never depends on dict ordering.

Measured live on the real path, one turn:

```
   0.3 ms  [jargon] Eventual consistency — The system will eventually reach a consistent state…
1603.2 ms  [jargon] Eventual consistency — Distributed systems guarantee: all replicas converge…
```

Same topic id, so the second updates the first **in place** — one card, better
text, no flash. That is what the S4 card lifecycle was built for, and this is
the first thing to depend on it.

**It does not replace the model call, and that is the point.** The indexes
match words that literally appear; the model reads what was meant, over the
whole conversation, and that is still the better card. Answering *instead* of
the model would trade it for a faster one. So this buys **latency, not
tokens**: the model is asked either way.

*(An earlier version of this paragraph said a `recall` card was "the one only a
model can write". That was true while the only local corpus was a shelf of
books. It stopped being true when the prep pack itself was indexed — see below.)*

**An unconfirmed preview is retracted, not left to time out.** The preview is a
keyword match; the model read the whole conversation. If the model's cards do
not include the previewed topic — or if it returns no cards at all, which is
the normal turn — the server sends `{"type": "expire", "ids": [...]}` and the
display takes the card down. Left standing, the guess would sit beside the
model's own card on a different topic, which is exactly the duplication the
card lifecycle exists to prevent.

Belt and braces: a preview also carries a short `GLOSS_PREVIEW_TTL_S` (12s), so
an unconfirmed guess fades on its own even if the enrichment pass never returns
at all. When the model confirms it, the same card is rewritten with no TTL at
all and from then on stays until it is dismissed.

Set `GLOSS_PREVIEW=` empty to turn it off and wait for the model every turn.

#### Why a glossary and not retrieval — measured 2026-08-25

Lexical retrieval over the same corpus was built first, and it does not work.
BM25 over 129,117 filtered sentences from 32 books, on ten test terms:

| | usable answers |
|---|---|
| BM25 over book sentences | **3 / 10** |
| offline LLM glossary pass | **17 / 18** |

The reason is structural rather than tunable. **These books are prose *about*
concepts, not a reference work *defining* them.** The best sentence containing
"circuit breaker pattern" is "the circuit breaker pattern can help here to
handle system dependencies" — a mention, and no ranking function turns a
mention into a definition. Books explain across paragraphs; a card has 25 words.

Two failures along the way are worth keeping:

* **Back matter wins BM25.** An index page is nothing but keywords, so it
  out-scores every real explanation of the same term. Eight of the first ten
  results were index entries, TOC dotted leaders or code imports.
* **Bag-of-words retrieval answers confidently about the wrong term.** "Chaos
  engineering" returned "Context engineering is a core component of
  orchestration". Phrase-anchoring fixed it, and is why `Glossary.lookup`
  matches whole terms on word boundaries rather than scoring tokens.

So the reading happens **once, offline**, where there is no latency budget and
no conversation waiting: `tools/build_kb.py` sends each passage to a model and
asks what it *defines*. Build-time tokens are not conversation-time tokens —
the whole 32-book corpus costs about a dollar, once, and the live path then
answers from a dict for nothing. The builder refuses to start above
`--max-cost` (default $2), because a build script that quietly emptied the
account funding the live fallback chain would take out the reliability it
exists to add.

**Apostrophes are deleted from lookup keys, not treated as separators.** A
speech recogniser rarely emits one: Nova-3 transcribes Conway's Law as "conways
law". Splitting on the apostrophe made every possessive term in the glossary
permanently unmatchable while looking entirely correct in the file.


One vendor is a single point of failure for a conversation that is happening
right now and cannot be rescheduled. `GLOSS_FALLBACKS` (default `deepseek`)
names the vendors tried behind `GLOSS_PROVIDER`, in order:

```
anthropic:claude-haiku-4-5  →  deepseek:deepseek-v4-flash
```

Fallbacks take their `providers.py` row verbatim; `GLOSS_MODEL` and
`GLOSS_MODEL_KWARGS` are scoped to the provider you selected and are **not**
carried across — a kwarg that follows you onto another vendor is the exact drift
that row exists to stop, and a chain is the easiest place for it to happen
unnoticed. Set `GLOSS_FALLBACKS=` empty to run deliberately on one provider.

Caches are model-scoped, so the fallback link starts cold. That is fine for an
outage and useless as a cost strategy: **this is outage resilience, not
quota-dodging.**

**Every link is preflighted with a live call at startup, and any link that fails
refuses the whole boot.** The preflight is a real HTTPS round trip carrying the
real card schema — not a check that a key is present and shaped right. That
weaker check exists too (`providers.missing_key`, which only asks whether the
variable is non-empty) and runs first, but it cannot tell a funded key from a
revoked one. Only a call can. An unproven fallback is not a fallback — it is a
second failure waiting for the worst possible moment, and it would be discovered
during the outage it exists for. A link whose credential is simply unset refuses
even earlier, before any call. During a genuine vendor outage the escape is
explicit: point `GLOSS_PROVIDER` at a link that answers and set
`GLOSS_FALLBACKS=` empty.

#### The prep pack, indexed — recall without a vendor, measured 2026-08-27

The section above rules out retrieval over **books**. It does not rule out
retrieval over the **prep pack**, and treating the two as one question left the
outage behaviour backwards: with both vendors down, gloss could still define a
term it read in a book, but could not reach the notes the person wrote for the
call that was happening. Those are the highest-value words in the system, and
they were already parsed and resident in memory.

**The objection does not carry across.** It was about *definitions* — prose
that mentions a term is not prose that defines it, and no ranking turns a
mention into a definition. A recall card makes no such claim. Its job is to put
back on screen something the person already wrote, and the system prompt's rule
("never invent a fact, a number, or an anecdote that is not written there") is
satisfied by construction: `detail` is a span copied out of the pack. `recall.py`
has no path that generates text. It cuts a long line rather than summarising it,
because summarising is generating.

**Why this is scored overlap and phrase matching is not.** `Glossary.lookup`
holds that a term either appears or it does not, which is right for a
dictionary and wrong here. "Tell me about a time you brought down latency on a
read path" shares no exact phrase with a note headed "Latency on a hot read
path" — a phrase matcher stays silent on precisely the turn this exists for.
Speech restates; notes do not.

So the safety moves into three refusal rules instead:

1. **Two matched words, or one *name* unique to a single section.**
2. **A word indexes only if it appears in at most half the sections** — a
   stoplist derived from the pack rather than maintained by hand, because
   "engineering" is distinctive in one pack and noise in another, and only the
   pack knows which. A second, small deny-list catches speech filler that is
   rare in a pack and meaningless in a sentence.
3. **A tie wins nothing.** Two sections matching equally well means the turn
   does not distinguish them, and the wrong story on screen looks exactly as
   confident as the right one.

**Rule 1 originally said "one word unique to a section", and that was measured
wrong.** Three of fifteen off-topic turns produced a card: "hand" out of
"hand-rolled", "second" out of "a second path", "next" out of "next time". Each
appears exactly once in the pack, so each alone was enough — and "I'll hand over
to my colleague now" answered itself with a rehearsed anecdote about partner
integrations. Rarity *inside a pack* is not rarity *in English*, and the
difference is almost exactly proper-nounhood. `keyterms.py` already draws that
line by position rather than by a word list, having had to solve the same
problem for the recogniser, so the line is drawn once and imported.

Measured on `sessions/example` after the fix:

| turns | cards emitted |
|---|---|
| 18 off-topic (greetings, logistics, hand-offs, generic openers) | **0** |
| 7 on-topic (restated in words the notes do not use) | **7** |

| operation | cost |
|---|---|
| index build, 8 sections | 1.1 ms, once at startup |
| lookup | 22µs median, 25µs p95 |

**What it costs the design: nothing new is exposed.** The pack is already the
system prompt and already reaches the screen as model-written recall cards.
This quotes the same bytes by a shorter route, and `tools/check_pack.py`
remains the single gate on what a pack may contain.

**The one accepted cost is card identity.** A recall card's `id` is the
section's slug, so it is stable across every turn that returns to that story —
which is what lets the display update in place. It rarely matches the id a
model would choose for the same topic, so a recall preview is usually
expired-and-replaced by the model's card rather than updated in place. Both
messages are sent together, so there is no flash; stability across turns is
worth more than the merge.

**A section needs a heading.** Text before the first heading in a pack is not
indexed, because a card needs a name and an unnamed preamble has none to give
it. This is now a rule for writing packs — see `sessions/README.md`.

#### What a failure means — `failures.py`

**A status code is a lossy key.** The same number means different things on
different vendors, and worse, different things on the same vendor. These were
read from the vendor docs on 2026-08-25:

| condition | Anthropic | DeepSeek | Gemini |
|---|---|---|---|
| revoked / invalid key | 401 `authentication_error` | 401 | 401 `authentication` |
| balance empty | 402 `billing_error` | 402 | — |
| spend limit you set | **400 `invalid_request_error`** | — | — |
| tier spend cap | **429, no `retry-after`** | — | — |
| daily quota exhausted | — | — | **429 `quota_exceeded`** |
| ordinary rate limit | 429 `rate_limit_error` | 429 | 429 `rate_limit_exceeded` |
| overloaded | 529 `overloaded_error` | 503 | 503 `service_unavailable` |
| server error | 500 `api_error` | 500 | 500 `api_error` |
| timeout | 504 `timeout_error` | — | 504 `deadline_exceeded` |
| malformed request | 400 `invalid_request_error` | 400 / 422 | 400 `invalid_request` |
| unknown model | 404 `not_found_error` | — | 404 `model_not_found` |

Two collisions do real damage if ignored:

* **Anthropic 400 `invalid_request_error` is either our bug or a spend limit.**
  Same status, same type string. Only the message separates them, so this is the
  one place a message substring is load-bearing. It fails safe: if the wording
  changes the match stops firing and the failure classifies as a bad request,
  which surfaces an error card rather than hiding anything.
* **429 is either a rate limit that clears in seconds or a spend cap that never
  clears.** Anthropic documents the tell — the spend-cap 429 carries no
  `retry-after` header.

So failures are classified into **reasons**, and two independent decisions hang
off the reason:

| reason | fails over | retires the link | typical cause |
|---|---|---|---|
| `unreachable` | yes | no | DNS, refused connection |
| `timeout` | yes | no | no answer inside the deadline |
| `rate-limited` | yes | no | too many requests; clears itself |
| `overloaded` | yes | no | vendor 5xx / 529; clears itself |
| `exhausted` | yes | **yes** | dead balance, spend limit, daily cap |
| `credential` | yes | **yes** | revoked, expired, no permission |
| `bad-request` | **no** | no | our bug — malformed, wrong model |
| `unknown` | **no** | no | unrecognised |

**Failing over** is an allow-list: an unrecognised failure stays put and reaches
the screen, because failing over on the unknown case would let a second vendor
bury a real bug behind a card that looks fine.

**Retiring** is the payoff for knowing *why*. A revoked key and a dead balance do
not fix themselves mid-conversation, so that link is skipped for the rest of the
session instead of costing a wasted round trip on every turn. Transient reasons
never retire a link — demoting the primary for an hour over a two-second rate
limit would be worse than the blip.

Credential failures are the entry worth arguing about, because a 401 is normally
a configuration bug that should fail loudly. What makes it safe here is the
preflight: a key that is missing, wrong or unfunded cannot get past startup, so
a 401 arriving at turn 20 is a key revoked or a balance hit zero *mid-call*,
which is outage-shaped. **The preflight is load-bearing for this decision — if
it is ever removed, `credential` and `exhausted` must come out of the failover
set with it.**

The error card names the reason rather than the exception class, because someone
glancing at a second screen mid-conversation can act on "Provider out of credit"
and cannot act on "APIStatusError".

**Which link answered is logged on every turn**, and a change of vendor is
warned about once. LangChain's `with_fallbacks()` swallows the primary's
exception and does not report the winner, so each link stamps its own name onto
its result. Without that, a chain that has silently degraded for an entire
conversation is invisible: the cards keep arriving and only the bill and the
cache counters change.

**Gemini and Bedrock are no longer targets.** Bedrock's catalog has no Google
models, which mattered while Gemini was the default and does not now. Gemini
itself is out as of 2026-08-24 in favour of Anthropic primary and DeepSeek
fallback; its row stays working but unmaintained, and its recorded cache floor
is contradicted by measurement (see below).

#### Measured 2026-08-23, gemini-3.6-flash, example pack, two turns

| `thinking_level` | latency | verdict |
|---|---|---|
| unset (default) | 2.7s, 4.4s | over budget |
| `"low"` | 15.6s, 3.2s | far over budget |
| `"minimal"` | 1.7s, 1.3s | **inside the 1–2s budget** |

`thinking_budget` — the 2.5-era parameter — is rejected outright with a 400 on
3.x. These numbers are retained as history; the provider is no longer a target.

#### Measured 2026-08-24, claude-haiku-4-5, cachetest pack (~5,110 tokens), five turns

**Caching is confirmed working here, for the first time in this project.**

| turn | input | `cache_read` | latency |
|---|---|---|---|
| 1 | 6,387 | 0 | 3.35s |
| 2 | 6,408 | 5,933 | 2.75s |
| 3 | 6,424 | 5,933 | 2.59s |
| 4 | 6,442 | 5,933 | 2.55s |
| 5 | 6,462 | 5,933 | 2.87s |

Turn 1 writes the prefix; every turn after reads 5,933 tokens at 0.1x input
price. `cache_read` is **flat rather than climbing**, which is correct at this
stage: only the system prefix carries a breakpoint, so the growing transcript
rides along uncached — visible as `input` creeping while `cache_read` does not.
It starts climbing when the newest-turn breakpoint lands.

**Latency is 2.5–3.3s, outside the 1–2s budget.** Haiku emits 158–222 output
tokens per turn against Gemini's 46–140, and latency scales with output. The
cards are markedly better — specific, and quoting real figures from the pack —
so this is a genuine quality/latency trade rather than a regression, but the
budget is missed and shortening the cards is the obvious lever. Not yet tried.

One counter does not work: `cache_write` reads 0 on every turn including the
first, so LangChain is not surfacing Anthropic's cache-creation field under
`input_token_details.cache_creation` — plausibly because the `1h` TTL reports
under a different key. `cache_read` is the counter that matters and it is
correct; the write counter is cosmetic and unfixed.

#### Measured 2026-08-24, claude-haiku-4-5, after S2 (per-turn blocks)

With one content block per turn and breakpoints on the last two, the
conversation itself caches and `cache_read` climbs with it:

| turn | input | `cache_read` | cached share | latency |
|---|---|---|---|---|
| 3 | 7,417 | 7,038 | 94.9% | 1.62s |
| 4 | 7,765 | 7,382 | 95.1% | 1.17s |
| 5 | 8,114 | 7,730 | 95.3% | 1.12s |
| 6 | 8,131 | 8,079 | **99.4%** | 1.74s |

Latency is **1.1–1.7s, inside the 1–2s budget**, against 2.5–3.3s at S1. The
budget miss recorded at S1 is resolved, though not purely by caching — the
cards in this run were shorter (86–142 output tokens against 158–222), and
output length drives latency more than input does.

**The prompt shape is load-bearing, and two shapes that looked equivalent were
measured failing first.** Prefix caching matches the exact serialised request,
block boundaries included:

* ASK glued to the newest turn — when that turn rolls into history it no longer
  carries the suffix, so the bytes diverge at that point.
* History re-joined into one growing block — turn N sends
  `[history_N][newest_N]`, turn N+1 sends `[history_N + newest_N][newest]`. The
  text is nearly identical; the block structure is not, and the cache key
  covers structure.

Both fail quietly: every lookup falls through to the system breakpoint, so
`cache_read` sits at exactly the prefix size and looks healthy. The only signal
that separates "caching the conversation" from "caching the prep pack alone" is
whether that number *moves*. It sat flat at 5,933 across three separate
experiments before the shape was right.

For contrast, on gemini-3.6-flash a 5,168-token byte-identical prefix reported
`cache_read=0` across four consecutive calls despite a documented 4,096 floor.
LangChain surfaces the field there too, so that was Gemini not caching rather
than a counter not plumbed.

#### Measured 2026-08-25, deepseek-v4-flash, cachetest pack, seven turns

The fallback link, on the same pack and the same conversation. DeepSeek takes no
`cache_control` marker, so the whole prompt is one string and caching is
implicit:

| turn | input | `cache_read` | miss | latency |
|---|---|---|---|---|
| 1 | 5,456 | 0 | 5,456 | 1.2s |
| 3 | 5,504 | 5,376 | 128 | 1.7s |
| 5 | 5,533 | 5,376 | 157 | 1.1s |
| 7 | 5,570 | **5,504** | 66 | 1.8s |

`cache_read` climbs 5,376 → 5,504, so the transcript caches here too and not
just the prep pack. Latency 1.1–1.8s, inside budget. A term introduced on turn 3
was correctly recalled on turn 7 — the same long-range check S2 used on Haiku.

**Its cache floor is 128 tokens, measured, because DeepSeek publishes none.**
Their caching guide states only that it is "enabled by default for all users"
and that a request must "fully match a cache prefix unit". Probing identical
back-to-back prefixes:

| prefix | second call `cache_read` |
|---|---|
| 32 | 0 |
| 64 | 0 |
| 128 | 128 |
| 192 | 128 |
| 256 | 256 |

Hits round **down** to a 128-token boundary, so 128 is one cache unit and the
smallest prefix that can produce a hit at all.

#### deepseek-v4-flash thinks by default, and thinking mode breaks structured output

Every call 400s with `Thinking mode does not support this tool_choice` —
`with_structured_output` forces a `tool_choice`, and thinking mode rejects it.
Thinking would also blow the latency budget, so it is off for two reasons.

The switch is `extra_body={"thinking": {"type": "disabled"}}`. Two details cost
real time and are worth not rediscovering:

* **It has to go through `extra_body`.** LangChain routes an unrecognised kwarg
  into `model_kwargs`, which the OpenAI SDK then passes as a *Python argument*
  to `Completions.create()` and rejects with a `TypeError`. `extra_body` is the
  SDK's sanctioned channel for vendor extensions and lands in the JSON body.
* **This API accepts and silently ignores parameters it does not know.**
  `enable_thinking=false` and `thinking_level="minimal"` both returned 200 with
  thinking still on. `reasoning_effort="none"` also worked but is undocumented
  (the documented values are `low`/`high`/`max`), and there is no way to prove
  it is being read rather than ignored. The documented `thinking` parameter has
  such a proof: setting it to `{"type": "enabled"}` still 400s on tool_choice,
  so the value is demonstrably read. That negative control is the reason to
  prefer it.

gloss gets one guard here for free: `main()` preflights every link with a live
call, so a silently-ignored disable surfaces as a refusal to start rather than
as a dead conversation.

The one thing that does *not* abstract away is the prompt: prefix caching is
prefix caching everywhere, so `SYSTEM_PROMPT` is built once at startup and the
volatile transcript always goes last, whichever provider is configured.

**Noise control:**
### The transcript ceiling

Every turn is sent, every turn — a glossary that cannot connect a thing said now
to the thing that defined it earlier is not doing its job. Bounded rather than
unbounded, though: `GLOSS_MAX_TRANSCRIPT_TOKENS` (default 100,000, half the
window) is roughly 400 turns of speech, far past any real conversation but not
"cannot".

On overflow the oldest turns are dropped and each drop is logged at **warning**
level, because it costs twice: it loses conversation the tool exists to
remember, and it changes the front of the prompt, which invalidates every cached
read after that point. The cost profile changes for a reason nothing else would
show.

**The newest turn is never evicted.** The earlier form dropped turns while the
transcript was over budget at all, so a single turn larger than the whole
ceiling emptied it — and the model was then asked for "cards for the final
[interviewer] turn" with no turn in the prompt. That call costs money and can
only return nothing. An oversized turn is now kept and reported instead: degrade
by carrying fewer turns, never by carrying none.

### Card lifecycle

Cards persist while their topic is live, rather than being replaced each turn.
Every card carries an `id` naming its topic; the display keys a map on it, so
the same topic raised again **updates the card in place** — same DOM node, same
position — instead of tearing the screen down and rebuilding it. A topic
discussed across four turns used to flash four times, on a screen whose entire
job is to be readable in a glance.

`id` is optional even though the schema asks for it, and falls back to a slug of
the label. `required` is advice to a model rather than a constraint on it, and a
card dropped for missing an id would be a real card lost over bookkeeping. The
fallback produces the same id the model would have supplied, so a turn where it
forgets stays continuous with the turns where it does not.

Each card stays until it is dismissed: `GLOSS_CARD_TTL_S` defaults to `0`,
which the display reads as "no clock". Set it above zero to restore the old
90-second expiry, in which case the clock is refreshed when the topic recurs.
When more topics are live than `GLOSS_MAX_CARDS`, the **least recently
mentioned** is dropped — not the oldest — so a thread the conversation keeps
returning to outlives a one-off from earlier. With no clock that cap is the
only bound on the screen, which is why it is absolute even when everything is
pinned.

**Error cards sit outside that cap** and expire on their own much shorter
`GLOSS_ERROR_TTL_S` (default 20). Counting them was the first implementation and
it was wrong in a way only a browser showed: an error evicted a real card, and
when the error expired the topic did not return, because the thing that
displaced it had expired into nothing. A twenty-second outage must not cost a
topic permanently. An error card is the server speaking about itself, not
content.

Neither value is a display policy. The TTL rides on each card and the cap rides
on the batch, so a second screen opened halfway through a call is configured by
the first message it receives, and two screens cannot disagree.

`display.html` is covered by `tests/test_display.py`, which drives the real file
in real Chromium rather than a stubbed DOM — the behaviour under test is DOM
identity (is this the *same* node, in the same position?) and timer-driven
expiry, and a stub would have tested the stub. Without a browser installed those
tests skip locally; in CI `GLOSS_REQUIRE_BROWSER_TESTS=1` turns a missing
browser into a failure, because a silent skip in the environment that gates is
indistinguishable from green.

- Utterances shorter than `GLOSS_MIN_CHARS` (default 25) never trigger a
  call — "mm-hm", "right", "yeah exactly" are turn-taking, not questions.
- One enrichment in flight at a time. A new turn **cancels** the previous
  in-flight call rather than queueing behind it: a card about the previous
  question is worse than no card, because it arrives while you're being
  asked a different one.

**Transport: WebSocket, not SSE.** The architecture diagram above said SSE
until Phase 2; that was never a reasoned decision. `b_server.py` is
already a WebSocket server, and `handler` already dispatches on path — so
`display.html` connects to `ws://localhost:8765/display` and receives the
same card JSON, costing zero new dependencies and zero new listeners. SSE
would need an HTTP server that this process otherwise has no reason to run.
`display.html` opens straight off disk as a `file://` page; WebSocket has no
CORS preflight, so a `null` origin connects to localhost without a server to
serve the page itself.

**Phase 3 (deferred)** — post-call export: Tier 2 research pass over the
saved transcript, grounded citations, written to a study-guide markdown
file. Natural feed target: a story bank / interview-prep doc, not a
standalone product.

## Open items / unresolved

- ~~Deepgram vs AssemblyAI not chosen.~~ **Resolved: Deepgram.** Keyterm
  prompting is measurably load-bearing (§ Priming the recogniser), and nothing
  since has given a reason to revisit it.
- How the prep pack gets assembled per interview (manual copy / symlink /
  build step) — undefined.
- Whether 3 display cards is the right number, or 1.
- Same-LAN is a confirmed assumption (both laptops on the same network) —
  no tunnel planned for v1.
- **Registry cleanup policy not set up.** CI pushes a `:<short-sha>` tag on
  every run to both GitLab Container Registries (`gloss/b-server`,
  `gloss-e2e/mock-deepgram`, `.../mock-listener`, `.../mock-display`) alongside the
  moving `:main` cache tag, and nothing ever deletes old SHA tags — they
  accumulate indefinitely. Images are small so this isn't urgent, but
  GitLab has a built-in per-repository expiration policy (Settings → Packages
  and registries → Container registry → Cleanup policy — keep N most recent,
  expire tags older than X, regex to exclude `main`) that should get turned
  on for all three repositories at some point.

  **This got sharper on 2026-08-30.** The `gloss-e2e` SHA tags are no longer
  just history: gloss's `e2e` job pulls the three doubles *at the tag named by
  `E2E_REF`*, so a cleanup policy that expires old SHA tags would break the
  pin — an older gloss commit would stop being re-runnable, and the failure
  would arrive as a pull error long after the deletion that caused it. Any
  policy set on `gloss-e2e` must exclude tags a live `E2E_REF` points at, or
  keep enough recent tags to cover the pin's lag. `gloss/b-server`'s SHA tags
  carry no such constraint — nothing pins them.
- **A pre-commit gate on what may be committed at all — built 2026-08-30.**
  `tools/check_committable.py`, with `hooks/pre-commit` as the local half and
  the same file run with `--tree` in CI as the half that cannot be skipped.
  The reason it exists: `origin` carries two push URLs, so one `git push`
  publishes to GitLab *and* to the public GitHub mirror at the same moment.
  There is no window between committed and public, and `git rm` does not
  unpublish. The stake is prep packs above all — they name a company and a
  person, and per `sessions/README.md` the documents behind them routinely
  carry a salary floor and a walk-away number.

  Three checks, all failing closed. **The path allow-list** is the primary one
  and refuses anything not named in `ALLOWED`, which is what catches `git
  add -f`, a directory nobody has ignored yet, and a stray file. `.gitignore`
  covers the case somebody thought about; this covers the rest. **Credential
  shapes** run over every added line — private keys, vendor prefixes, hex keys,
  assignments. **Prep-pack leak patterns** reuse `check_pack.py`'s
  `LEAK_PATTERNS` table rather than copying it, and apply to `sessions/` only:
  everywhere else that table fires on prose *about* the risk, which this very
  paragraph would trip.

  Installed per checkout with `git config core.hooksPath hooks`, because
  `.git/hooks` is not version-controlled and a hook that lives there protects
  exactly one clone. `--no-verify` skips it in one flag, which is why the same
  checker runs in the `test` job over every tracked file.

  **Two holes were found by proving it rather than by reading it, and both
  passed a planted key through a hook that reported OK.** First, the exemption
  for `api_key=DEEPGRAM_API_KEY` — a reference is not a literal — was written
  as `^[A-Za-z_][A-Za-z0-9_]*$`, and a 40-character hex key *is* a valid
  identifier. Second, and worse because it disabled two rules at once, the name
  match began with `\b`: a key name is almost always a suffix
  (`DEEPGRAM_API_KEY`, `GITLAB_REPO_TOKEN`), and `_` is a word character, so
  there is no boundary before `API_KEY` and neither rule ever fired. The
  planted-case suite is five files — a stray file, a `.env` backup, a
  force-added prep pack, an `sk-ant-` key and a hex key — and all five are now
  refused by name while an ordinary source change passes.

  **What it does not catch, stated rather than implied:** a real company name
  used as an example, a salary figure paraphrased into prose with no digits,
  an all-letter secret with no digits in it, and a transcript excerpt in a test
  fixture. Every one of those is ordinary text to a regex.

  **Medium term, as its own phase: the second layer should read, not match.**
  Everything above is patterns, and patterns only refuse what somebody already
  thought to name. What they cannot see is the category that actually matters
  here — a real company name used as an example in a doc, a salary target
  paraphrased into prose so no number appears, a transcript excerpt pasted into
  a test fixture because it was convenient, an interviewer's name in a commit
  message, a doc asserting a control the code does not implement. Every one of
  those is ordinary text by every regex and obvious to anything that reads.

  So: an agent over the staged diff, answering one question — *would this be
  fine on a public GitHub repository?* — and refusing with the specific line
  and the reason. Four constraints, all of which follow from what it is:

  - **It runs on pre-push, not pre-commit.** Reading is slow, and pushing is
    the event that actually publishes; a ten-second gate on every commit gets
    disabled within a week, which is a control that removes itself.
  - **It is never the only layer, and never relaxes the first one.** An LLM
    judgement is not reproducible, so it goes second: the deterministic gate
    refuses what it can enumerate, and the reader is there for the rest. If
    they disagree, the refusal wins.
  - **It fails closed.** No key, no network, model errors, a response that will
    not parse — all of those are a refusal to push, not a pass. "Could not
    check" and "checked and clean" must never look the same, which is the exact
    failure this whole item exists to prevent.
  - **It has to be free.** There is no budget for a paid API on this project,
    so the options are a local model, Claude Code invoked on the diff as part
    of the commit routine, or CI-only. Whichever it is, the cost per push
    belongs in this spec before it is built, not after.

  One thing to design for rather than discover: the diff is text the model
  reads as input, and a prep pack can contain arbitrary prose. Treat staged
  content as data, never as instructions to the reviewer.

  Prove this one the same way, with cases regex cannot reach: a paraphrased
  salary expectation with no digits in it, a real employer name in an example
  pack, and a clean refactor that must pass. If it cannot tell the third from
  the first two, it is not ready to gate anything.

### Cannot be verified without hardware

Almost everything in this document is measured. These are the things that
structurally cannot be, listed together so the gap is visible rather than
scattered — none of them is blocked on code, and all of them fail in ways the
test suite cannot see.

- **Which keys the presenter clicker actually sends.** Phase 4a is tested with
  synthetic key events in headless Chromium, so the *behaviour* is proven and
  the *mapping* is not. Pair the device, focus `display.html`, press each
  button. If a button emits something outside the map in § 4a, it is a one-line
  addition. Deferred deliberately 2026-08-25.
- **Two-laptop capture end to end.** The E2E suite proves the audio pipe with a
  mock listener and a mock Deepgram; live runs have used recorded or synthetic
  audio on one machine. Real WASAPI loopback on Laptop A, over the LAN, into
  real Deepgram, has not been exercised by anything automated.
- **Real end-to-end latency.** Every latency figure here is the enrichment call
  measured at the server. Capture, network and STT are not in those numbers, and
  the budget that matters to a person in a conversation includes all of them.

## Superseded work (do not revive)

- **`~/files/unravel`** (Hack Midwest 2024 Flutter app). Mic-only capture
  (can't hear the far side of a remote call), client-side-signed Bedrock
  credentials that are almost certainly revoked, a record button that's a
  hardcoded no-op, and a chunk loop that double-processes every utterance.
  Compiles, but four fatal blockers for this use case.
- **`~/files/reunravel/docs/REVIVAL_PLAN.md`**. 68 sessions / 12 phases
  specifying FastAPI + WebSockets + SQLite + Qdrant + Ollama + whisper.cpp
  + a Flutter rewrite. Over-built for a personal tool; its one durable
  idea (model emits search queries, backend resolves real URLs, never
  trust model-invented links) is kept and moved into Phase 3 above.
- Five `unravel*` directories exist on disk (`unravel/`, `unraveled_poc/`,
  `unravel_enrich/`, `unravel-py/`, `reunravel/`) — only the main
  `unravel/` app and `reunravel/`'s docs have been reviewed. 14 other
  branches on `unravel` (`ethompson/bedrock-wip`, `ethompson/stt-wip`,
  `ethompson/media-display-wip`, `sakshi-bedrock-transcription`,
  `sakshi-speech-to-text-and-chunk`, `kdb/service`, etc.) were never read
  and may contain more complete partial work.

## Separately resolved: recording as a fallback

Not part of this app, but already set up on this Mac: OBS
(`~/Library/Application Support/obs-studio/basic/scenes/Untitled.json`)
configured with `sck_audio_capture` (system audio via ScreenCaptureKit),
`coreaudio_input_capture` (mic), and `screen_capture`. Unverified:
`kTCCServiceScreenCapture` grant — needs a dry-run test call, and OBS must
be restarted after granting. `ffmpeg` is not installed
(`brew install ffmpeg` needed to pull audio out of the MKV output).
