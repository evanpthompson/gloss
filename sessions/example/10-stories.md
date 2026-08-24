# Stories

Written the way I'd actually say them. A `recall` card can only quote what is
here, so anything I'd want surfaced mid-call has to be written down first.

## Latency on a hot read path

Situation: a customer-facing lookup sat at 6s p95 and was the top complaint.
What I did: traced it to N+1 fan-out against a downstream service, added a
request-coalescing layer and a short-TTL cache keyed on the stable part of the
query. Measured before and after rather than guessing.
Result: 6s -> 1.5s p95. A second path went 12s -> 8s; that one was bounded by
the downstream service and I said so rather than claiming the win.
If pushed: the cache was the easy half. The coalescing was where the risk was,
because a bug there returns *someone else's* data, so it shipped behind a flag
with a shadow-compare before it served real traffic.

## Integration platform, ~100K calls/day

Situation: partner integrations were each hand-rolled; every new one was a
multi-week project and every one broke differently.
What I did: built a manifest-driven integration layer — one declarative
description per partner, shared transport, retries, auth and redaction.
Result: onboarding a partner went from weeks to days, ~100K calls/day at steady
state, and failures became legible because they all failed the same way.
If pushed: the honest cost is that the manifest became a small language, and
small languages grow. I'd version the manifest schema from day one next time.

## Raising the floor on a team

Situation: 4-person team, wildly uneven review turnaround and a lot of
knowledge in one person's head.
What I did: drove AI-assisted development adoption across a 20-person org —
paired on it rather than mandating it, and wrote down what actually worked.
Result: roughly 40 hrs/week of aggregate time saved across the org.
If pushed: adoption was uneven and I'd not pretend otherwise. Two people got a
lot out of it, most got some, one never used it, and that was fine.

## Something that went badly

A migration I sequenced wrong: I moved reads before the write path was fully
dual-writing, so a subset of records read stale for about forty minutes.
Caught it via a diff job I'd built for exactly that reason, rolled back, and
re-sequenced. The lesson I actually took: the diff job existed because I was
nervous, and being nervous was correct — I should have let that nervousness
change the sequencing, not just add monitoring.
