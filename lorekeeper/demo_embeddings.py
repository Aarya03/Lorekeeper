"""Offline, deliberately small hand-curated semantic vocabulary for judging.

This is a constrained rehearsal mode, not a replacement for a trained embedding model.
Only listed words are accepted, and their vectors encode curated concept tags.
"""
from __future__ import annotations

import hashlib
import math
import re

WORD_TAGS = {
    "comet": ("space", "astronomy", "transient", "celestial"), "meteor": ("space", "astronomy", "transient", "celestial"),
    "asteroid": ("space", "astronomy", "rock", "celestial"), "orbit": ("space", "astronomy", "motion", "celestial"),
    "planet": ("space", "astronomy", "celestial", "world"), "moon": ("space", "astronomy", "celestial", "world"),
    "star": ("space", "astronomy", "celestial", "light"), "galaxy": ("space", "astronomy", "universe", "celestial"),
    "universe": ("space", "astronomy", "universe"), "telescope": ("space", "astronomy", "instrument", "observation"),
    "observatory": ("space", "astronomy", "place", "observation"), "sky": ("space", "astronomy", "atmosphere"),
    "satellite": ("space", "astronomy", "instrument", "orbit"), "eclipse": ("space", "astronomy", "light", "celestial"),
    "rings": ("space", "astronomy", "planet", "shape"), "ring": ("space", "astronomy", "planet", "shape"),
    "astronomy": ("space", "astronomy", "study"), "space": ("space", "astronomy", "universe"),
    "gravity": ("physics", "motion", "space", "force"), "energy": ("physics", "power", "measurement"),
    "atom": ("physics", "chemistry", "matter", "particle"), "molecule": ("chemistry", "matter", "particle"),
    "light": ("physics", "wave", "energy", "observation"), "wave": ("physics", "wave", "energy"),
    "force": ("physics", "motion", "measurement"), "motion": ("physics", "motion", "measurement"),
    "particle": ("physics", "matter", "particle"), "magnet": ("physics", "force", "instrument"),
    "experiment": ("research", "laboratory", "observation", "method"), "research": ("research", "study", "method"),
    "hypothesis": ("research", "study", "method"), "observation": ("research", "observation", "method"),
    "data": ("research", "measurement", "method"), "result": ("research", "measurement", "method"),
    "sample": ("laboratory", "biology", "chemistry", "specimen"), "sensor": ("laboratory", "instrument", "measurement"),
    "microscope": ("laboratory", "instrument", "biology", "observation"), "laboratory": ("laboratory", "research", "place"),
    "lab": ("laboratory", "research", "place"), "signal": ("measurement", "communication", "observation"),
    "cell": ("biology", "matter", "specimen"), "organism": ("biology", "specimen", "life"),
    "water": ("earth", "chemistry", "matter", "life"), "ocean": ("earth", "water", "place", "life"),
    "climate": ("earth", "atmosphere", "measurement"), "weather": ("earth", "atmosphere", "measurement"),
    "earth": ("earth", "planet", "place"), "volcano": ("earth", "geology", "energy"),
    "rock": ("earth", "geology", "matter"), "fossil": ("earth", "geology", "life"),
    "sound": ("physics", "wave", "observation"), "temperature": ("measurement", "physics", "earth"),
    "measurement": ("measurement", "research", "method"), "discovery": ("research", "observation", "result"),
    "mystery": ("puzzle", "research", "question"), "clue": ("puzzle", "research", "question"),
    "question": ("puzzle", "research", "question"), "answer": ("puzzle", "research", "result"),
    "signal": ("measurement", "communication", "observation"), "memory": ("story", "community", "memory"),
    "story": ("story", "community", "memory"), "community": ("story", "community", "people"),
    "people": ("community", "people"), "idea": ("research", "thought", "question"),
}

WORDS = tuple(sorted(WORD_TAGS))
_TAGS = tuple(sorted({tag for tags in WORD_TAGS.values() for tag in tags}))
_TAG_AXIS = {tag: index for index, tag in enumerate(_TAGS)}


def embed(word: str, dimensions: int | None = None) -> list[float]:
    token = str(word).casefold().strip()
    tags = WORD_TAGS.get(token)
    if not tags:
        raise ValueError("That word is not in the bundled offline practice vocabulary")
    if dimensions is None:
        from .runtime import embedding_dimensions
        dimensions = embedding_dimensions()
    if dimensions <= len(_TAGS):
        raise ValueError(f"Offline practice needs an embedding dimension above {len(_TAGS)}")
    vector = [0.0] * dimensions
    for tag in tags:
        vector[_TAG_AXIS[tag]] += 1.0
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    vector[len(_TAGS) + int.from_bytes(digest, "big") % (dimensions - len(_TAGS))] = 0.28
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector]


def choose_target(summary: str, blocked: set[str] | None = None) -> str:
    """Choose a related offline target that is not stated in the source text."""
    tokens = set(re.findall(r"[a-z]+", str(summary).casefold()))
    excluded = tokens | (blocked or set())
    source_tags = {tag for token in tokens for tag in WORD_TAGS.get(token, ())}
    ranked = []
    for word, tags in WORD_TAGS.items():
        if word in excluded:
            continue
        overlap = len(source_tags.intersection(tags))
        if overlap:
            ranked.append((overlap / math.sqrt(len(tags) * max(1, len(source_tags))), word))
    # Stable tie-breaking keeps replay deterministic while choosing a semantic
    # neighbor (e.g. comet/meteor) instead of copying "observatory" verbatim.
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked[0][1] if ranked else next(
        (word for word in ("signal", "observation", "planet", "sample", "telescope")
         if word not in excluded), "")
