# Phase 3 — Plan

Written 2026-08-24. Scope decisions made under ponytail rules: the first rung
of the ladder that holds, wins. This document records what is being built,
what is deliberately **not**, and why for both.

Read `SPEC.md` § "Phase 2 design" first. Phase 3 changes nothing about the
trigger, the card kinds, or the transport.

---

## The problem Phase 3 exists to solve

Phase 2 works and is measured: 1.3–1.9s per turn, correct cards. It has
exactly one thing wrong with it, found 2026-08-23:

```
Quota exceeded: generate_content_free_tier_requests
limit: 20, model: gemini-3.6-flash    (per day, per project, per model)
```

The design budgets ~15–25 calls per **hour**. The free tier allows 20 per
**day**. Past that every turn shows "Enrichment is down", which is honest but
useless.

The constraint driving every decision below: **usage has to be near-free, and
a rate limit must never stall a live conversation.**

---

## What is being built

### 1. Provider fallback chain

`llm` becomes a chain instead of a single model:

```
groq:llama-3.3-70b     primary — fastest inference available, generous free tier
gemini-3.6-flash       second  — known-good, rationed to 20/day
ollama:llama3.2        last    — local, cannot rate-limit, always answers
```

**Why a chain rather than a router.** LangChain's `Runnable.with_fallbacks()`
already does cross-provider failover, in-process, with
`exceptions_to_handle` to scope it to rate-limit and timeout errors. It is
already installed. LiteLLM's Router is the better tool if you need per-
deployment RPM/TPM accounting and cooldown windows — see § "Not building" for
why this does not.

**Why Ollama last and not merely as an option.** Every hosted free tier can
429. A chain whose last link can fail has no floor. Local inference cannot
rate-limit, so the chain always terminates in an answer. Its latency is
unmeasured on this hardware and probably poor — but a slow card beats a red
error card, and it only runs when everything else is exhausted.

**Why Groq first.** Fastest inference of the free options, which matters
because the whole design lives inside a 1–2s budget. Its free tier is 30 RPM /
6,000 TPM / 1,000 RPD.

### 2. Shrink the prompt

Current prompt is ~4,600 tokens/call: instructions + a prep pack sized to clear
Gemini's 4,096-token cache minimum + the transcript window.

Phase 3 drops the pack-size guidance to "as small as it is useful," target
~1,500 tokens.

**Why this is the highest-value change and costs nothing.** The 4,096 figure
exists *only* to satisfy prompt caching. Groq does not cache, so on Groq a big
pack is pure cost against the binding limit. Do the arithmetic:

| Limit | Budget | At 4,600 tok/call | At ~2,000 tok/call |
|---|---|---|---|
| 30 RPM | 0.42 calls/min needed | 70× headroom | 70× headroom |
| 1,000 RPD | ~100 calls/day | 10× headroom | 10× headroom |
| **6,000 TPM** | **the binding one** | **two turns in one minute = 9,200, 429s** | 4,000, fits |

RPM and RPD are not close. TPM is reachable during rapid back-and-forth, and
halving the prompt fixes it without a line of orchestration code.

Caching guidance becomes provider-conditional rather than universal: a large
pack is right on Gemini/Anthropic paid, and wrong on Groq.

### 3. Card lifecycle — cards persist while a topic is live

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
manual dismissal. That is a proven design and worth copying the *idea* from.
Copying the whole schema — `layout`, `dataJson`, `source` namespacing, server-
evaluated expiry, history retention — would import a display server's
requirements into a page that shows at most three cards.

Taken: stable id, upsert, TTL.
Left: everything else, until gloss has a second consumer.

---

## Not building, and why

### PageIndex

Cut for now. `github.com/VectifyAI/PageIndex` builds a reasoning-navigable tree
over long documents, which is a genuinely better fit for a story bank than
embedding similarity — "which story answers this question" is a reasoning
problem, and the material has real structure.

It solves a problem gloss does not have yet. A prep pack is one conversation's
notes: 1,500–4,000 tokens, which fits in context with room to spare. Retrieval
over a document that already fits is machinery with nothing to do.

**Add it when:** the corpus stops fitting. That is the "current versions,
libraries, tooling" corpus — external, large, and genuinely needing retrieval.
That corpus does not exist yet. Build it first, and PageIndex becomes obvious
rather than speculative.

### Per-provider RPM/TPM budget tracking

Cut. The arithmetic above shows RPM at 70× headroom and RPD at 10×. Tracking
budgets that are never approached is bookkeeping for its own sake. TPM is the
one reachable limit and shrinking the prompt addresses it directly.

**Add it when:** 429s are observed in a real conversation *after* the prompt
shrink. Then it is a measured problem with a known shape, and LiteLLM's Router
is the thing to reach for rather than a hand-rolled bucket.

### A local ML model for routing decisions

Cut, and not merely deferred.

Rate limits are countable, not inferable. Provider budgets are published, spend
is observable, and the next call's cost is estimable from a character count.
Deciding "can this call go to Groq" is a token bucket — microseconds,
deterministic, reproducible when it misbehaves. A model in front of every model
call would add latency to the one path that must be fast, make routing
non-deterministic, and require training data that does not exist.

The instinct behind the request is right — many competing factors — but
weighted scoring over measurable inputs is a scoring function, not machine
learning.

### Turn-importance gating

Cut for now, and the one place where judgment genuinely beats arithmetic. Most
turns correctly produce zero cards, so most calls spend quota for nothing. A
cheap classifier deciding "is this a substantive question" could roughly double
effective quota.

**Add it when:** quota is the binding constraint after the changes above. It is
an optimization on a system that must first work.

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

### S1 — Groq primary, prompt shrink

The whole quota problem, addressed with the cheapest change available.

1. `uv add langchain-groq`, key from `console.groq.com`.
2. `GLOSS_PROVIDER=groq`, `GLOSS_MODEL=llama-3.3-70b-versatile` in `.env`.
3. Verify structured output works on Groq — this is the risk in the session.
   `with_structured_output` support varies by provider, and the card schema is
   the thing most likely to break. The startup preflight already exercises it.
4. Re-measure latency. Phase 2's numbers were Gemini's; Groq's will differ.
5. Update `sessions/README.md`: pack-size guidance becomes provider-conditional
   and stops telling everyone to write 3,000 words.
6. Re-point `tools/check_pack.py` — its cache-minimum check should not fail a
   pack for being short when the provider does not cache.

**Done when:** a full smoke-test run on Groq returns correct cards, and the
measured latency is recorded in `SPEC.md` next to Gemini's.

### S2 — Fallback chain

1. Build the chain with `.with_fallbacks()`, scoped via `exceptions_to_handle`
   to rate-limit and timeout errors only. A schema mismatch must **not**
   silently fail over — that hides a real bug behind a working-looking card.
2. `uv add langchain-ollama`; pull a small local model.
3. Log which provider answered, once per turn. Without this, a chain that
   silently degrades to local for an entire conversation is invisible.
4. Measure Ollama latency on this hardware. If it is 30s, say so in the docs
   rather than pretending the floor is usable.

**Done when:** revoking the Groq key mid-run falls through to the next provider
and a card still lands. Test it by breaking it on purpose, not by reasoning
about it.

### S3 — Card lifecycle

1. Add `id` to the card schema; the model supplies a short topic slug.
2. `display.html` keeps a `Map` keyed by id: same id updates in place, new id
   appends, cap at `GLOSS_MAX_CARDS` visible.
3. TTL per card, default ~90s, refreshed when the topic recurs.
4. Error cards get a distinct short TTL so a transient failure clears itself.

**Done when:** a topic raised on turn 1 and returned to on turn 4 shows one
card that updates, not two that flash.

### S4 — Up-to-date tooling corpus (only if wanted)

The "most current libraries, versions, tooling" goal, which is a different
retrieval problem from the prep pack: not your notes, and it goes stale.

Deliberately last, and deliberately optional. It is the only part of the
original Phase 3 sketch that is a genuinely new subsystem rather than a change
to an existing one — and it is the part with no evidence yet that it improves a
single card. Build S1–S3, use the tool in a real conversation, and let that
answer whether S4 is worth it.

If built: a curated corpus, indexed offline, PageIndex over it, retrieved
slice appended after the cached prefix. Not live web search — `SPEC.md` already
rules that out on latency and that reasoning still holds.

---

## Open questions

1. **Does Groq's `with_structured_output` produce the card schema reliably?**
   Gemini 3.6 returned `{}` for the zero-card case and needed filtering. Each
   provider will have its own version of that. S1 finds out.
2. **Is local inference usable at all on this hardware?** An i9 with no useful
   GPU. `SPEC.md` already measured local Whisper as too slow for the STT half;
   the LLM half may go the same way. If Ollama is unusable the fallback chain
   has no floor, and that changes S2's conclusion.
3. **What is the real call rate?** ~15–25/hour is an estimate from the spec,
   never measured against a real conversation. Every budget calculation here
   rests on it.
