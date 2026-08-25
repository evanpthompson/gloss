"""Build the local glossary from a directory of books. Offline, once.

    uv run python tools/build_kb.py ~/files/automation/resources/project_books --estimate
    uv run python tools/build_kb.py ~/files/automation/resources/project_books

This is the expensive half of the knowledge base, and it is deliberately not on
the conversation's path. It reads every book with a model and asks what each
passage *defines*; what ships is the resulting dictionary, which the live path
loads and queries with no model and no network. Build-time tokens are not
conversation-time tokens.

**Why a model reads the books rather than a ranking function.** Lexical
retrieval was built first and measured at 3 usable answers out of 10. These
books are prose about concepts, not a reference work defining them, so the best
sentence containing "circuit breaker pattern" is a mention rather than a
definition. Asking a model what a passage defines produced 17 out of 18 on the
same corpus. The measurement is in SPEC.md.

**It refuses to spend more than it was told it could.** The estimate is printed
before anything is sent and the run stops if it exceeds `--max-cost`. A build
script that quietly emptied the account funding the live fallback chain would
take out the thing it was trying to make more reliable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from langchain.chat_models import init_chat_model

import providers

# Build-time cost estimation only, $ per million tokens. Read from the vendors'
# pricing pages 2026-08-25; DeepSeek's off-peak rate is used because a corpus
# build has no deadline and can simply be run then. These numbers exist to
# stop a runaway spend, not to bill anyone, so they are allowed to drift a
# little — but check them if the ceiling ever looks wrong.
PRICES: dict[str, tuple[float, float]] = {   # provider -> (input, output)
    "deepseek": (0.22, 0.66),
    "anthropic": (1.00, 5.00),
}

CHUNK_CHARS = 7000        # ~2k tokens: enough context to define a term properly
OUT_TOKENS = 1200
CONCURRENCY = 8

SCHEMA = {
    "title": "emit_glossary",
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string", "description": "The technical term, as normally written."},
                    "definition": {"type": "string", "description": "One line, at most 25 words, grounded ONLY in this passage."},
                },
                "required": ["term", "definition"],
            },
        }
    },
    "required": ["entries"],
}

PROMPT = """Extract glossary entries from the passage below.

Only include a term if the passage actually explains what it IS. Skip terms
merely mentioned, used in an example, or named in passing. Skip product names
unless the passage defines the category they belong to.

Definitions must be grounded in this passage and must never add outside
knowledge. At most 25 words each — they are read in about a second.

Return an empty list if the passage defines nothing. That is the common case
and the correct answer.

PASSAGE:
"""


def unwrap(text: str) -> list[str]:
    """Rejoin PDF hard-wrapping into paragraphs.

    These books were extracted from PDFs at roughly 67 characters per line, so
    84% of lines end mid-sentence. Chunking without this splits sentences at
    arbitrary points and hands the model fragments.
    """
    # U+00AD soft hyphen and U+2010 hyphen are what the PDF extractor emits at
    # line breaks; both mean "this word continues on the next line".
    text = text.replace("­", "").replace("‐\n", "").replace("-\n", "")  # noqa: RUF001
    out: list[str] = []
    buf = ""
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            if buf:
                out.append(buf)
                buf = ""
            continue
        buf = f"{buf} {stripped}".strip() if buf else stripped
        if stripped.endswith((".", "?", "!", ":", ";")):
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return [p for p in out if len(p) > 120]


# Front and back matter defines nothing and costs the same to send as a chapter.
JUNK = re.compile(
    r"O.Reilly Media|All rights reserved|ISBN|Printed in the United States|"
    r"registered trademark|About the Author|Colophon|Table of Contents|"
    r"sales promotional use|errata", re.I)


def chunks_for(path: Path) -> list[str]:
    paragraphs = [p for p in unwrap(path.read_text(errors="ignore")) if not JUNK.search(p)]
    out: list[str] = []
    current = ""
    for para in paragraphs:
        current += para + "\n\n"
        if len(current) > CHUNK_CHARS:
            out.append(current)
            current = ""
    if len(current) > 500:
        out.append(current)
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("books", type=Path, help="directory of .md books")
    ap.add_argument("--out", type=Path, default=Path("kb/glossary.json"))
    ap.add_argument("--provider", default=os.environ.get("GLOSS_KB_PROVIDER", "deepseek"),
                    choices=sorted(providers.PROFILES))
    ap.add_argument("--estimate", action="store_true", help="print the cost and send nothing")
    ap.add_argument("--max-cost", type=float, default=2.00,
                    help="refuse to start above this estimated spend, in USD")
    ap.add_argument("--limit", type=int, default=0, help="only this many chunks (for a sample)")
    args = ap.parse_args()

    files = sorted(args.books.glob("*.md"))
    if not files:
        print(f"No .md files in {args.books}", file=sys.stderr)
        return 1

    work: list[tuple[str, str]] = []   # (book, chunk)
    for f in files:
        book = f.stem.split(" - ")[0]
        work += [(book, c) for c in chunks_for(f)]
    if args.limit:
        work = work[: args.limit]

    in_tokens = sum(providers.estimate_tokens(PROMPT + c) for _, c in work)
    price_in, price_out = PRICES.get(args.provider, (1.0, 5.0))
    estimate = (in_tokens * price_in + len(work) * OUT_TOKENS * 0.35 * price_out) / 1e6

    print(f"{len(files)} books -> {len(work):,} chunks, ~{in_tokens:,} input tokens")
    print(f"estimated cost on {args.provider}: ${estimate:.2f}")
    if args.estimate:
        return 0
    # Fail closed on spend. A build script that quietly emptied the account
    # funding the live fallback chain would take out the reliability it exists
    # to add.
    if estimate > args.max_cost:
        print(f"\nRefusing to start: ${estimate:.2f} exceeds --max-cost ${args.max_cost:.2f}.\n"
              f"Raise the ceiling deliberately, or narrow the corpus with --limit.",
              file=sys.stderr)
        return 1

    profile = providers.PROFILES[args.provider]
    if (missing := providers.missing_key(profile, os.environ)) is not None:
        print(f"Cannot build on {args.provider} — {missing}", file=sys.stderr)
        return 1

    llm = init_chat_model(
        profile.model, model_provider=args.provider, temperature=0,
        max_tokens=OUT_TOKENS, max_retries=2, timeout=120, **profile.kwargs,
    ).with_structured_output(SCHEMA, include_raw=True)

    gate = asyncio.Semaphore(CONCURRENCY)
    entries: dict[str, dict] = {}
    stats = {"in": 0, "out": 0, "failed": 0, "dropped": 0}
    started = time.monotonic()

    async def one(book: str, chunk: str, index: int) -> None:
        async with gate:
            try:
                result = await llm.ainvoke(PROMPT + chunk)
            except Exception as exc:
                stats["failed"] += 1
                print(f"  chunk {index} failed: {type(exc).__name__}: {str(exc)[:90]}",
                      file=sys.stderr)
                return
        usage = getattr(result.get("raw"), "usage_metadata", None) or {}
        stats["in"] += usage.get("input_tokens", 0)
        stats["out"] += usage.get("output_tokens", 0)
        for raw in (result.get("parsed") or {}).get("entries", []):
            # `required` in a schema is advice to a model rather than a
            # constraint on it — the same lesson cards.py records. Validate
            # each entry on its own so one malformed row does not lose a chunk.
            term = str(raw.get("term", "")).strip()
            definition = " ".join(str(raw.get("definition", "")).split())
            if not term or not definition or len(definition.split()) > 40:
                stats["dropped"] += 1
                continue
            key = term.lower()
            if key not in entries or len(definition) > len(entries[key]["definition"]):
                entries[key] = {"term": term, "definition": definition, "source": book}
        if index % 100 == 0:
            print(f"  {index}/{len(work)} chunks, {len(entries):,} terms", file=sys.stderr)

    await asyncio.gather(*[one(b, c, i) for i, (b, c) in enumerate(work, 1)])

    elapsed = time.monotonic() - started
    spent = (stats["in"] * price_in + stats["out"] * price_out) / 1e6
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"entries": sorted(entries.values(), key=lambda e: e["term"].lower())},
                   indent=1, ensure_ascii=False),
        encoding="utf-8")

    print(f"\n{len(entries):,} terms -> {args.out} ({args.out.stat().st_size/1024:.0f} KB)")
    print(f"{len(work):,} chunks in {elapsed:.0f}s; {stats['failed']} failed, "
          f"{stats['dropped']} malformed entries dropped")
    print(f"tokens in={stats['in']:,} out={stats['out']:,}; actual cost ${spent:.2f} "
          f"(estimated ${estimate:.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
