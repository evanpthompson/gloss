# Gloss

A live glossary for jargon-heavy calls. Gloss listens to both sides of a
remote conversation on a second machine and surfaces two things at a glance:
notes you wrote before the call, matched to what was just said, and a flag on
any term you don't recognize. It does not answer for you — see `SPEC.md`
§Name for why it isn't called what it used to be called.

**Status as of 2026-08-27.** Phases 1–3 are complete and Phase 4a is built:
audio pipe, turn-end enrichment with cards on a second screen, prompt caching,
a three-link provider chain whose floor is two local indexes — your prep pack
for `recall`, a book-built glossary for `jargon` — cards that persist while
their topic is live, and a presenter clicker to drive them. Both vendors can be
out and the call still gets cards. Phase 4 is complete: the HUD ships behind
`?mode=wheel` as a tightened proof of concept on the second screen it was
measured on. The transparent overlay it argues toward is a window problem rather
than a page one and is Phase 5, not started. 226 tests gate all of it in CI.

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
open "display.html?mode=wheel"   # the same cards as a HUD — see below
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

## Local knowledge base

The chain's last link is not a model. It answers from two indexes held in
memory — no network, no tokens, ~22µs:

| index | built from | emits | needs setup? |
|---|---|---|---|
| notes | your prep pack, indexed at startup | `recall` | no |
| glossary | books on disk, read once offline | `jargon` | yes, below |

The notes index needs nothing: point `GLOSS_SESSION` at a pack and it works.
Write the pack in **headed sections** — a card is named by its heading, and text
before the first heading is not indexed. See `sessions/README.md`.

### The glossary half (optional)

Built offline from books on disk. Build it once:

```bash
uv run python tools/build_kb.py ~/files/automation/resources/project_books --estimate
uv run python tools/build_kb.py ~/files/automation/resources/project_books
```

`--estimate` prints the cost and sends nothing. The build refuses to start
above `--max-cost` (default $2). The output is `kb/glossary.json`, which is
gitignored: it is reproducible from the builder, and it is derived from books
that are not ours to redistribute.

Without it the chain still keeps its `local` link, serving `recall` from your
pack alone. The link is dropped with a warning only when both halves are empty
— no glossary built *and* a pack with no headed sections.

## Glance test (Phase 4b)

Before any HUD code is written, `SPEC.md` § 4b calls for a measurement: is a
one-word card taken in faster than the card that ships today, and does moving
the text help or hurt? The instrument is one file, no server, no dependencies:

```bash
open tools/glance_test.html      # then fullscreen it on the second screen
```

Sit at the distance you actually sit — size is the variable. About six minutes
for 28 trials. Press SPACE when a card has landed, then answer two questions.
Results come out as a table and as JSON to paste into `SPEC.md`.

Two runs are recorded there. **Read § 4b before trusting the accuracy columns:**
a timeout leaves the card up for the full ceiling, so those two probes measure
exposure rather than legibility and are withdrawn. Response rate and reaction
time are unaffected and are what the conclusions rest on.

```bash
open tools/exposure_test.html   # run 3: the current instrument
```

**Run this one, not the glance test.** The card appears for a fixed time,
disappears behind a mask, and then you answer two questions — one about the
term, one about an arbitrary specific only the card could have told you. Fixed
exposure is what makes the accuracy numbers mean anything; the glance test is
kept as the record of runs 1–2, not for new work. About five minutes.

**Run 3 has been run (2026-08-28) and its fact column is void** —
`tools/results/exposure-run3-2026-08-28.json`. The timing held (drift ≤17 ms,
median 0), but the probe leaks: most `factDistractor` values are absurd by an
order of magnitude, so the answer is recoverable from domain sense without the
card. The two conditions that display *no detail at all* scored 91% on those
pairs and 20% on the pairs where both options were plausible — and the condition
that *does* display the detail scored the same as the ones that do not. See
`SPEC.md` § 4b. **The fix is better probe pairs, not more trials**; the corpus
needs rewriting before a re-run is worth anyone's five minutes.

```bash
open tools/wheel_hud.html       # the display both participants preferred
```

A prototype, not a result: one term per row, wheel or arrow keys or the clicker
to move, five rows with the focus at full opacity and its neighbours fading
out, detail on dwell. Sliders along the bottom set the opacity gradient and the
dwell delay and print the settings as JSON. Nothing here has been measured.

### The tools, and which is current

| file | what it is |
|---|---|
| `tools/exposure_test.html` | **run 3, current.** Fixed exposure, masked, arbitrary-fact probe |
| `tools/glance_test.html` | runs 1–2. Self-terminated; its accuracy columns are withdrawn |
| `tools/wheel_hud.html` | the prototype the HUD came from. Superseded by `?mode=wheel`; kept because its sliders are how the numbers were found |
| `tools/cards_corpus.js` | the 32 cards all three share, and what each probe does not measure. **Its `fact` pairs are known broken — see `SPEC.md` § 4b run 3** |
| `tools/results/` | raw run JSON, kept for provenance rather than re-typed into prose |

## HUD mode (`?mode=wheel`)

The display both run-2 participants preferred, folded into `display.html`
behind a flag. Same file, same WebSocket, same cards — the second screen is
still what you get with no flag, and nothing about it changed.

```bash
open "display.html?mode=wheel"
open "display.html?mode=wheel&stealth=40&near=90&far=25&dwell=900&wheel=6"
```

One term per row, stacked, with the focus row at full size and its neighbours
fading out. Wheel, arrow keys, PageUp/PageDown or the clicker move the focus.
Detail for the focused row appears after a dwell, and leaves the instant the
focus moves — a detail line that survives a move is a line read about the wrong
card.

| param | default | what it does |
|---|---|---|
| `stealth` | `100` | opacity of the whole HUD, 15–100. The status dot is exempt |
| `near` | `85` | opacity of the rows either side of the focus |
| `far` | `45` | opacity of the rows two out |
| `dwell` | `600` | ms of stillness before the detail line appears; `0` for instant |
| `wheel` | `4` | wheel sensitivity, 1–10 |

A bad value falls back to its default rather than to `NaN`. A HUD rendered
invisible because `?far=abc` quietly became nothing is exactly the silent
failure this display exists not to have.

**It is not the `scroll` condition that finished 0-of-14 in the glance test.**
That one moved on its own and looped, so it never ended and nobody ever
finished it. This one is still until you move it and stops where you stop.

**What is decided but not measured**, so it is the first thing to argue with:

- **A new card takes the focus.** The HUD renders exactly one row legibly, so a
  focus that lags the conversation would make it worse than the screen it
  replaces — the card for the question just asked would be the dim one. The
  wheel takes the focus straight back.
- **The opacity gradient** is Direction 1, which no run has touched.
- **The kind mark shrinks to one word** (`term` / `yours`); the border colour is
  what carries kind on the HUD. An error card is exempt from that and from the
  fading and the culling, because "down" and "scrolled out of view" must never
  look the same.

### It is not a transparent overlay, and a browser cannot make it one

`SPEC.md` § 4b names a transparent overlay as the direction of travel. A web
page cannot get there on its own: `background: transparent` in a tab is
composited against the browser's own opaque base, and there is no web API for
window transparency, always-on-top, or click-through. `stealth` fades the
*content* against a solid background, which is the right effect on a second
screen and would be a grey rectangle over a video call.

That is a **window** problem, not a page problem, and the page is the part that
survives it — Electron, Tauri and pywebview all render this same file unchanged.
So Phase 4 stops here on purpose, on the screen the HUD was measured on. The
overlay is **Phase 5**: transparent and frameless, above a full-screen call,
click-through, and **excluded from screen capture** — without that last one,
sharing your screen puts your prep notes in the capture, which inverts the tool
rather than degrading it. `SPEC.md` § Phase 5 carries the shell comparison, the
two prerequisites, and what would prove it rather than demo it. Not started.

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
| `test_recall.py` | unit | the prep-pack index — mostly what it refuses to answer |
| `test_eviction.py` | unit | the transcript ceiling, and what crossing it costs |
| `test_display.py` | browser | card lifecycle and HUD mode in `display.html`, in real Chromium |

The end-to-end audio pipe test lives in the separate `gloss-e2e` project; it
needs two containers and a mocked Deepgram, and CI triggers it after this
project's own tests pass.

## Next (not built)

- **Phase 5 — the overlay window.** The HUD ships on the second screen
  (`?mode=wheel`, above); what is left is a window that can sit over the call
  instead of beside it — transparent, frameless, above a full-screen call,
  click-through, and excluded from screen capture. The page does not change; the
  phase is the shell. `SPEC.md` § Phase 5 has the comparison, why capture
  exclusion is the row that decides it, and the two things to settle first.
- **Rewrite the 32 `fact`/`factDistractor` pairs in `tools/cards_corpus.js`,
  then re-run run 3.** Run 3 happened and its detail column is void — the
  distractors are implausible, so the probe is answerable without the card
  (`SPEC.md` § 4b). Nothing about how much text a card can carry is settled, and
  it gates Phase 5.
- **Tier 2 post-call research export.** Unchanged from the original plan and
  still not started.
- **Expand / go deeper on a card.** Raised 2026-08-28; **judged not essential to
  the MVP** and specced in `SPEC.md` § Card interaction with the reasoning — the
  clicker has no free button, a mid-call vendor round trip is a new failure mode
  on the interactive path, and Tier 2 already covers depth where it is free to
  be slow. If it is built, the shape to build is *local-only expansion* (show
  more of the prep-pack section or glossary entry the card came from), not a
  second model call. Tiling and pinning, raised alongside it, are already built
  — see the same section.
- **Physical verification.** Clicker keycodes, two-laptop capture, real
  end-to-end latency — `SPEC.md` § "Cannot be verified without hardware".

See `SPEC.md` for the reasoning behind every design choice here, and for the
things deliberately not built with the measurements that justified it.

## License

MIT — see `LICENSE`.

The license covers this code. It does not extend to whatever you point
`tools/build_kb.py` at: the glossary is built locally from books you supply,
`kb/` is gitignored, and no book text is redistributed here.
