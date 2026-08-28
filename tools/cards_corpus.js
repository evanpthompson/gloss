// The card corpus, shared by every tool in this directory so there is one
// list rather than one per instrument. Loaded with <script src>, which works
// over file:// where fetch() does not.
//
// Each row: term, six-word label, detail line, a plausible wrong term, the
// correct gist, a plausible wrong gist.
//
// KNOWN LIMITATION, measured 2026-08-28: the gist pair is often separable from
// the term alone — "Leader election" implies "one node holds a lease" without
// the card saying anything. A participant with no domain knowledge still scored
// 6/7 on gist in the one-word condition. So these probes test recognition and
// inference, not what the card delivered. Replacing them needs distractors
// drawn from material only the card could supply. See SPEC.md § 4b, run 2.
window.GLOSS_CARDS = [
  ["Kestrel","Kestrel is their deploy gate","Every change clears it before shipping. They have raised it twice already.","Merlin","a deploy gate","a load balancer"],
  ["Idempotency","Idempotency on the retry path","The same request twice must not charge twice. Ask where the keys live.","Concurrency","safe to retry","safe to cache"],
  ["Backpressure","Backpressure in the ingest queue","Slow consumers push load back upstream instead of silently dropping it.","Backfill","slowing the producer","dropping old messages"],
  ["Sharding","Sharding by tenant identifier","Data split across nodes by customer, so one tenant cannot starve another.","Caching","splitting data across nodes","copying data to every node"],
  ["Quorum","Quorum reads on the replica set","A majority of replicas must agree before a read is returned.","Quota","a majority must agree","the fastest replica answers"],
  ["Blue-green","Blue-green deploy for the API","Two identical environments; traffic switches at once and rolls back the same way.","Red-black","two environments, instant switch","a gradual percentage rollout"],
  ["Canary","Canary release to five percent","A small slice of live traffic sees the new build before anyone else.","Cascade","a small traffic slice first","a full switch at midnight"],
  ["Sidecar","Sidecar proxy per pod","A helper container beside the app handles TLS, retries and telemetry.","Sandbox","a helper beside the app","a shared gateway in front"],
  ["CQRS","CQRS split on the orders service","Reads and writes use separate models, so each side scales on its own.","CQL","separate read and write models","one unified model"],
  ["Saga","Saga for the booking workflow","A long transaction as steps with compensating undo, since two-phase commit will not hold.","Sonar","steps with compensating undo","one atomic transaction"],
  ["Bulkhead","Bulkhead around the payments client","Isolated pools, so one slow dependency cannot drain every thread.","Bulk load","isolating resource pools","sharing one thread pool"],
  ["Circuit breaker","Circuit breaker on the search call","After repeated failures it stops calling and fails fast for a while.","Rate limiter","stops calling after failures","queues calls until healthy"],
  ["Tail latency","Tail latency on the checkout path","The slowest requests, p99 and beyond — not the average anybody quotes.","Tail sampling","the slowest requests","the average request"],
  ["Write amplification","Write amplification in the storage layer","One logical write causes many physical writes, wearing the device out faster.","Read amplification","one write causes many","one read causes many"],
  ["Consistent hashing","Consistent hashing across cache nodes","Adding a node moves a fraction of the keys instead of all of them.","Cryptographic hashing","adding a node moves few keys","adding a node rehashes everything"],
  ["Fan-out","Fan-out on the timeline write","One post copied into many followers' feeds at write time rather than read time.","Failover","copying to many on write","assembling the feed on read"],
  ["Dual write","Dual write during the migration","Both stores written while reads move across. Order matters or records read stale.","Dry run","writing to both stores","reading from both stores"],
  ["Outbox","Outbox pattern for event publishing","Events written in the same transaction as the data, then relayed separately.","Inbox","events written with the data","events published directly"],
  ["Leader election","Leader election in the scheduler","One node holds a lease and does the work; the rest stand by.","Load shedding","one node holds a lease","every node shares the work"],
  ["Load shedding","Load shedding above the limit","Rejecting some requests early so the rest still meet their deadline.","Load balancing","rejecting some to save the rest","spreading load evenly"],
  ["Cold start","Cold start on the serverless path","The first request after idle pays container startup, sometimes whole seconds.","Cold storage","first request pays startup","archived data is slow"],
  ["Thundering herd","Thundering herd after cache expiry","Every client misses at the same moment and hits the origin together.","Rolling restart","everyone misses at once","one client retries forever"],
  ["Exactly once","Exactly-once delivery in the pipeline","Usually at-least-once plus deduplication. Ask where the dedupe key is stored.","At most once","at-least-once plus dedupe","the broker guarantees it"],
  ["Schema registry","Schema registry for the event bus","Producers register schemas, so consumers break at deploy rather than at runtime.","Service registry","schemas checked before deploy","schemas discovered at runtime"],
  ["Feature flag","Feature flag on the coalescing layer","The new path ships dark, then serves a slice after a shadow compare.","Feature branch","shipped off, enabled later","merged only when finished"],
  ["Shadow traffic","Shadow traffic against the new service","Real requests mirrored to it; the responses are compared and never served.","Synthetic traffic","mirrored, responses discarded","generated, responses served"],
  ["Backfill","Backfill job for the missing rows","Historic records reprocessed after a schema change, throttled to spare live traffic.","Backpressure","reprocessing historic records","pausing new records"],
  ["Blast radius","Blast radius of a bad deploy","How much breaks when one thing does. Cells and quotas shrink it.","Burn rate","how much breaks at once","how fast budget is spent"],
  ["Error budget","Error budget for the quarter","How much unreliability the SLO permits before feature work has to stop.","Error rate","allowed unreliability","measured failure count"],
  ["Toil","Toil in the on-call rotation","Manual, repetitive work that scales with load and should be automated away.","Churn","repetitive manual work","staff turnover"],
  ["Golden signals","Golden signals on every service","Latency, traffic, errors and saturation — the four things they alert on.","Golden path","four metrics to alert on","the approved way to build"],
  ["Chaos engineering","Chaos engineering in staging only","Failures injected on purpose, to find out what actually breaks first.","Context engineering","injecting failures deliberately","tuning prompts deliberately"],
].map(([term, label, detail, termDistractor, gist, gistDistractor]) =>
  ({ term, label, detail, termDistractor, gist, gistDistractor }));
