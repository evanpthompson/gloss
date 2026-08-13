# Interview Copilot — Spec

Personal tool. Not a product. Supersedes the `unravel` hackathon app and the
`reunravel/docs/REVIVAL_PLAN.md` 68-session plan — both are considered dead;
see "Superseded work" below.

## Problem

Being interviewed is cognitively saturating. Two things would help live: (1)
instant recall of your own prep — stories, facts, open questions — matched to
whatever was just asked, and (2) a flag when the interviewer uses a term you
don't recognize, so you can ask instead of bluff. Both have to cost about one
second of glance, or they're worse than not having them.

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
│    A2DP, not HFP)          │                          │   Tier 1: Haiku + prep pack   │
└──────────────────────────┘                          │        ↓ cards (1–2s)         │
   ~40 lines, no API keys                              │        ↓ SSE                  │
                                                        │   display.html (2nd screen)   │
                                                        └───────────────────────────────┘
```

Three files plus per-interview prep:

```
a_listener.py     ~40 lines, runs on Windows, no secrets
b_server.py       ~150–200 lines, runs on the Mac, holds all API keys
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
Tier 1: put the whole prep pack in the Haiku system prompt with a cache
breakpoint, let the model do the matching as part of its normal job. No
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

**Phase 2 (deferred, not yet specced in detail)** — Tier 1 enrichment:
prep-pack system prompt, turn-end trigger, card schema, `display.html`
over SSE.

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
  every run to both GitLab Container Registries (`interview-copilot/b-server`,
  `interview-copilot-e2e/mock-deepgram`, `.../mock-listener`) alongside the
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
