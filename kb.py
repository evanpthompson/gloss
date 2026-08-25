"""The local knowledge base: a glossary the live path can read for free.

gloss's chain is two hosted vendors deep, and both can be out at once. More
often, both are simply slower than a person glancing at a second screen can
afford. This is the floor under the chain and the fast path in front of it: a
dictionary of terms, built once from books that are already on disk, answered
locally in microseconds with no network and no tokens.

**Why a glossary and not retrieval.** Lexical retrieval over these books was
built and measured first, and it does not work — 3 of 10 test terms produced
anything usable. The reason is structural rather than tunable: the corpus is
prose *about* concepts, not a reference work *defining* them. "Circuit breaker
pattern" retrieves "the circuit breaker pattern can help here to handle system
dependencies", which is a mention, and no amount of ranking turns a mention
into a definition. Books explain across paragraphs; a card has 25 words.

So the reading happens once, offline, where there is no latency budget and no
conversation waiting: `tools/build_kb.py` sends each passage to a model and
asks what it *defines*. That pass produced 17 usable entries out of 18 on the
same corpus, for well under a dollar. What ships is its output — a plain dict,
loaded at startup, queried with no model in the loop at all.

**Only jargon, never recall.** A recall card claims the person's own notes say
something, and the system prompt forbids inventing a fact that is not written
there. A book is not their notes. This module can only ever produce `jargon`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_PATH = Path("kb/glossary.json")


class Entry(BaseModel):
    """One glossary term. Validated on load, not trusted from disk.

    The file is model output that was written to disk by a build script, which
    makes it exactly as trustworthy as model output — a stale or hand-edited
    file must fail loudly at startup rather than put a malformed card on screen
    mid-conversation.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    term: str = Field(min_length=1)
    definition: str = Field(min_length=1)
    source: str = ""
    """Which book it came from. Shown to no one, but a wrong definition is
    traceable to a passage without rebuilding anything."""


def normalise(term: str) -> str:
    """The lookup key. Case, punctuation and spacing all vary in speech.

    Apostrophes are **deleted rather than treated as a separator**, so
    "Conway's Law" becomes `conways law`. A speech recogniser rarely emits an
    apostrophe — Nova-3 transcribes it as "conways law" — and splitting on one
    made every possessive term in the glossary permanently unmatchable while
    looking perfectly correct in the file.
    """
    without_apostrophes = term.lower().replace("'", "").replace("\u2019", "")
    return re.sub(r"[^a-z0-9]+", " ", without_apostrophes).strip()


class Glossary:
    """Terms in, at most one card out. Never raises during a conversation."""

    def __init__(self, entries: list[Entry]) -> None:
        self._by_term: dict[str, Entry] = {}
        for entry in entries:
            key = normalise(entry.term)
            # Longest definition wins a collision: two books defining the same
            # term is common, and the fuller one is more likely to stand alone
            # in 25 words without the surrounding chapter.
            if key and (key not in self._by_term
                        or len(entry.definition) > len(self._by_term[key].definition)):
                self._by_term[key] = entry
        # Longest first, so "east west traffic" is preferred over "traffic".
        self._keys = sorted(self._by_term, key=lambda k: -len(k))

    def __len__(self) -> int:
        return len(self._by_term)

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> Glossary:
        """Read the built glossary, or an empty one if it has not been built.

        An absent file is not an error: the knowledge base is optional, and a
        deployment without one should run with the chain it has rather than
        refuse to start. A *malformed* file is a different matter and raises —
        that is a build that went wrong, and it should be found now.
        """
        if not path.exists():
            return cls([])
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls([Entry.model_validate(e) for e in raw.get("entries", [])])

    def lookup(self, text: str) -> Entry | None:
        """The longest glossary term appearing in `text`, or None.

        Whole-phrase matching on word boundaries, not a bag of words. The
        prototype that scored a bag of words answered "chaos engineering" with
        a sentence about *context* engineering — a confident answer about a
        different term, which is the worst thing this can do on a screen
        somebody trusts. A term either appears or it does not.
        """
        haystack = f" {normalise(text)} "
        for key in self._keys:
            if f" {key} " in haystack:
                return self._by_term[key]
        return None

    def card(self, text: str) -> dict | None:
        """A jargon card for `text`, or None when nothing is known.

        None is the common and correct answer. Emitting a card the glossary is
        not sure of would be worse than emitting nothing: this is the link that
        runs when no model is available to be second-guessed by.
        """
        entry = self.lookup(text)
        if entry is None:
            return None
        return {
            "id": normalise(entry.term).replace(" ", "-"),
            "kind": "jargon",
            "label": entry.term,
            "detail": entry.definition,
        }
