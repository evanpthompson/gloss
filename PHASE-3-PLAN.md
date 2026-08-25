# Phase 3 — Plan

Rewritten 2026-08-24. **This document replaces an earlier version of itself**
(commit `e82d7cf`) whose premise was overturned within an hour of being
written. The reversal is recorded in § "What the pivot deleted" rather than
quietly edited out, because the reasoning that produced the wrong plan is worth
being able to re-read.

Scope decisions made under ponytail rules: the first rung of the ladder that
holds, wins. This document records what is being built, what is deliberately
**not**, and why for both.

Read `SPEC.md` § "Phase 2 design" first. Phase 3 changes nothing about the
trigger, the card kinds, or the transport.

---

## The problem Phase 3 exists to solve

Phase 2 works and is measured: 1.3–1.9s per turn, correct cards. It has two
things wrong with it.

**One: it has amnesia.** `transcript` is a `deque(maxlen=6)`. A term defined in
minute three is gone by minute five, so the tool cannot do the one thing a
glossary is for — connect a thing said now to the thing it referred to earlier.
This is the real defect, and no amount of quota engineering touches it.

**Two: the free tier cannot hold a conversation.**

```
Quota exceeded: generate_content_free_tier_requests
limit: 20, model: gemini-3.6-flash    (per day, per project, per model)
```

The earlier plan treated (two) as the problem and (one) as acceptable. That was
backwards. Free tiers are structurally hostile to the thing gloss needs —
resending an entire growing conversation every turn — and the previous plan
contradicted itself by demanding a shrunk prompt *and* full history at once.

The constraint driving every decision below: **the model must see the whole
conversation, every turn, inside a 1–2s budget, and the way that is affordable
is prompt caching, not a smaller prompt.**

---

## What is being built

### 1. Caching-first prompt shape

Every turn sends the entire conversation. Almost all of it is a cache read.

```
system   = [ INSTRUCTIONS + prep pack ]          <- breakpoint, ttl "1h"
messages = [ turn 1, turn 2, ... turn N ]        <- breakpoint on turn N
```

Each call reads the whole prior conversation from cache at 0.1x input price and
pays full price only on the newest turn. The prefix is already frozen —
`SYSTEM_PROMPT` is built once at `b_server.py:154` and `SYSTEM_MESSAGE` at
`:248` precisely so its bytes never move. Phase 3 adds the breakpoints that
turn that discipline into money.

**Verified costs** (fetched from the providers' pricing pages 2026-08-24, not
recalled). One hour, 20 enrich calls, 4k-token prefix, history growing to ~12k,
~250 output tokens per call:

| | Haiku 4.5 | DeepSeek V4-Flash (off-peak) |
|---|---|---|
| Base input | $1.00 /MTok | $0.22 /MTok (cache miss) |
| Cache read | $0.10 /MTok (0.1x) | $0.007 /MTok |
| Cache write | $1.25 /MTok (5m) · $2.00 (1h) | n/a — implicit |
| Output | $5.00 /MTok | $0.66 /MTok |
| **Hour of conversation** | **~$0.07** | **~$0.01** |

Ten conversations is seventy cents. The entire previous plan existed to avoid
that, and cost the feature to do it.

**Two hard constraints that fall out of Anthropic's caching rules, both
confirmed against the docs rather than assumed:**

- **The prep pack must be at least 4,096 tokens.** Haiku 4.5 has the highest
  minimum cacheable prefix of any current Claude model (Opus 5 is 512, Sonnet 5
  is 1,024). Below 4,096 nothing caches, no error is raised, and the counters
  simply read zero. This is why "shrink the prompt" is not a small mistake —
  see § "What the pivot deleted".
- **TTL is 5 minutes by default, and a write does not refresh an earlier
  breakpoint's clock.** A pause longer than five minutes evicts the prefix and
  the next turn pays $1.00/MTok instead of $0.10. Use `ttl: "1h"` on the system
  breakpoint: 2x on write instead of 1.25x, which on a 4k prefix is a rounding
  error, and it removes the cliff entirely.

### 2. Full conversation, bounded by tokens rather than turns

`transcript` stops being `deque(maxlen=WINDOW_TURNS)` (`b_server.py:250`) and
`GLOSS_WINDOW_TURNS` is deleted.

**Not unbounded, though.** Fail closed: an unbounded list is a context-window
overflow waiting for a long call. Haiku 4.5's window is 200K; speech at ~250
tokens per turn reaches that at roughly 800 turns, far past any real
conversation — but "far past" is not "cannot". Bound it explicitly with
`GLOSS_MAX_TRANSCRIPT_TOKENS` (default ~100,000, half the window). On overflow,
drop from the oldest end and **log it**, because dropping the front of the
conversation also invalidates every cache read after it — the cost profile
changes silently otherwise.

### 3. Fallback chain — outage resilience, not quota-dodging

```
anthropic:claude-haiku-4-5     primary   — reliable structured output, 200K
deepseek:deepseek-v4-flash     fallback  — different vendor, different outage
```

**Why Haiku primary.** The card schema is what visibly breaks mid-conversation.
Gemini 3.6 already returns `[{}]` for the zero-card case, which `enrich()` has
to filter at `b_server.py:292`. Anthropic's structured outputs are native and
strict-checkable, which removes the failure mode that actually shows on screen.
Its cost is 7x DeepSeek's and still seven cents an hour.

**Why DeepSeek second and not Ollama.** The earlier plan reasoned that a chain
whose last link can rate-limit has no floor, and put local inference at the
bottom. That reasoning was sound for a quota problem. It is not the problem any
more: with a paid primary, the chain exists for an Anthropic *outage*, and a
second hosted vendor answers that better than local inference that `SPEC.md`
already measured as too slow for the STT half. Ollama stays available as a
`GLOSS_PROVIDER` value; it is not in the default chain.

**Why a chain and not a router.** LangChain's `Runnable.with_fallbacks()` does
cross-provider failover in-process, is already installed, and takes
`exceptions_to_handle` to scope it. LiteLLM's Router is the better tool when
you need per-deployment RPM/TPM accounting — see § "Not building".

**The cost of this choice, stated plainly.** `cache_control` breakpoints are an
Anthropic-specific annotation on message content blocks. DeepSeek's caching is
implicit and takes no such markers. So the prompt builder branches on provider:
one shape with breakpoints, one without. That is a real dent in the
provider-agnostic abstraction Phase 2 built, and it is the price of the
hybrid. Keep the branch in one function, not scattered through `enrich()`.

Also true and worth knowing before it surprises someone: **caches are
model-scoped.** Failing over to DeepSeek starts cold. Fine for an outage,
useless as a quota strategy — which is the whole reason the chain got reframed.

### 4. Card lifecycle — cards persist while a topic is live

Unchanged from the previous plan; it was never the part that was wrong.

Today `render()` replaces every card on each turn, so a topic discussed across
four turns flashes four times and nothing stays readable.

Change: each card carries a stable `id`, the display keeps a map, a repeated id
updates in place, and cards age out on a TTL.

**Why client-side only.** The display already holds the cards. Adding server
state, a card store, or a REST surface would be building `landfall` again
inside gloss. This is roughly 15 lines of JavaScript.

**Why borrow the shape from landfall and not the schema.** `landfall`'s
`docs/card-schema.md` has the right model — `externalId` upserts in place,
`priority` drives a default TTL, `persistent` opts out, `dismissedAt` records
manual dismissal. Copying the whole schema — `layout`, `dataJson`, `source`
namespacing, server-evaluated expiry, history retention — would import a
display server's requirements into a page that shows at most three cards.

Taken: stable id, upsert, TTL. Left: everything else, until gloss has a second
consumer.

---

## What the pivot deleted

Recorded because each of these was argued for at length in the previous
version, and three of the four were wrong for the same reason: they optimised
against a free-tier constraint that the design should never have accepted.

- **The rolling 6-turn window.** This was the amnesia, presented as a budget
  measure. Deleted; see § 2.
- **"Shrink the prompt to ~1,500 tokens."** Called "the highest-value change
  and costs nothing." Under caching it is backwards — a large stable prefix is
  an asset that costs 0.1x after the first turn. Worse, on Haiku 4.5
  specifically, a 1,500-token pack sits *below the 4,096-token cache minimum*
  and would silently disable caching altogether, turning the intended
  optimisation into a permanent 10x cost increase and a latency penalty on
  every turn. The pack guidance in `sessions/README.md` and the
  `CACHE_MINIMUMS["anthropic"] = 4096` constant in `tools/check_pack.py` are
  **correct as written** and need no change.
- **Groq primary.** Chosen for being the fastest free option. With a paid
  primary the selection criterion is reliability of structured output, not
  free-tier throughput. Groq does not cache, which under this design is
  disqualifying rather than neutral.
- **The TPM arithmetic table.** It computed headroom against Groq's 6,000 TPM.
  Not applicable to either chosen provider.

Kept from the previous version, unchanged: card lifecycle (§ 4), and every
entry in § "Not building" below.

---

## Not building, and why

### Chasing a free tier

Cut, explicitly and permanently. This is the pivot stated as a rule: an hour of
conversation costs seven cents on the reliable provider. Any engineering that
exists to avoid that — routing layers, quota accounting, turn gating, prompt
shrinking — costs more in complexity and in feature quality than it saves in
money. If cost ever becomes real, the fix is DeepSeek, which is already the
second link in the chain.

### PageIndex

Cut for now. `github.com/VectifyAI/PageIndex` builds a reasoning-navigable tree
over long documents, which is a genuinely better fit for a story bank than
embedding similarity — "which story answers this question" is a reasoning
problem, and the material has real structure.

It solves a problem gloss does not have. A prep pack is one conversation's
notes: 4,000–8,000 tokens, which fits in a 200K window with room to spare.
Retrieval over a document that already fits is machinery with nothing to do.

**Add it when:** the corpus stops fitting — the "current versions, libraries,
tooling" corpus, which is external, large, and genuinely needs retrieval. That
corpus does not exist yet. Build it first and PageIndex becomes obvious rather
than speculative.

### Per-provider RPM/TPM budget tracking

Cut. Paid tiers on both providers have limits far above ~20 calls/hour, and the
chain already handles the case where one vendor says no. Tracking budgets that
are never approached is bookkeeping for its own sake.

**Add it when:** 429s are observed in a real conversation. Then it is a
measured problem with a known shape, and LiteLLM's Router is the thing to reach
for rather than a hand-rolled bucket.

### A local ML model for routing decisions

Cut, and not merely deferred.

Rate limits are countable, not inferable. Provider budgets are published, spend
is observable, and the next call's cost is estimable from a character count.
Deciding "can this call go to provider X" is a token bucket — microseconds,
deterministic, reproducible when it misbehaves. A model in front of every model
call would add latency to the one path that must be fast, make routing
non-deterministic, and require training data that does not exist.

The instinct behind the request is right — many competing factors — but
weighted scoring over measurable inputs is a scoring function, not machine
learning.

### Turn-importance gating

Cut. Most turns correctly produce zero cards, so most calls spend quota for
nothing — which mattered when quota was 20/day and does not matter at seven
cents an hour. Under caching, a turn that produces no cards is also the
*cheapest* possible call: it is almost entirely cache reads.

**Add it when:** latency, not cost, makes it worth skipping calls.

### Rewriting any of this in Go or Rust

Cut. The work is I/O-bound waiting on HTTP, not CPU-bound, so a faster language
buys nothing — and a separate service adds a network hop and a second process
to supervise inside a 1–2s budget.

**Revisit when:** something becomes CPU-bound. Local model inference and
PageIndex tree search over a large corpus are the two candidates, and both are
separable processes when the time comes.

---

## Sessions

Each is independently shippable and leaves the tool working.

### S1 — Haiku primary, cached prefix

The one that pays for everything else.

1. `uv add langchain-anthropic`; `ANTHROPIC_API_KEY` in `.env` (already named
   in the `GLOSS_PROVIDER=fake` key guard at `b_server.py:188`, so no new
   plumbing).
2. `GLOSS_PROVIDER=anthropic`, `GLOSS_MODEL=claude-haiku-4-5` in `.env`.
3. Add the `cache_control` breakpoint to the system block, `ttl: "1h"`, behind
   a single provider branch. Do **not** set `thinking` — Haiku 4.5 has no
   thinking by default and the 1–2s budget cannot afford it.
4. Verify caching actually happened, from the counters and not from the code
   reading correctly: `cache_read_input_tokens` must be non-zero from turn 2.
   `enrich()` already logs these once per run at `b_server.py:320`; make it log
   every turn behind a debug flag, because a zero on turn 40 is the failure
   this design has to catch.
5. Re-measure latency and record it in `SPEC.md` next to Gemini's numbers.

**Done when:** a smoke run shows `cache_read` non-zero from turn 2 and steady
at the prefix size, and the measured latency is written down. A run where
`cache_read` stays 0 is a **failed** session even if the cards are correct —
that is the whole feature.

*Criterion corrected 2026-08-24, having first been written as "`cache_read`
climbing turn over turn". That is S2's shape, not S1's: with a breakpoint on
the system prefix alone, the prefix is what gets read and the figure is
flat by construction. It only climbs once the newest-turn breakpoint lands and
the conversation itself starts caching. Written down because a criterion that
describes the next session's behaviour would have failed a correct run.*

**Result: PASSED 2026-08-24.** `cache_read=5933` from turn 2 onward across five
turns; `SPEC.md` carries the table. Latency measured at 2.5–3.3s, **outside**
the 1–2s budget — recorded rather than waved through, and the lever (Haiku
emits 158–222 output tokens against Gemini's 46–140) is identified but untried.

### S2 — Full conversation

1. Delete `WINDOW_TURNS` and the `deque` maxlen; append every turn.
2. Add `GLOSS_MAX_TRANSCRIPT_TOKENS` (default 100,000) with oldest-first
   eviction and a warning log when it fires.
3. Move the newest-turn breakpoint so it lands on the last message each turn.
4. Confirm the thing this exists for: define a term in turn 2, refer to it
   obliquely in turn 20, and check the card connects them. Phase 2 cannot do
   this; that is the acceptance test.

**Done when:** the turn-20 callback produces a correct card, and per-turn cost
has not grown superlinearly (read the counters, not the intent).

**Result: PASSED 2026-08-24.** A term defined in turn 2 ("Kestrel", an internal
deploy gate) was correctly recalled at turn 12 and again at turn 6 of a second
run — impossible under the old 6-turn window, which evicted it by turn 8.
`cache_read` climbs 7,038 → 7,382 → 7,730 → 8,079, reaching **99.4% of input
read from cache**, so cost per turn is sublinear in conversation length rather
than superlinear. Latency 1.1–1.7s, back inside budget.

The shape took three attempts, and the two failures are recorded in `SPEC.md`
because both looked correct and both failed silently — `cache_read` stays
pinned at the system-prefix size, which reads as healthy. Anything that
re-merges history between turns, or lets a constant suffix ride on the newest
turn, breaks the byte-prefix match. One block per turn, appended, never
re-merged; breakpoints on the last two; the constant ASK last of all.

### S3 — Fallback chain

1. `uv add langchain-deepseek`; key from `platform.deepseek.com`.
2. Build the chain with `.with_fallbacks()`, scoped via `exceptions_to_handle`
   to rate-limit, timeout, and connection errors only. A schema mismatch must
   **not** silently fail over — that hides a real bug behind a working-looking
   card.
3. The provider branch from S1 supplies the no-breakpoint prompt shape for the
   DeepSeek link.
4. Log which provider answered, once per turn. Without this, a chain that
   silently degrades for an entire conversation is invisible.

**Done when:** revoking the Anthropic key mid-run falls through and a card
still lands. Test it by breaking it on purpose, not by reasoning about it.

**Result: PASSED 2026-08-25.** DeepSeek was funded, and the first live call
against it 400'd rather than 402'd: `deepseek-v4-flash` thinks by default and
thinking mode rejects the forced `tool_choice` structured output depends on.
Disabled with the documented `extra_body={"thinking": {"type": "disabled"}}` —
see SPEC.md for why that one and not the `reasoning_effort="none"` that also
appeared to work. It then answered correctly at 1.1–1.8s with `cache_read`
climbing, and its cache floor came back **128 tokens, measured**, which closes
open question 1 and takes `check_pack --provider deepseek` from `NOT RUN` to a
real PASS.

**Step 2 as written conflicted with the Done-when, and the Done-when won.**
Scoping failover to "rate-limit, timeout, and connection errors only" excludes
the 401 that revoking a key produces, so the two could not both hold. Credential
failures (401/402/403) are in the failover set, and what licenses that is the
preflight: `main()` proves *every* link with a live call before the conversation
starts, so a key that is missing, wrong or unfunded refuses to boot. A 401
arriving at turn 20 has already passed that gate — it is a key revoked or a
balance hit zero mid-call, which is outage-shaped, not a typo. **If the preflight
is ever removed, those three statuses must come out with it.** 400, 404 and 422
stay out: a malformed request or a schema mismatch is our bug, and a second
vendor quietly succeeding would bury it.

Failover is decided by an **allow-list** rather than by handing vendor exception
classes to `exceptions_to_handle`. An unrecognised failure therefore does *not*
fail over, which is the direction that fails closed — and the classifier is
testable with no network and no keys.

**Amended 2026-08-25, after reading all three vendors' error docs.** A boolean
"should this fail over" was the wrong shape, and a status-code allow-list was
missing a case: Anthropic returns a **spend limit as a 400 `invalid_request_error`**
— the same status *and* the same type string as a request this code built wrong
— so the chain would not have failed over on the exact condition it exists for.
Classification now produces a *reason* (`failures.py`), from the vendor's own
error type where it is more specific than the code, from the status where it is
not, and from the message only for the documented collisions. Two decisions hang
off the reason instead of one: whether to fail over, and whether this link is
worth trying again this session. A revoked key or a dead balance retires the link
for the conversation; a rate limit never does. Full mapping in SPEC.md
§ "What a failure means".

Two things the plan did not anticipate:

* **`with_fallbacks()` hands every link the same input**, but the two vendors
  need different message shapes. So a link is not a model, it is
  render-then-call: it ignores the input and builds its own messages from the
  shared transcript when it runs. That is what keeps the two-shape branch inside
  `system_message_for()` and `build_turn_message()` rather than in `enrich()`.
* **LangChain never reports which link answered** — it swallows the primary's
  exception and returns. Each link stamps `provider` onto its own result, so the
  per-turn log names the vendor and a change of vendor is warned about once.

**Broken on purpose, six cases, all as intended** (see the S3 result table in
§ Sessions of SPEC.md for the numbers):

| what was broken | expected | got |
|---|---|---|
| primary returns 401 | fall through, card lands | DeepSeek answered, card `Kestrel` |
| primary returns 400 | do **not** fall through | error card, DeepSeek never called |
| `should_failover`, 12 statuses/types | allow-list holds | 12/12 |
| fallback key unset | refuse to start | refused, named the key |
| unknown name in `GLOSS_FALLBACKS` | refuse to start | refused, listed known rows |
| `GLOSS_FALLBACKS=` | single provider, no chain | one link |

The 401/400 cases are real sockets and real SDK error objects — the Anthropic
link was pointed at a local server returning that status, so only the *cause* of
the failure was staged, not the failure.

### S4 — Card lifecycle

1. Add `id` to `CARD_SCHEMA` (`b_server.py:116`); the model supplies a short
   topic slug.
2. `display.html` keeps a `Map` keyed by id: same id updates in place, new id
   appends, cap at `GLOSS_MAX_CARDS` visible.
3. TTL per card, default ~90s, refreshed when the topic recurs.
4. Error cards get a distinct short TTL so a transient failure clears itself.

**Done when:** a topic raised on turn 1 and returned to on turn 4 shows one
card that updates, not two that flash.

**Result: PASSED 2026-08-25**, verified in a real browser against the DOM rather
than by reading the code. Turn 1 raises "Kestrel"; a quiet turn leaves the
screen untouched; turn 3 adds a second topic; turn 4 returns to Kestrel and the
card count stays at two, **reusing the same DOM node in the same position** with
only the detail text changed.

**Two departures from the plan, both deliberate.**

1. **`id` is optional, not required.** The plan had the model supply it. Making
   it required means a model that omits the field costs a real card over a
   bookkeeping one — and `required` is advice to a model, not a constraint on
   it, which `cards.py` already documents for the zero-card case. It defaults to
   a slug derived from the label, which is stable whenever the label is, and
   which produces the same id the model would have supplied. Duplicate ids
   within a batch are disambiguated rather than merged, since two cards sharing
   an id would fight over one slot.

2. **Error cards sit outside the card cap.** The first version counted them,
   and the browser check caught what that means: an error card evicted
   "Kestrel", and when the error expired twenty seconds later the topic did not
   come back — the card that displaced it had expired into nothing. A transient
   outage must not permanently destroy content. An error card is the server
   speaking about itself, so it now shows alongside the cap rather than inside
   it. Verified: three content cards plus an error, error clears, all three
   still there.

Eviction is by **least-recently-mentioned**, not oldest-created, so a topic the
conversation keeps returning to outlives a one-off raised earlier. Lifecycle
values are not a display policy: the TTL rides on each card and the cap rides on
the batch, so a second screen opened halfway through a call is configured by the
first message it receives.

**Covered by `tests/test_display.py` as of the same day**, driving the real file
in real Chromium. Both defects above were replanted afterwards to confirm the
tests catch them: reverting to per-turn replacement failed 6 tests, and counting
error cards against the cap failed the one that exists for it.

---

### S5 — Local retrieval, as the chain's floor

A last link that answers from local data with no model behind it. It is the only
link that survives both vendors being out, and on the turns it can answer it
replaces a 1.1–1.8s round trip with roughly nothing.

**The corpus already exists**: 32 books already converted to markdown under
`~/files/automation/resources/project_books` (~3.0M words), plus 21 PDFs in
`~/files/books`. BM25 over 3M words is milliseconds on this hardware and needs
no GPU, no embeddings and no local model — the hardware constraint that ruled
out local inference for STT does not apply to lexical retrieval.

**The split, and why it is a correctness rule rather than a preference:**

* **`recall` cards stay prep-pack only.** The instructions say "Never invent a
  fact, a number, or an anecdote that is not written there". A recall card
  sourced from a book would be a plausible lie on screen, mid-conversation.
* **`jargon` cards are what the books can serve.** Defining a term needs world
  knowledge the pack does not have, and that is exactly what 3M words of
  engineering books hold.

**Books never enter the prompt.** The retriever reads them and emits a card
directly, or emits nothing. That deletes the failure mode above rather than
mitigating it, and it is also the cheaper design — see below.

#### The cost of the alternative, measured against real prices

If instead the books were retrieved into the prompt for the model to read, the
retrieved passages are **per-turn payload that caching cannot help**, because
they differ every turn. Today's measured turn is 8,079 cached and **52 uncached**
input tokens. Three 800-token passages is 2,400 uncached — **46x the uncached
payload**. Modelled over a 20-turn call at Haiku 4.5 and DeepSeek V4-Flash list
prices (read 2026-08-25):

| scenario | $/call | vs today | context at turn 20 |
|---|---|---|---|
| today, no retrieval | $0.039 | — | 8,079 tok |
| passages appended as history (cached) | $0.181 | **4.6x** | 56,079 tok |
| passages after the last breakpoint (uncached) | $0.087 | 2.2x | 10,479 tok |
| local retriever answers 40% of turns | $0.024 | 0.6x | 8,079 tok |
| local retriever answers 60% of turns | $0.016 | 0.4x | 8,079 tok |

**The middle row is the trap, and it is the one instinct picks.** Caching the
retrieved passages is *worse* than not caching them. A 1h cache write costs 2x
base input and caching only repays on reuse; a passage retrieved for turn N is
never read again, so you pay the 2x write and then collect 0.1x reads forever on
text nobody looks at. It also grows context to 56K tokens by turn 20 — seven
times today's — which eats `GLOSS_MAX_TRANSCRIPT_TOKENS` and slows every turn.

**So: if retrieved text is ever put in the prompt, it goes after the last
breakpoint** — the same slot the constant `ASK` occupies, for the same reason.
Anywhere earlier and it either invalidates the prefix or pays to cache something
that will never be read twice.

**The dollars are not the argument.** Nine cents against four on a 45-minute
call is noise. The arguments are latency (a local hit is not a slow call, it is
no call) and the correctness rule above.

**PageIndex specifically is out.** It navigates its tree *using LLM calls* —
several round trips per query. That multiplies both tokens and latency on a path
budgeted at 1–2s, where the whole enrichment call currently takes 1.1–1.8s.
Worth knowing about for offline work; wrong shape for a live conversation.

1. Index `project_books` at build time, not call time — a prebuilt on-disk index
   loaded at startup, so a cold call pays nothing.
2. BM25 or TF-IDF. No embeddings; nothing to download, nothing to GPU.
3. Hard-code the emitted card to `kind: "jargon"`. Enforced in code, not by
   convention — `cards.py` already refuses an `error` kind from a model for the
   same reason.
4. A minimum score, below which it emits nothing. Fail closed: no card beats a
   wrong card on a screen someone is glancing at.
5. Check `~/files/automation/resources/KNOWLEDGE_INDEX.md` and the existing
   Librarian MCP server first — some of this index may already exist.

**Done when:** with both vendors unreachable, a jargon term from the books still
produces a correct card; and a term the books do not cover produces nothing at
all rather than a low-confidence guess.

**Result: PASSED 2026-08-25**, and the design changed on the way. Step 2 above
said BM25 or TF-IDF. That was built first and **measured at 3 usable answers out
of 10**, for a structural reason: these books are prose *about* concepts, not a
reference work *defining* them, so the best sentence containing "circuit breaker
pattern" is a mention rather than a definition. Ranking cannot fix that.

The reading moved offline instead. `tools/build_kb.py` sends each passage to a
model and asks what it *defines*; the same corpus then scored **17 usable
entries out of 18**. Build-time tokens are not conversation-time tokens — the
whole 32-book corpus costs about a dollar, once — and what ships is a plain dict
the live path queries for zero tokens and zero network. The full comparison is
in SPEC.md.

Three findings worth not rediscovering:

* **Back matter wins BM25.** An index page is nothing but keywords, so it
  out-scores every real explanation. Eight of the first ten results were index
  entries, TOC dotted leaders or code imports.
* **Bag-of-words retrieval is confidently wrong.** "Chaos engineering" returned
  "Context engineering is a core component of orchestration" — the worst
  possible failure for a screen with no model above it to disagree. Lookup is
  whole-phrase on word boundaries for this reason.
* **A glossary miss must report the vendor's failure, not its own.**
  `with_fallbacks()` re-raises only the last link's exception, and the last link
  is the glossary, so a total outage was about to display "no glossary term in
  this turn". Links now record their failures per turn and the card names the
  first with a recognisable reason.

Also fixed by a test rather than by inspection: apostrophes were being treated
as separators, so `Conway's Law` keyed as `conway s law` while Nova-3
transcribes it "conways law" — every possessive term in the glossary was
permanently unmatchable and the file looked perfectly correct.

#### Recogniser priming — done 2026-08-25, outside the S-numbers

Not in the original plan and worth recording: `keyterms.py` mines the prep pack
for domain vocabulary and primes Nova-3 with it at connect time. Measured on
identical synthesised audio, 4/6 terms recovered unprimed against 5/6 primed —
notably `A2DP`, which unprimed came back as the three words "a two d p" and was
unrecoverable by anything downstream. `GRIFFON` → `Griffin` failed both times:
priming does not beat a homophone, and S5's retrieval has to assume some terms
arrive wrong. Full numbers and the extraction rules are in SPEC.md.

## Open questions

1. ~~**Is DeepSeek's context caching automatic, and does it need any
   opt-in?**~~ **ANSWERED 2026-08-25: automatic, no opt-in, floor 128 tokens.**
   Their guide says caching is "enabled by default for all users" and that a
   request must "fully match a cache prefix unit", but publishes no minimum, so
   the floor was measured: 32 and 64 never cache, 128 does, and hits round down
   to a 128-token boundary. Confirmed live on the real prompt — `cache_read`
   climbed 5,376 → 5,504 as the conversation grew. No second prompt shape was
   needed.
2. **Does a five-minute gap actually happen in a real conversation?** The 1h
   TTL makes this moot at negligible cost, which is why it is the default here
   — but the answer determines whether it can ever be dropped back to 5m.
3. **Does Anthropic's structured output remove the `[{}]` zero-card problem?**
   The defensive filter at `b_server.py:292` stays either way; the question is
   whether it ever fires. If it never does on Haiku, that is evidence the
   provider choice was right.
4. **What is the real call rate?** ~15–25/hour is an estimate from the spec,
   never measured against a real conversation. Every cost figure here rests on
   it — though at seven cents an hour, being wrong by 3x is still pocket change.
