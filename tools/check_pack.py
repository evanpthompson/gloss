"""Gate a prep pack before it is ever used in a live conversation.

Two independent checks, both of which fail closed:

1. **Leak scan.** Prep documents are written to be read *before* a call and
   routinely contain things that must never reach a card on a screen during
   one — a salary floor, a walk-away number, notes about the interviewer. The
   pack goes into the system prompt, so anything in it can surface. This is an
   allow-nothing scan: any hit fails, and you delete the line or move it out of
   the pack.

2. **Cache-minimum check.** Every provider silently stops caching below a
   minimum prefix size. Under it, the whole pack is reprocessed on every turn,
   which spends latency in the one place this design has none.

    uv run python tools/check_pack.py sessions/my-call
    uv run python tools/check_pack.py sessions/my-call --provider anthropic

Exit code is 0 only if both checks pass, so this can gate a script.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers import PROFILES, estimate_tokens

# The cache floors used to be a second copy of the numbers in providers.py,
# which meant this gate and the server could disagree about whether a pack was
# big enough — and the gate is the one that says "OK". One table now, and it is
# the same table the server resolves its model from.

# Anything matching these must not be in a pack that becomes a system prompt.
# Deny-listing is the weaker half of this gate by design: it cannot enumerate
# what nobody has thought of yet, so the skill that writes packs is what has to
# build them from an allow-list of sections. This catches the known-bad.
LEAK_PATTERNS = [
    (r"\bsalary\b", "salary discussion"),
    (r"\bcomp(?:ensation)?\s+(?:range|band|target|expectation)", "comp target"),
    (r"\bwalk[- ]away\b", "walk-away number"),
    (r"\byour floor\b|\bfloor is\b", "salary floor"),
    (r"\$\s?\d{2,3}[,.]?\d{0,3}\s*(?:k\b|,000)", "dollar figure"),
    (r"\bRSU|equity grant\b", "equity detail"),
    (r"\bdo not improvise\b|\bbefore you dial\b", "pre-call coaching"),
    (r"\bNEEDS INPUT\b", "unfilled placeholder"),
    (r"\brecruiter\b.*\b(?:said|told|mentioned)\b", "recruiter side-channel"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pack", type=Path, help="prep pack directory, e.g. sessions/my-call")
    # Defaults to whatever GLOSS_PROVIDER the server would resolve, so
    # `check_pack sessions/x` gates against the provider actually in use rather
    # than against whichever one was hardcoded here.
    ap.add_argument(
        "--provider",
        default=os.environ.get("GLOSS_PROVIDER", "anthropic"),
        choices=sorted(PROFILES),
    )
    args = ap.parse_args()

    files = sorted(args.pack.glob("*.md"))
    if not files:
        print(f"FAIL  no *.md files in {args.pack}", file=sys.stderr)
        return 1

    ok = True

    # --- 1. leak scan -------------------------------------------------------
    hits: list[str] = []
    for f in files:
        for lineno, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for pattern, label in LEAK_PATTERNS:
                if re.search(pattern, line, re.IGNORECASE):
                    hits.append(f"  {f.name}:{lineno}  [{label}]  {line.strip()[:78]}")
                    break

    if hits:
        ok = False
        print(f"FAIL  {len(hits)} line(s) must not be in a live prep pack:")
        print("\n".join(hits[:20]))
        if len(hits) > 20:
            print(f"  ... and {len(hits) - 20} more")
        print("\n  These reach the system prompt and can surface on screen mid-call.")
        print("  Delete them, or keep them in the pre-call doc and out of the pack.")
    else:
        print(f"PASS  leak scan clean across {len(files)} file(s)")

    # --- 2. cache minimum ---------------------------------------------------
    combined = "\n\n".join(f.read_text(encoding="utf-8").strip() for f in files)
    tokens = estimate_tokens(combined)
    profile = PROFILES[args.provider]
    minimum = profile.cache_min_tokens

    if minimum is None:
        # Nobody has looked this provider's floor up yet. That is not the same
        # as "no floor": a pack that silently never caches costs latency on
        # every single turn, in the one place this design has none. Report it
        # as unrun and fail, rather than printing a PASS nobody checked.
        ok = False
        print(
            f"NOT RUN  {args.provider}'s cache minimum is unverified, so this "
            f"pack (~{tokens} tokens) cannot be checked."
        )
        print(f"         {profile.note}")
        print(
            "         Look the floor up and set cache_min_tokens for "
            f"{args.provider!r} in providers.py — an unrun check is not a pass."
        )
    elif tokens < minimum:
        ok = False
        short = minimum - tokens
        print(
            f"FAIL  ~{tokens} tokens, under {args.provider}'s {minimum}-token "
            f"cache minimum by ~{short} (~{int(short * 0.75)} words)."
        )
        print("      Every turn will reprocess the whole pack. Add more material,")
        print("      or accept the latency knowingly.")
    else:
        print(
            f"PASS  ~{tokens} tokens, clears {args.provider}'s {minimum}-token "
            f"minimum for {profile.model}"
        )

    print(f"\n{'OK' if ok else 'NOT READY'}: {args.pack} ({len(files)} files)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
