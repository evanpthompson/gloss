# Gloss — Phase 1

A live glossary for jargon-heavy calls. Gloss listens to both sides of a
remote conversation on a second machine and surfaces two things at a glance:
notes you wrote before the call, matched to what was just said, and a flag on
any term you don't recognize. It does not answer for you — see `SPEC.md`
§Name for why it isn't called what it used to be called.

**Phase 1 (this) proves the pipe:** audio captured on Laptop A (Windows, hosts the call)
arrives on Laptop B (this Mac) as two correctly-labeled live transcript
streams. Nothing else — no enrichment, no display, no LLM calls. See
`SPEC.md` for the full design and what Phase 2/3 add later.

## Setup

Both machines need [uv](https://docs.astral.sh/uv/).

```
uv sync
```

installs everything in `pyproject.toml` on either machine. `soundcard` is
cross-platform (WASAPI on Windows, CoreAudio on Mac/Linux) so this is safe
to run on both, even though each script only uses half the dependencies.

**Laptop B (Mac):**

```
cp .env.example .env
# put your Deepgram API key in .env
uv run b_server.py
```

It listens on `ws://0.0.0.0:8765/interviewer` and `.../user`. Find this
Mac's LAN IP with `ipconfig getifaddr en0` (or the relevant interface).

**Laptop A (Windows):**

```
uv sync
$env:B_SERVER_URL = "ws://<laptop-b-lan-ip>:8765"
uv run a_listener.py
```

`a_listener.py` needs no API keys and never talks to Deepgram or Anthropic
directly — it only ever talks to Laptop B.

## What "working" looks like

On Laptop B's console you should see two labeled transcript streams as you
talk and as the interviewer talks, each tagged `interviewer` or `user`,
with an elapsed-time marker so you can eyeball end-to-end latency:

```
[interviewer/FINAL  +12.3s] can you walk me through your approach to multi-tenancy
[user/interim       +14.1s] sure so the way we handled that at
```

## Known constraints for Phase 1

- **Same LAN required.** No tunnel — confirmed assumption, not yet solved.
- **Built-in mic only for the user's voice.** Never let the Bluetooth
  headset's mic activate — see the comment in `a_listener.py` for why.
- **Default output device = "the interviewer."** `a_listener.py` assumes
  the call app (Teams/Zoom/etc.) is playing through the OS default output
  device. If it isn't, loopback will capture the wrong (or silent) source.
- No reconnect state/backfill — if a WebSocket drops, `a_listener.py`
  reconnects and resumes capturing, but Deepgram gets a fresh connection
  on the Mac side, so a few seconds of transcript are lost at the seam.

## Next (deferred, not built yet)

- Tier 1 enrichment (Haiku + prep pack, turn-end trigger, card schema)
- `display.html` glanceable second screen over SSE
- Tier 2 post-call research export

See `SPEC.md` for the reasoning behind every design choice here.
