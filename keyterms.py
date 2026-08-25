"""Terms to prime the speech recogniser with, mined from the prep pack.

Nova-3 accepts `keyterm` prompting: domain vocabulary supplied up front, which
it then recognises far more reliably. gloss already has exactly the right
corpus for this and was not using it — the prep pack is, by definition, the
words this particular conversation is going to contain.

**Why this matters more here than it looks.** Everything downstream reads the
transcript. A jargon card fires on a term the recogniser heard; a recall card
matches notes against words the recogniser produced. If Nova-3 renders "Kestrel"
as "kestrel gate" or "customer", every layer after it is working on the wrong
string, and no amount of care in the prompt or the retrieval can recover it.
Priming is upstream of all of it.

**What makes a good keyterm is not what makes a good heading.** Prep packs are
prose. In this repo's own packs, `**bold**` is used to emphasise whole
sentences — "**Two independent captures instead of a diarization model.**" — so
bold is a noisy source, while backticks and acronyms are almost pure signal.
Sources are therefore ranked, not pooled, and anything sentence-shaped is
dropped rather than truncated.

Deepgram caps keyterms at 500 tokens per request and errors above that, so the
list is filled in rank order until a conservative budget is reached, and what
did not fit is logged rather than silently dropped.
"""

from __future__ import annotations

import re
from collections import Counter

# Deepgram documents a 500-token ceiling per request. The default sits below it
# because their tokenizer is not ours: overshooting produces an error at connect
# time, which is loud and early, but a conversation that refuses to start is
# still a conversation that refuses to start.
DEFAULT_BUDGET_TOKENS = 400

MAX_WORDS = 4
"""Longer than this is a phrase, not a term. Multi-word keyterms are supported,
but a sentence handed to the recogniser as vocabulary is noise."""

# All-caps strings that are punctuation or prose, not domain vocabulary.
# Capitalised English words that are not names. The positional test above is
# the primary defence and catches these wherever they open a sentence; this is
# the second layer, for the ones that appear mid-sentence after a dash or a
# quote. A deny-list can only ever list what somebody has already tripped over,
# which is why it is second and not first.
NOT_NAMES = frozenset(
    {"the", "this", "that", "these", "those", "there", "their", "they", "them",
     "it", "its", "we", "our", "you", "your", "he", "she", "but", "and", "or",
     "if", "when", "while", "where", "what", "why", "how", "not", "no", "yes",
     "so", "then", "than", "also", "both", "each", "every", "some", "any",
     "all", "one", "two", "three", "first", "second", "next", "last", "only",
     "even", "just", "still", "now", "here", "for", "with", "without", "from",
     "into", "onto", "about", "after", "before", "because", "should", "would",
     "could", "will", "can", "may", "must", "does", "did", "was", "were"}
)

NOT_ACRONYMS = frozenset(
    {"I", "A", "OK", "TODO", "FIXME", "NOTE", "AND", "OR", "NOT", "THE", "IF",
     "NO", "YES", "ALL", "NEW", "OLD", "API", "URL", "HTTP", "JSON"}
)

# Spans that are paths, filenames or code rather than things a human says out
# loud. A prep pack written by an engineer is full of these, and each one that
# gets through costs budget that a term like "Kestrel" needed.
_PATHISH = re.compile(r"[/\\]|\.(md|py|json|yml|yaml|toml|txt|sh)(:\d+)?$|^--|^-[a-z]$")
_CODEY = re.compile(r"""[(){}\[\]<>=;|$"']|::|\w+:\d""")
_HASHLIKE = re.compile(r"^[0-9a-f]{7,40}$")

_BACKTICK = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9]{1,7}\b")

# Characters that, immediately before a capitalised word, mean it is starting a
# sentence, heading or list item — where capitalisation says nothing about
# whether the word is a proper noun.
# `[-*#>]*` and not `[-*#>]?`: a level-two heading opens with "## ", and
# allowing only one marker let every heading's first word through as if it were
# a proper noun — which is how "Who", "Raising" and "Something" got in.
# `:` and `;` are deliberately NOT boundaries. English does not capitalise
# after a colon, so a capital there is evidence of a proper noun — and
# "Company: Example Corp" is exactly the shape prep packs use for the names
# that matter most. Treating a colon as a sentence start dropped every one.
_SENTENCE_START = re.compile(r"(?:^|[.!?]\s|\n\s*[-*#>]*\s*|\n)$")

# A run of capitalised words, so "Deepgram Nova-3" is one term rather than two.
_PROPER_RUN = re.compile(r"\b[A-Z][\w.-]*(?:\s+[A-Z][\w.-]*){0,3}\b")


def _clean(term: str) -> str:
    return term.strip().strip("*_`\"'“”().,;:!?").strip()


def _is_termlike(term: str) -> bool:
    if not term or len(term) < 2:
        return False
    if len(term.split()) > MAX_WORDS:
        return False
    if _PATHISH.search(term) or _CODEY.search(term) or _HASHLIKE.match(term):
        return False
    # Needs at least one letter; a bare number or version string is not a term
    # the recogniser benefits from.
    return any(c.isalpha() for c in term)


def _trim_stopwords(term: str) -> str:
    """Shave leading and trailing English words off a capitalised run.

    "The Bluetooth trap" is a sentence fragment wrapped around one real term.
    Trimming beats rejecting: the name in the middle is the part worth priming
    the recogniser with.
    """
    words = term.split()
    while words and words[0].lower() in NOT_NAMES:
        words.pop(0)
    while words and words[-1].lower() in NOT_NAMES:
        words.pop()
    return " ".join(words)


def _reads_as_a_name(term: str) -> bool:
    """Whether a heading or bold span is a term rather than a sentence.

    Every word has to look like part of a name — capitalised, all-caps, or a
    number. "Kestrel" and "Nova-3 Migration" pass; "Something that went badly"
    and "Latency on a hot read path" do not. This replaces a word-count cap,
    which let four-word section titles straight through.
    """
    words = term.split()
    return bool(words) and all(
        w[0].isupper() or w.isupper() or w[0].isdigit() for w in words
    )


def _capitalised_terms(text: str) -> Counter[str]:
    """Proper nouns, judged by position rather than by a stoplist.

    A capitalised word at the start of a sentence tells you nothing — "Two",
    "Why", "Keeps" all look like proper nouns there. A word that appears
    capitalised *mid-sentence* at least once is one. This is why there is no
    hand-maintained list of English words to exclude: a stoplist can only ever
    enumerate the ones somebody already tripped over.
    """
    found: Counter[str] = Counter()
    # Leading newline so the first line of a document is recognised as a
    # sentence start like any other. Without it, a file opening with "# Who I'm
    # talking to" yielded "Who I" as a proper noun.
    text = "\n" + text
    for match in _PROPER_RUN.finditer(text):
        preceding = text[max(0, match.start() - 40) : match.start()]
        if _SENTENCE_START.search(preceding):
            continue  # sentence-initial: capitalisation is not evidence
        term = _trim_stopwords(_clean(match.group(0)))
        if term and _is_termlike(term) and not term.isupper():
            found[term] += 1
    return found


def extract(pack_text: str) -> list[str]:
    """Candidate keyterms from a prep pack, best first.

    Ranked by source rather than pooled, because the sources differ wildly in
    precision. Within a source, more mentions ranks higher — a term the notes
    return to is a term the conversation will return to.
    """
    ranked: list[str] = []
    seen: set[str] = set()

    def add(terms: list[tuple[str, int]]) -> None:
        for term, _count in sorted(terms, key=lambda t: -t[1]):
            key = term.lower()
            if key not in seen:
                seen.add(key)
                ranked.append(term)

    # 1. Backticked spans. Near-pure signal in a technical pack.
    backticks = Counter(
        t for raw in _BACKTICK.findall(pack_text) if _is_termlike(t := _clean(raw))
    )
    add(list(backticks.items()))

    # Code spans are done with. Every later pass reads prose only — otherwise
    # the acronym scan mines "PLAN" out of `reunravel/docs/PLAN.md` and the
    # proper-noun scan mines "True" out of `include_loopback=True`, both of
    # which this function has just deliberately rejected as whole terms.
    prose = _BACKTICK.sub(" ", pack_text)

    # 2. Acronyms. The single highest-value class for a recogniser, and the
    #    class it most reliably mangles.
    acronyms = Counter(
        t for raw in _ACRONYM.findall(prose)
        if (t := _clean(raw)) not in NOT_ACRONYMS and _is_termlike(t)
    )
    add(list(acronyms.items()))

    # 3. Proper nouns.
    add(list(_capitalised_terms(prose).items()))

    # 4. Short headings. A heading that is a sentence is a section title, not a
    #    term, so the word cap does the filtering.
    headings = Counter(
        t
        for raw in _HEADING.findall(prose)
        if _is_termlike(t := _clean(raw)) and _reads_as_a_name(t)
    )
    add(list(headings.items()))

    # 5. Short bold spans, last because in these packs bold usually emphasises a
    #    whole sentence. The ones that survive the word cap are real terms.
    bold = Counter(
        t
        for raw in _BOLD.findall(prose)
        if _is_termlike(t := _clean(raw)) and _reads_as_a_name(t)
    )
    add(list(bold.items()))

    return ranked


def fit(terms: list[str], budget_tokens: int = DEFAULT_BUDGET_TOKENS) -> tuple[list[str], int]:
    """Take terms in rank order until the budget is spent.

    Returns `(kept, dropped)`. Dropping is expected rather than exceptional — a
    long pack yields far more candidates than Deepgram will accept — but the
    count is returned so the caller can say so out loud. A silently truncated
    vocabulary looks identical to a fully primed one right up until the term
    that mattered is the one that was cut.
    """
    kept: list[str] = []
    spent = 0
    for term in terms:
        # Conservative: a word is rarely more than two tokens, plus a separator.
        cost = len(term.split()) * 2 + 1
        if spent + cost > budget_tokens:
            break
        kept.append(term)
        spent += cost
    return kept, len(terms) - len(kept)


def from_pack(pack_text: str, budget_tokens: int = DEFAULT_BUDGET_TOKENS) -> tuple[list[str], int]:
    """Everything above, in one call: pack text in, keyterms out."""
    return fit(extract(pack_text), budget_tokens)
