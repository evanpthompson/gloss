# Gloss

A live glossary for jargon-heavy calls. Gloss listens to both sides of a
remote conversation on a second machine and surfaces two things at a glance:
notes you wrote before the call, matched to what was just said, and a flag on
any term you don't recognize. It does not answer for you — see `SPEC.md`
§Name for why it isn't called what it used to be called.

**Status as of 2026-08-25.** Phases 1–3 are complete and Phase 4a is built:
audio pipe, turn-end enrichment with cards on a second screen, prompt caching,
a three-link provider chain ending in a local glossary, cards that persist while
their topic is live, and a presenter clicker to drive them. Phase 4b (HUD mode)
is specced and not built. 194 tests gate all of it in CI.

`SPEC.md` carries the design and every measurement behind it, including the
things deliberately **not** built and the data that justified not building them.
`PHASE-3-PLAN.md` has the session-by-session record.

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

`a_listener.py` needs no API keys and never talks to Deepgram or any model
provider directly — it only ever talks to Laptop B.

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

## Phase 2 — Tier 1 enrichment

Cards on a second screen, driven by the interviewer's end-of-turn.

**0. Pick a provider.** Defaults to **Anthropic** (`claude-haiku-4-5`) with
DeepSeek and the local glossary behind it — get a key at
[console.anthropic.com](https://console.anthropic.com/settings/keys) and put it
in `.env` as `ANTHROPIC_API_KEY`. `DEEPSEEK_API_KEY` is required too unless you
set `GLOSS_FALLBACKS=` empty: an unproven fallback is one you discover during
the outage it exists for, so the server refuses to start without it.

Every provider is one row in `providers.py` — model id, kwargs, key variable and
cache floor together, so they cannot drift apart. An unlisted provider is
refused rather than attempted.

> **Historical, kept because it explains the default.** gloss ran on Gemini
> until 2026-08-24. Measured 2026-08-23: the `gemini-3.6-flash` free tier is
> **20 requests per day**, against a design that budgets ~15–25 calls *per
> hour* — one conversation exhausts a day. Chasing a free tier was dropped as a
> goal at that point; a paid primary at pennies an hour is the cheaper answer
> once your own time is counted. `google_genai` still works and is unmaintained.

**Providers are an allow-list, not a free-text setting.** Each is one row in
`providers.py`, and an unlisted one is refused at startup rather than attempted
— "unrecognised" and "unsupported" are the same thing from a live conversation's
point of view, and the failure would otherwise land mid-call. Adding one means
adding a row with its model id, kwargs, key variable and cache floor looked up:

```
GLOSS_PROVIDER=anthropic      # default — claude-haiku-4-5, cache breakpoint, floor 4096
GLOSS_PROVIDER=deepseek       # deepseek-v4-flash, implicit caching, floor 128 (measured)
GLOSS_PROVIDER=local          # the offline glossary; no model, no network, no tokens
GLOSS_PROVIDER=ollama         # llama3.2, nothing leaves the machine
GLOSS_PROVIDER=fake           # canned cards, mocked E2E suite only
```

A failing call now fails fast rather than retrying: `GLOSS_MAX_RETRIES=1` and
`GLOSS_TIMEOUT_S=10`. A quota 429 was measured blocking a single call for 40.4s
across four backoff retries — mid-conversation that is worse than failing,
since the next turn supersedes the request anyway.

**1. Build a prep pack.** One directory of `*.md` per conversation, loaded in
sorted filename order. Ask Claude Code for the `gloss-prep` skill rather than
writing one by hand — it assembles from your existing story bank and prep docs,
and gates the result:

```
uv run python tools/check_pack.py sessions/my-call
```

That gate is not optional politeness. A pack becomes the system prompt, so
anything in it can appear on screen mid-call — and real prep docs are full of
salary floors and negotiation scripts that must never get there. It also checks
the pack is long enough to cache; see `sessions/README.md`.

**2. Run the server against it:**

```
GLOSS_SESSION=sessions/my-call uv run b_server.py
```

**3. Open the display** on the second screen. No web server needed — it's a
`file://` page that opens a WebSocket back to `b_server.py`:

```
open display.html
```

It reconnects on its own, so restarting the server mid-session is fine.

**What you should see.** Most turns produce nothing — that's designed, not
broken. When a turn does produce cards, the console shows them alongside the
transcript, and the first enrichment of each run reports the cache counters:

```
Preflight OK: google_genai:gemini-3.6-flash answered and matched the card schema
[interviewer/FINAL +12.3s] how do you think about multi-tenancy at the data layer
Tokens: in=2140 out=64 cache_read=0
[cards + 1.2s] [{'kind': 'recall', 'label': 'Request coalescing story', ...}]
```

`cache_read=0` on every turn means the prep pack is under the provider's
minimum prefix size and each turn reprocesses the whole thing — see
`sessions/README.md`.

A red card means the call failed, and it **names the reason** — "Provider out
of credit", "API key rejected", "Model provider overloaded" — because someone
glancing at a second screen mid-conversation can act on those and cannot act on
an exception class. That card exists at all because zero cards is the *normal*
state here, so a silent failure would be invisible for the rest of the call.

**Tuning** (all optional, all documented in `.env.example`): `GLOSS_PROVIDER`,
`GLOSS_FALLBACKS`, `GLOSS_MODEL`, `GLOSS_MODEL_KWARGS`, `GLOSS_MAX_CARDS`,
`GLOSS_MIN_CHARS` (how short an interviewer turn has to be before it counts as
turn-taking rather than a question), `GLOSS_MAX_TRANSCRIPT_TOKENS`,
`GLOSS_CARD_TTL_S`, `GLOSS_ERROR_TTL_S`, `GLOSS_PREVIEW`, `GLOSS_KB`,
`GLOSS_KEYTERM_BUDGET`, `GLOSS_CACHE_TTL`, `GLOSS_LOG_CACHE`.

## Local knowledge base (optional)

The chain's last link answers from a glossary built offline from books on disk
— no model, no network, no tokens. Build it once:

```bash
uv run python tools/build_kb.py ~/files/automation/resources/project_books --estimate
uv run python tools/build_kb.py ~/files/automation/resources/project_books
```

`--estimate` prints the cost and sends nothing. The build refuses to start
above `--max-cost` (default $2). The output is `kb/glossary.json`, which is
gitignored: it is reproducible from the builder, and it is derived from books
that are not ours to redistribute.

Without it, the `local` link is dropped with a warning and the chain runs on
its vendors alone.

## Clicker control

A Bluetooth presenter clicker drives the display — it is an HID keyboard, so
`display.html` just listens for the keys it sends. Nothing to install.

| key | effect |
|---|---|
| `→` `PageDown` `space` | select next card |
| `←` `PageUp` | select previous card |
| `Enter` `p` | pin / unpin (a pinned card ignores its TTL) |
| `Backspace` `Delete` `x` | dismiss |
| `b` `.` `F5` | blank / unblank the screen |
| `Esc` | clear selection |

Requires the display to have keyboard focus, which it does on a dedicated
second screen. On a machine where the video call is focused, the keys go to the
call instead.

## Tests


```bash
uv sync                       # dev dependencies included
uv run pytest                 # 194 tests, ~30s
uv run ruff check .
```

**The whole suite is offline.** No API keys, no network, no spend — integration
tests run `b_server` against a local fake vendor that speaks both wire formats
(`tests/fake_vendor.py`). That is deliberate: a gate that needs a funded account
is a gate that gets skipped the first time it is inconvenient, and this one runs
in CI where merges actually happen.

The browser tests need Chromium (`uv run playwright install chromium`). Without
it they skip locally — but in CI, where `GLOSS_REQUIRE_BROWSER_TESTS=1` is set,
a missing browser fails the job instead. A check that could not run is not a
pass.

| file | layer | covers |
|---|---|---|
| `test_failures.py` | unit | every documented vendor error → its meaning |
| `test_keyterms.py` | unit | what gets primed into Nova-3, and what must not |
| `test_prompt_shape.py` | unit | the block structure prefix caching depends on |
| `test_cards.py` | unit | the card contract, and what gets dropped |
| `test_providers.py` | unit | the provider allow-list and its refusals |
| `test_chain.py` | integration | the fallback chain through the real SDKs |
| `test_kb.py` | unit | the local glossary — mostly what it refuses to answer |
| `test_eviction.py` | unit | the transcript ceiling, and what crossing it costs |
| `test_display.py` | browser | card lifecycle in `display.html`, in real Chromium |

The end-to-end audio pipe test lives in the separate `gloss-e2e` project; it
needs two containers and a mocked Deepgram, and CI triggers it after this
project's own tests pass.

## Next (not built)

- **Phase 4b — HUD mode.** Cards as a heads-up surface rather than a second
  screen: meaning carried by form and position before any word is read, and
  ultimately a transparent overlay. Specced in `SPEC.md` § 4b, with the reading
  research that constrains it and a test to run before writing any code.
- **Recall cards from the prep pack when every vendor is out.** The local
  glossary serves `jargon` only, so a total outage leaves the notes written for
  that specific call unreachable — which is backwards. Small: the pack is
  already loaded at startup and `kb.Glossary` has the lookup shape to copy.
- **Tier 2 post-call research export.** Unchanged from the original plan and
  still not started.
- **Physical verification.** Clicker keycodes, two-laptop capture, real
  end-to-end latency — `SPEC.md` § "Cannot be verified without hardware".

See `SPEC.md` for the reasoning behind every design choice here, and for the
things deliberately not built with the measurements that justified it.
