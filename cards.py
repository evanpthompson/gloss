"""The card contract, defined once.

A card is the only thing gloss ever puts on the screen, and it arrives from a
language model — which is to say from the least trustworthy input in the
system. It was previously a hand-written JSON schema plus a hand-written
validity check in `enrich()` that re-stated the same three required fields.
Two statements of one contract drift, and the one that drifts silently is the
check, because a card that fails it is simply dropped.

Now the pydantic model is the definition. The JSON schema sent to the provider
is derived from it, and the validation applied to what comes back is the same
model, so there is nothing to keep in sync.

**Why validation is per-card and not per-batch.** Zero cards is the correct
answer for most turns — small talk, logistics, an utterance that is not really
a question. Asked for zero, Gemini 3.6 returns `[{}]` rather than `[]`, because
`required` is advice to a model rather than a constraint on it. Handing the
whole batch to pydantic would turn that ordinary quiet turn into a validation
failure and put "Enrichment is down" on the screen during the most common case
there is. So the batch is accepted leniently, each card is validated on its
own, and the junk ones are dropped and counted.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CardKind = Literal["recall", "jargon"]
"""What a model may emit. `error` is deliberately absent: an error card is the
server speaking about itself, not model output, and letting the model emit one
would let a bad turn impersonate an outage."""


class Card(BaseModel):
    """One card. Every field is load-bearing on a screen read in one second."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: CardKind
    label: str = Field(min_length=1, description="Two or three words, the thing itself.")
    detail: str = Field(
        min_length=1, description="One line. What the person needs in order to respond."
    )


class EmitCards(BaseModel):
    """The batch a model returns for one turn. Empty is the common answer."""

    model_config = ConfigDict(extra="forbid")

    cards: list[Card] = Field(default_factory=list)


def _flatten(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline `$defs`/`$ref` and drop `title` noise.

    pydantic emits nested models as `$ref` pointers into `$defs`. Provider
    schema dialects vary in whether they resolve those — Gemini's
    `responseSchema` is the strict one — so the reference is resolved here
    rather than hoped about. `title` keys are pydantic's own decoration; they
    are prompt bytes that say nothing to a model, and they sit in the cached
    prefix where bytes are the thing being paid for.
    """
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].rsplit("/", 1)[-1]]))
            return {k: walk(v) for k, v in node.items() if k not in ("$defs", "title")}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def card_schema(max_cards: int) -> dict[str, Any]:
    """The JSON schema handed to the provider, derived from the model above.

    `max_cards` is runtime configuration rather than part of the type, so it is
    applied here instead of as a pydantic constraint — the model is the shape
    of a card, not the operator's preference about how many fit on a screen.
    """
    schema = _flatten(EmitCards.model_json_schema())
    schema["properties"]["cards"]["maxItems"] = max_cards
    # langchain-anthropic turns structured output into a tool call, and a tool
    # needs a name. Without this it raises "Unsupported function" at import,
    # before any call is made. Named as a verb because this string is the one
    # part of the schema a model reads as an instruction.
    schema["title"] = "emit_cards"
    return schema


def valid_cards(raw: Any, max_cards: int) -> tuple[list[dict[str, Any]], int]:
    """Keep the cards that are actually cards; count what was dropped.

    Returns `(cards, dropped)` with cards as plain dicts, ready to serialise
    onto the wire. Anything that is not a complete, correctly-typed card is
    discarded rather than repaired — a half-rendered card on a screen someone
    is glancing at during a live conversation is worse than no card.
    """
    if not isinstance(raw, list):
        return [], 0
    kept: list[dict[str, Any]] = []
    dropped = 0
    for item in raw:
        try:
            kept.append(Card.model_validate(item).model_dump())
        except Exception:  # pydantic ValidationError, or item is not a mapping
            dropped += 1
    return kept[:max_cards], dropped
