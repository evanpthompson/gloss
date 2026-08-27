"""The prep pack, indexed — the recall half of the local floor.

`kb.py` gave the chain a floor for `jargon` cards and stopped there, for a
reason that was correct at the time: a book is not the person's notes, so a
book can never source a `recall` card. But that left the outage behaviour
backwards. If both vendors are out, the one thing gloss could not reach was the
pack the person wrote *for this specific call* — the material with the highest
value per word in the entire system, already parsed and sitting in memory.

**The objection that killed retrieval-over-books does not apply here.** That
objection was about *definitions*: prose that mentions a term is not prose that
defines it, and no amount of ranking turns a mention into a definition. A
recall card makes no such claim. Its whole job is to put back on screen
something the person already wrote down, and the system prompt's rule — "never
invent a fact, a number, or an anecdote that is not written there" — is
satisfied by construction here, because `detail` is a span copied out of the
pack rather than anything generated.

**Why this is scored overlap and not phrase matching.** `Glossary.lookup` holds
that a term either appears or it does not, and that is right for a dictionary.
It is wrong here. "Tell me about a time you brought down latency on a read
path" contains no exact phrase from a note headed "Latency on a hot read path",
so a phrase matcher would stay silent on precisely the turn this exists for.
Speech restates; notes do not. So the match is on shared distinctive words, and
the safety comes from the three rules in `lookup()` instead.

**No new exposure.** The pack is already the system prompt and already reaches
the screen as model-written recall cards. This quotes the same bytes by a
shorter route. `tools/check_pack.py` remains the gate on what may be in a pack.
"""

from __future__ import annotations

import math
import re
from collections import Counter

import keyterms
from cards import slug
from kb import normalise

MAX_LABEL_WORDS = 6
"""The card contract's cap on a label. A heading longer than this is trimmed
rather than dropped: the first six words of a section title still name it."""

MAX_DETAIL_WORDS = 25
"""The card contract's cap on detail. A longer span is cut, never summarised —
summarising is generating, and this module must only ever quote."""

HEADING_WEIGHT = 2.0
"""A word in the section's heading counts double. Headings are what the person
chose to call the topic, so a word there is evidence about the whole section;
the same word buried in a body sentence may be incidental."""

MIN_WORD_LEN = 3

# Layer two, behind the document-frequency filter in `Notes.__init__`.
#
# That filter is the primary defence and needs no maintenance: a word common
# across the pack is dropped because it is common, whoever wrote it. What it
# cannot catch is a word that is rare *in the pack* and yet meaningless *in
# speech* — "time" appearing once in one section, matched by "tell me about a
# time", is a card sourced entirely from filler. Those are listed here, and the
# list is speech-shaped rather than a general English stoplist, because the
# general case is already handled above.
FILLER = frozenset(
    {"the", "and", "but", "for", "with", "that", "this", "these", "those",
     "there", "their", "they", "them", "you", "your", "our", "was", "were",
     "are", "has", "have", "had", "how", "what", "why", "when", "where",
     "who", "which", "not", "any", "all", "one", "two", "can", "could",
     "would", "should", "will", "may", "much", "many", "more", "most", "some",
     "from", "into", "onto", "about", "over", "under", "than", "then", "also",
     "just", "like", "kind", "sort", "really", "actually", "basically",
     "maybe", "sure", "yeah", "okay", "right", "well", "know", "think",
     "tell", "talk", "say", "said", "get", "got", "make", "made", "take",
     "took", "give", "want", "need", "see", "look", "come", "going", "did",
     "does", "done", "time", "times", "thing", "things", "stuff", "way",
     "ways", "lot", "bit", "little", "good", "bad", "great", "let", "put",
     "here", "now", "out", "off", "own", "yes", "because", "example"}
)

_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
_BULLET = re.compile(r"^\s*[-*+]\s+|^\s*\d+[.)]\s+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_EMPHASIS = re.compile(r"[*_`]+")


def names_in(pack_text: str) -> frozenset[str]:
    """The words in the pack that are names rather than ordinary English.

    This exists because of three false positives measured on the example pack:
    "hand" (from "hand-rolled"), "second" (from "a second path") and "next"
    (from "next time") each appear exactly once, which made them unique to a
    section — and so each one alone was enough to put a story on screen, from
    "hand over to my colleague", "share my screen for a second" and "your next
    role". Small talk, answered with a rehearsed anecdote.

    Rarity within a pack is not the same as rarity in English, and the
    difference is almost exactly proper-nounhood: "Northwind" and "Kestrel" are
    names, "hand" and "second" are not. keyterms.py already draws that line, by
    position rather than by a word list, because it had to solve the same
    problem for the speech recogniser — so the line is drawn there once.
    """
    return frozenset(
        word
        for term in keyterms.extract(pack_text)
        for word in normalise(term).split()
        if len(word) >= 2
    )


def _indexable(word: str) -> bool:
    """Whether a normalised word is worth matching on at all."""
    return len(word) >= MIN_WORD_LEN and word not in FILLER and not word.isdigit()


def _words(text: str) -> set[str]:
    return {w for w in normalise(text).split() if _indexable(w)}


def _tidy(text: str) -> str:
    """A pack line as it should read on a card: markup off, spacing collapsed."""
    return re.sub(r"\s+", " ", _EMPHASIS.sub("", _BULLET.sub("", text))).strip()


def _segments(lines: list[str]) -> list[str]:
    """A section's body, cut into the spans a card could quote.

    Prep packs are hard-wrapped prose, so a raw line is usually half a thought —
    "request-coalescing layer and a short-TTL cache keyed on the stable part of
    the" is a real line from the example pack and a terrible card. Lines are
    therefore rejoined into paragraphs and re-split on sentence boundaries.
    Bullets are exempt: a bullet is already the unit its author intended.
    """
    segments: list[str] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            joined = " ".join(paragraph)
            segments.extend(s for part in _SENTENCE.split(joined) if (s := _tidy(part)))
            paragraph.clear()

    for line in lines:
        if not line.strip():
            flush()
        elif _BULLET.match(line):
            flush()
            if tidied := _tidy(line):
                segments.append(tidied)
        else:
            paragraph.append(line.strip())
    flush()
    return segments


class Section:
    """One heading and the prose under it. The unit a recall card names."""

    def __init__(self, heading: str, body: list[str]) -> None:
        self.heading = _tidy(heading)
        self.segments = _segments(body)
        self.heading_words = _words(self.heading)
        self.segment_words = [_words(s) for s in self.segments]
        self.words = self.heading_words.union(*self.segment_words) if self.segments else set()

    @property
    def label(self) -> str:
        return " ".join(self.heading.split()[:MAX_LABEL_WORDS])


def sections(pack_text: str) -> list[Section]:
    """Split a pack on markdown headings, flat rather than nested.

    Flat because the useful label is the nearest heading: a card reading
    "Latency on a hot read path" says more than one reading "Stories". Text
    before the first heading is dropped — a card needs a name, and an unnamed
    preamble has none to give it.
    """
    found: list[Section] = []
    heading: str | None = None
    body: list[str] = []
    for line in pack_text.splitlines():
        if match := _HEADING.match(line):
            if heading is not None:
                found.append(Section(heading, body))
            heading, body = match.group(2), []
        elif heading is not None:
            body.append(line)
    if heading is not None:
        found.append(Section(heading, body))
    return [s for s in found if s.segments]


class Notes:
    """The prep pack, in. At most one recall card, out. Never raises live."""

    def __init__(self, found: list[Section], names: frozenset[str] = frozenset()) -> None:
        self._sections = found
        # Empty by default, which switches the single-word rule in `lookup` off
        # entirely. A caller that has not said which words are names gets the
        # stricter matcher, not a guess about which ones might be.
        self._names = names
        count = len(found)
        frequency = Counter(word for s in found for word in s.words)
        # A word indexes only if it appears in at most half the sections. This
        # is the stoplist, derived from the pack rather than maintained by
        # hand: "engineering" is a distinctive word in one pack and noise in
        # another, and only the pack knows which. It also scales down —
        # in a two-section pack, only words unique to a section index at all.
        cap = max(1, count // 2)
        self._idf = {
            word: math.log((1 + count) / (1 + df)) + 1
            for word, df in frequency.items()
            if df <= cap
        }
        self._df = {word: frequency[word] for word in self._idf}

    def __len__(self) -> int:
        return len(self._sections)

    @classmethod
    def from_pack(cls, pack_text: str) -> Notes:
        return cls(sections(pack_text), names_in(pack_text))

    def lookup(self, text: str) -> tuple[Section, str] | None:
        """The section this turn is about and the line to quote, or None.

        Three rules, and all three exist to make silence the default:

        1. **Two matched words, or one name unique to a single section.** One
           word shared with four sections is a coincidence. One *name* that
           appears in exactly one section is the strongest signal available —
           but an ordinary word that happens to appear once is not a name, and
           treating it as one answered small talk with a rehearsed anecdote.
           See `names_in`.
        2. **The best score wins, but a tie wins nothing.** Two sections
           matching equally well means the turn does not distinguish them, and
           guessing between them puts the wrong story on screen with the same
           confidence as the right one.
        3. **Only indexable words count** — see `__init__` and `FILLER`.

        This link runs when every vendor is out, so there is nothing above it
        to disagree with a bad answer. None is the common and correct result.
        """
        spoken = _words(text) & self._idf.keys()
        if not spoken:
            return None

        scored: list[tuple[float, Section, set[str]]] = []
        for section in self._sections:
            matched = spoken & section.words
            if not matched:
                continue
            if len(matched) < 2 and not any(
                self._df[w] == 1 and w in self._names for w in matched
            ):
                continue
            score = sum(
                self._idf[w] * (HEADING_WEIGHT if w in section.heading_words else 1.0)
                for w in matched
            )
            scored.append((score, section, matched))

        if not scored:
            return None
        scored.sort(key=lambda row: -row[0])
        if len(scored) > 1 and math.isclose(scored[0][0], scored[1][0], rel_tol=1e-9):
            return None

        _, section, matched = scored[0]
        # The line with the most words in common with what was said — but it
        # has to earn the departure. A section matched on its heading overlaps
        # no line at all, and one incidental word is not evidence that some
        # line in the middle beats the one its author opened with. Two is.
        best = max(
            range(len(section.segments)),
            key=lambda i: (len(matched & section.segment_words[i]), -i),
        )
        if len(matched & section.segment_words[best]) < 2:
            best = 0
        return section, section.segments[best]

    def card(self, text: str) -> dict | None:
        """A recall card for `text`, or None when the notes say nothing.

        `id` is the section's slug and not the matched topic, so the card keeps
        one identity across every turn that returns to this story and updates in
        place rather than appearing again. The cost is that it rarely collides
        with the id a model would choose for the same topic, so a preview from
        here is usually expired-and-replaced by the model's card rather than
        updated in place. Stability across turns is worth more than that.
        """
        found = self.lookup(text)
        if found is None:
            return None
        section, quote = found
        words = quote.split()
        detail = " ".join(words[:MAX_DETAIL_WORDS]) + ("…" if len(words) > MAX_DETAIL_WORDS else "")
        return {
            "id": slug(section.heading) or "note",
            "kind": "recall",
            "label": section.label,
            "detail": detail,
        }
