# Prep packs

**Don't write one by hand, and never paste an existing prep doc in.** Ask
Claude Code to run the `gloss-prep` skill
(`~/files/automation/skills/gloss-prep/SKILL.md`) — it assembles a pack from
your story bank and per-conversation prep docs, compressed into the form a card
can actually use, and gates the result.

The reason it rewrites rather than copies: prep docs are written to be read
*before* a call, and routinely contain a salary floor, a target number, and a
negotiation script. A pack becomes the **system prompt**, so anything in it can
surface as a card on a screen the other person may be looking at. Concatenating
one real prep doc was measured and tripped 14 separate leaks.

Before using any pack:

```
uv run python tools/check_pack.py sessions/<name>
```

One directory per conversation. Every `*.md` in it is concatenated, in
**sorted filename order**, into the cached part of the system prompt — hence
the `00-`, `10-` numeric prefixes. Point the server at one with:

```
GLOSS_SESSION=sessions/example uv run b_server.py
```

## Two rules that actually matter

**Write what you would say, not what you would summarize.** A `recall` card can
only quote what is in these files. "Talk about the latency work" produces
nothing useful at a glance; the actual numbers and the actual caveat produce a
card you can read in a second.

**Longer is genuinely better here, up to a point.** Every provider has a
minimum prefix size below which it never caches, and all of them fail silently
— no error, the counters just stay at zero. The floor per provider lives in
`providers.py` and `check_pack.py` reads it from there, so there is one number
rather than two that can disagree. Gemini 3.x Flash and Claude Haiku 4.5 both
need **4096 tokens** — roughly 3000 words. Haiku 4.5 has the highest minimum of
any current Claude model, so a pack sized for a different Claude will not cache
here. Under the threshold the whole pack is reprocessed on every single turn,
which costs latency in the exact place the design cannot afford it.

Where a provider's floor has never been looked up — DeepSeek today — the check
reports `NOT RUN` and fails rather than printing a PASS nobody verified.

`b_server.py` logs the counters on the first enrichment of each run:

```
Tokens: in=2140 out=64 cache_read=0       <-- turn 1, nothing to read yet
Tokens: in=2140 out=58 cache_read=2048    <-- what you want from turn 2 on
Tokens: in=430  out=61 cache_read=0       <-- too short; never going to cache
```

The `example/` pack here is deliberately below the line, so a first run shows
you the zero rather than hiding it.

## Not checked in

Real prep packs contain a company name, a person's name, and your own unedited
notes about a live conversation. `sessions/*` is gitignored except this README
and `example/`.
