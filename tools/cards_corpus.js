// The card corpus, shared by every tool in this directory so there is one list
// rather than one per instrument. Loaded with <script src>, which works over
// file:// where fetch() does not.
//
// --- Why `fact` exists, and why `gist` is retired --------------------------
//
// `gist` / `gistDistractor` were the comprehension probe for the glance test.
// Run 2 (2026-08-28) killed them. A participant with no engineering background,
// who took the vocabulary to be invented, still scored 6 of 7 on gist in the
// ONE-WORD condition — which displays no gist at all. "Leader election" implies
// "one node holds a lease" whether or not a card says so. The probe was testing
// inference from the term, not what the card delivered.
//
// `fact` / `factDistractor` replace them, and the rule is: **the answer must be
// arbitrary.** A number, a name, a schedule — something no amount of domain
// knowledge or inference can recover, so the only way to have it is to have
// read it here. Both options are the same shape and equally plausible.
//
// `detail` was rewritten to carry one such specific per card. That means the
// `today` condition's stimulus CHANGED after run 2, so run 3 is not comparable
// to runs 1-2 on detail-dependent measures. A valid probe was worth more than
// comparability with a condition that was already losing.
//
// `gist` is kept only so glance_test.html still runs against its own history.
// Do not build anything new on it.
//
// --- AND `fact` FAILED THE SAME WAY, run 3, 2026-08-28 ---------------------
//
// The rule above is right and the pairs below do not meet it. Arbitrariness was
// checked against the TERM and not against the DISTRACTOR, so most pairs offer
// one ordinary value and one that is absurd by an order of magnitude or that
// contradicts something the field already knows: 40MB vs 400MB per pod, 200ms
// vs 30s, 500 vs 50,000 rows a second, a canary at five vs twenty-five percent,
// 43 minutes a month (99.9%) vs four hours, Stripe's 24-hour idempotency key vs
// 30 days. Domain sense answers those without the card.
//
// Measured: the two conditions that display NO detail at all scored 10/11 on
// those pairs and 1/5 on the pairs where both options were ordinary — and the
// condition that DOES display the detail scored the same as the ones that do
// not. SPEC.md § 4b run 3 has the table.
//
// So: DO NOT run exposure_test.html for a result until these pairs are
// rewritten. Both options must be ordinary. The test for a pair is not "could
// someone guess this" but "would an experienced engineer reject one of these
// out of hand" — and it has to be applied before the run, not after it.
//
// --- REWRITTEN 2026-08-29, AND PRE-REGISTERED BEFORE RUN 4 ----------------
//
// All 32 pairs below were rewritten under that rule, applied pair by pair and
// before the run: **would an experienced engineer reject one of these two out
// of hand?** If yes the pair is broken. Both options must be values a real
// team could have picked, in the same unit and the same magnitude class.
//
// Six pairs were kept exactly as they stood — Sharding, Saga, Dual write,
// Leader election, Thundering herd, Golden signals. They are six of the seven
// symmetric pairs run 3 contained, the subset where the controls sat at
// chance, so they are what a working pair looks like and they are the
// template the other 26 were rewritten against.
//
// Three cards needed the DETAIL moved rather than just the distractor,
// because the correct answer was the field's own canonical value and so was
// recoverable without reading the card at all: `Idempotency` (24 hours is
// Stripe's default → 72 hours), `Canary` (five percent is the canonical first
// slice → three percent, and its label no longer states the number either),
// and `Error budget` (43 minutes a month simply IS 99.9% → 90 minutes). The
// stimulus for those three changed, so run 4 is not comparable to run 3 on
// them. That is an acceptable price: run 3's fact column is void anyway.
//
// PRE-REGISTERED, and written here before the run so it cannot be assigned
// after seeing the numbers: **all 32 pairs are declared symmetric.** Run 3's
// asymmetric/symmetric split was assigned after the results were in, which is
// what made its 91%/20% table a hypothesis rather than a finding. There is no
// post-hoc subset to take this time — the control check is the whole run.
//
// THE FALSIFIER: `one` and `column` display no detail at all and so cannot
// answer this probe from the card. Both must land near 50%. If either clears
// roughly 70%, the probe is STILL leaking, the fact column is void for a
// fourth time, nothing in SPEC.md § 4b's detail column may be cited, and
// Phase 5 does not start. Read that check first, before any comparison
// between conditions.
//
// What nothing checks: plausibility itself. No assertion can decide whether
// two numbers are both ordinary — that judgement is human, it is the exact
// judgement that has now failed twice, and it lives in this comment rather
// than in a test. What IS mechanical here, and was verified after the
// rewrite: every correct answer appears in its own card's `detail`, and no
// card's `detail` or `label` contains its own distractor.

window.GLOSS_CARDS = [
  { term: "Kestrel", label: "Kestrel is their deploy gate",
    detail: "Every change clears it before shipping; they have raised it twice this quarter.",
    termDistractor: "Merlin", gist: "a deploy gate", gistDistractor: "a load balancer",
    fact: "raised twice this quarter", factDistractor: "raised three times this quarter" },
  { term: "Idempotency", label: "Idempotency on the retry path",
    detail: "The same request twice must not charge twice. Their keys expire after 72 hours.",
    termDistractor: "Concurrency", gist: "safe to retry", gistDistractor: "safe to cache",
    fact: "keys expire after 72 hours", factDistractor: "keys expire after 12 hours" },
  { term: "Backpressure", label: "Backpressure in the ingest queue",
    detail: "Slow consumers push load back upstream. The queue holds 10,000 before it pushes.",
    termDistractor: "Backfill", gist: "slowing the producer", gistDistractor: "dropping old messages",
    fact: "queue holds 10,000", factDistractor: "queue holds 50,000" },
  { term: "Sharding", label: "Sharding by tenant identifier",
    detail: "Data split across nodes by customer. They run 64 shards and rebalance quarterly.",
    termDistractor: "Caching", gist: "splitting data across nodes", gistDistractor: "copying data to every node",
    fact: "64 shards", factDistractor: "8 shards" },
  { term: "Quorum", label: "Quorum reads on the replica set",
    detail: "A majority of replicas must agree before a read returns. Theirs is 3 of 5.",
    termDistractor: "Quota", gist: "a majority must agree", gistDistractor: "the fastest replica answers",
    fact: "3 of 5", factDistractor: "4 of 7" },
  { term: "Blue-green", label: "Blue-green deploy for the API",
    detail: "Two identical environments, traffic switches at once. They keep the old one 48 hours.",
    termDistractor: "Red-black", gist: "two environments, instant switch", gistDistractor: "a gradual percentage rollout",
    fact: "old one kept 48 hours", factDistractor: "old one kept 24 hours" },
  { term: "Canary", label: "Canary release on the checkout API",
    detail: "A slice of live traffic sees the new build first. They start at three percent.",
    termDistractor: "Cascade", gist: "a small traffic slice first", gistDistractor: "a full switch at midnight",
    fact: "starts at three percent", factDistractor: "starts at eight percent" },
  { term: "Sidecar", label: "Sidecar proxy per pod",
    detail: "A helper container beside the app handles TLS and retries. Adds 40MB per pod.",
    termDistractor: "Sandbox", gist: "a helper beside the app", gistDistractor: "a shared gateway in front",
    fact: "adds 40MB per pod", factDistractor: "adds 90MB per pod" },
  { term: "CQRS", label: "CQRS split on the orders service",
    detail: "Reads and writes use separate models. Their read side lags by about 200 milliseconds.",
    termDistractor: "CQL", gist: "separate read and write models", gistDistractor: "one unified model",
    fact: "lags about 200 milliseconds", factDistractor: "lags about 800 milliseconds" },
  { term: "Saga", label: "Saga for the booking workflow",
    detail: "A long transaction as steps with compensating undo. Their booking saga has seven steps.",
    termDistractor: "Sonar", gist: "steps with compensating undo", gistDistractor: "one atomic transaction",
    fact: "seven steps", factDistractor: "three steps" },
  { term: "Bulkhead", label: "Bulkhead around the payments client",
    detail: "Isolated pools, so one slow dependency cannot drain every thread. Payments gets twenty.",
    termDistractor: "Bulk load", gist: "isolating resource pools", gistDistractor: "sharing one thread pool",
    fact: "payments gets twenty", factDistractor: "payments gets fifty" },
  { term: "Circuit breaker", label: "Circuit breaker on the search call",
    detail: "After repeated failures it stops calling and fails fast. Theirs opens after five.",
    termDistractor: "Rate limiter", gist: "stops calling after failures", gistDistractor: "queues calls until healthy",
    fact: "opens after five failures", factDistractor: "opens after ten failures" },
  { term: "Tail latency", label: "Tail latency on the checkout path",
    detail: "The slowest requests, not the average anybody quotes. Their p99 sits at 2.4 seconds.",
    termDistractor: "Tail sampling", gist: "the slowest requests", gistDistractor: "the average request",
    fact: "p99 at 2.4 seconds", factDistractor: "p99 at 1.6 seconds" },
  { term: "Write amplification", label: "Write amplification in the storage layer",
    detail: "One logical write causes many physical writes, wearing the device. Theirs runs about 12x.",
    termDistractor: "Read amplification", gist: "one write causes many", gistDistractor: "one read causes many",
    fact: "about 12x", factDistractor: "about 5x" },
  { term: "Consistent hashing", label: "Consistent hashing across cache nodes",
    detail: "Adding a node moves a fraction of the keys, not all. They use 128 virtual nodes.",
    termDistractor: "Cryptographic hashing", gist: "adding a node moves few keys", gistDistractor: "adding a node rehashes everything",
    fact: "128 virtual nodes", factDistractor: "256 virtual nodes" },
  { term: "Fan-out", label: "Fan-out on the timeline write",
    detail: "One post copied into many feeds at write time. They cap it at 5,000 followers.",
    termDistractor: "Failover", gist: "copying to many on write", gistDistractor: "assembling the feed on read",
    fact: "capped at 5,000 followers", factDistractor: "capped at 20,000 followers" },
  { term: "Dual write", label: "Dual write during the migration",
    detail: "Both stores written while reads move across. Their cutover ran three weeks.",
    termDistractor: "Dry run", gist: "writing to both stores", gistDistractor: "reading from both stores",
    fact: "cutover ran three weeks", factDistractor: "cutover ran three days" },
  { term: "Outbox", label: "Outbox pattern for event publishing",
    detail: "Events written in the same transaction as the data, then relayed every 200 milliseconds.",
    termDistractor: "Inbox", gist: "events written with the data", gistDistractor: "events published directly",
    fact: "relayed every 200 milliseconds", factDistractor: "relayed every 500 milliseconds" },
  { term: "Leader election", label: "Leader election in the scheduler",
    detail: "One node holds a lease and does the work; the rest stand by. Lease is 15 seconds.",
    termDistractor: "Load shedding", gist: "one node holds a lease", gistDistractor: "every node shares the work",
    fact: "a 15-second lease", factDistractor: "a five-minute lease" },
  { term: "Load shedding", label: "Load shedding above the limit",
    detail: "Rejecting some requests early so the rest meet deadline. They shed above 80% CPU.",
    termDistractor: "Load balancing", gist: "rejecting some to save the rest", gistDistractor: "spreading load evenly",
    fact: "sheds above 80% CPU", factDistractor: "sheds above 60% CPU" },
  { term: "Cold start", label: "Cold start on the serverless path",
    detail: "The first request after idle pays container startup. Theirs is about 900 milliseconds.",
    termDistractor: "Cold storage", gist: "first request pays startup", gistDistractor: "archived data is slow",
    fact: "about 900 milliseconds", factDistractor: "about 300 milliseconds" },
  { term: "Thundering herd", label: "Thundering herd after cache expiry",
    detail: "Every client misses at the same moment and hits the origin. They jitter expiry by 60 seconds.",
    termDistractor: "Rolling restart", gist: "everyone misses at once", gistDistractor: "one client retries forever",
    fact: "jitters expiry by 60 seconds", factDistractor: "jitters expiry by five minutes" },
  { term: "Exactly once", label: "Exactly-once delivery in the pipeline",
    detail: "Usually at-least-once plus deduplication. Their dedupe window is ten minutes.",
    termDistractor: "At most once", gist: "at-least-once plus dedupe", gistDistractor: "the broker guarantees it",
    fact: "a ten-minute dedupe window", factDistractor: "a two-hour dedupe window" },
  { term: "Schema registry", label: "Schema registry for the event bus",
    detail: "Producers register schemas, so consumers break at deploy. They allow two versions live.",
    termDistractor: "Service registry", gist: "schemas checked before deploy", gistDistractor: "schemas discovered at runtime",
    fact: "two versions live", factDistractor: "four versions live" },
  { term: "Feature flag", label: "Feature flag on the coalescing layer",
    detail: "The new path ships dark, then serves a slice after a shadow compare. Theirs ran two weeks dark.",
    termDistractor: "Feature branch", gist: "shipped off, enabled later", gistDistractor: "merged only when finished",
    fact: "ran two weeks dark", factDistractor: "ran four days dark" },
  { term: "Shadow traffic", label: "Shadow traffic against the new service",
    detail: "Real requests mirrored to it; responses compared and never served. They mirror ten percent.",
    termDistractor: "Synthetic traffic", gist: "mirrored, responses discarded", gistDistractor: "generated, responses served",
    fact: "mirrors ten percent", factDistractor: "mirrors thirty percent" },
  { term: "Backfill", label: "Backfill job for the missing rows",
    detail: "Historic records reprocessed after a schema change, throttled to 500 rows a second.",
    termDistractor: "Backpressure", gist: "reprocessing historic records", gistDistractor: "pausing new records",
    fact: "500 rows a second", factDistractor: "2,000 rows a second" },
  { term: "Blast radius", label: "Blast radius of a bad deploy",
    detail: "How much breaks when one thing does. Cells shrink it; they run twelve per region.",
    termDistractor: "Burn rate", gist: "how much breaks at once", gistDistractor: "how fast budget is spent",
    fact: "twelve cells per region", factDistractor: "six cells per region" },
  { term: "Error budget", label: "Error budget for the quarter",
    detail: "How much unreliability the SLO permits before feature work stops. Theirs is 90 minutes monthly.",
    termDistractor: "Error rate", gist: "allowed unreliability", gistDistractor: "measured failure count",
    fact: "90 minutes a month", factDistractor: "150 minutes a month" },
  { term: "Toil", label: "Toil in the on-call rotation",
    detail: "Manual repetitive work that scales with load. They cap it at 30% of a rotation.",
    termDistractor: "Churn", gist: "repetitive manual work", gistDistractor: "staff turnover",
    fact: "capped at 30% of a rotation", factDistractor: "capped at 20% of a rotation" },
  { term: "Golden signals", label: "Golden signals on every service",
    detail: "Latency, traffic, errors and saturation. They page on three of the four.",
    termDistractor: "Golden path", gist: "four metrics to alert on", gistDistractor: "the approved way to build",
    fact: "pages on three of the four", factDistractor: "pages on all four" },
  { term: "Chaos engineering", label: "Chaos engineering in staging only",
    detail: "Failures injected on purpose to find what actually breaks. Staging only, on Thursdays.",
    termDistractor: "Context engineering", gist: "injecting failures deliberately", gistDistractor: "tuning prompts deliberately",
    fact: "staging only, on Thursdays", factDistractor: "staging only, on Mondays" },
];
