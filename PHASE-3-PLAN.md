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

### S4 — Card lifecycle

1. Add `id` to `CARD_SCHEMA` (`b_server.py:116`); the model supplies a short
   topic slug.
2. `display.html` keeps a `Map` keyed by id: same id updates in place, new id
   appends, cap at `GLOSS_MAX_CARDS` visible.
3. TTL per card, default ~90s, refreshed when the topic recurs.
4. Error cards get a distinct short TTL so a transient failure clears itself.

**Done when:** a topic raised on turn 1 and returned to on turn 4 shows one
card that updates, not two that flash.

---

## Open questions

1. **Is DeepSeek's context caching automatic, and does it need any opt-in?**
   Its pricing page bills cache hits and misses separately but does not state
   how a hit is produced. Assumed implicit; **unverified**. S3 finds out, and
   if it turns out to need explicit configuration the provider branch grows a
   second shape rather than the design changing.
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
