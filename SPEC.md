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

**The Bluetooth trap.** The instant anything opens the headset's mic, the
whole link drops to HFP and output collapses to ~16 kHz mono — degrading
transcription of the interviewer too, not just the user. Dodge: laptop
built-in mic for the user's voice, Bluetooth for output only. The headset
stays on A2DP.

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

Three cards maximum, newest pinned to the top so the eye lands in the same
place every time, older cards fading after ~90 seconds. No transcript.

```
┌────────────────────────────────────┐
│ ▸ Story 5 — integration platform   │  from prep pack, instant
│   Camel/MuleSoft buy-vs-build      │
├────────────────────────────────────┤
│ ▸ GRIFFON                          │  jargon flag, unresolved
│   unfamiliar — ask them            │
└────────────────────────────────────┘
```

Card schema from Tier 1:

```json
{"cards": [{
  "kind": "story | term | fact | question",
  "label": "≤ 7 words",
  "detail": "≤ 20 words"
}]}
```

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
GLOSS_PROVIDER=deepseek       # deepseek-v4-flash, implicit caching, floor unverified
GLOSS_PROVIDER=ollama         # llama3.2, nothing leaves the machine
GLOSS_PROVIDER=fake           # canned cards, mocked E2E suite only
GLOSS_PROVIDER=google_genai   # no longer a target; kept working, unmaintained
```

The list is an **allow-list**: an unnamed provider is refused at startup rather
than attempted, because "unrecognised" and "unsupported" are the same thing
from a live conversation's point of view and the failure would otherwise land
mid-call. Overriding the pinned model is allowed but must pin its kwargs too —
an unpinned kwargs override follows you onto the next provider and fails there.

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

For contrast, on gemini-3.6-flash a 5,168-token byte-identical prefix reported
`cache_read=0` across four consecutive calls despite a documented 4,096 floor.
LangChain surfaces the field there too, so that was Gemini not caching rather
than a counter not plumbed.

The one thing that does *not* abstract away is the prompt: prefix caching is
prefix caching everywhere, so `SYSTEM_PROMPT` is built once at startup and the
volatile transcript always goes last, whichever provider is configured.

**Noise control:**
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

- Deepgram vs AssemblyAI not chosen (leaning Deepgram — endpointing
  controls + keyterm prompting).
- How the prep pack gets assembled per interview (manual copy / symlink /
  build step) — undefined.
- Whether 3 display cards is the right number, or 1.
- Same-LAN is a confirmed assumption (both laptops on the same network) —
  no tunnel planned for v1.
- **Registry cleanup policy not set up.** CI pushes a `:<short-sha>` tag on
  every run to both GitLab Container Registries (`gloss/b-server`,
  `gloss-e2e/mock-deepgram`, `.../mock-listener`) alongside the
  moving `:main` cache tag, and nothing ever deletes old SHA tags — they
  accumulate indefinitely. Images are small so this isn't urgent, but
  GitLab has a built-in per-repository expiration policy (Settings → Packages
  and registries → Container registry → Cleanup policy — keep N most recent,
  expire tags older than X, regex to exclude `main`) that should get turned
  on for all three repositories at some point.

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
