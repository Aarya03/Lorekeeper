"""Rank-calibrated semantic heat for shared word hunts."""
from __future__ import annotations

import array
import bisect
import hashlib
import json
import math
import os
import struct
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from . import embeddings, runtime

_VOCABULARY = Path(__file__).resolve().parent / "resources" / "heat_reference_vocabulary.txt"
_CACHE_MAGIC = b"LKHEAT01"
_BATCH_SIZE = 512
_INDEX_LOCK = threading.Lock()
_INDEX = None
_WARMUP_LOCK = threading.Lock()
_WARMUP_STATE = "idle"
_WARMUP_ERROR = ""
_WARMUP_COMPLETED = 0
_WARMUP_TOTAL = 0


@dataclass(frozen=True)
class ReferenceIndex:
    words: tuple[str, ...]
    vectors: array.array
    dimensions: int
    fingerprint: str


def _load_words() -> tuple[str, ...]:
    from .guess_moderation import blocked_terms

    blocked = blocked_terms()
    words = []
    seen = set()
    for raw in _VOCABULARY.read_text(encoding="utf-8").splitlines():
        word = raw.strip().casefold()
        if not word or word.startswith("#") or not word.isascii() or not word.isalpha():
            continue
        if not 2 <= len(word) <= 24 or word in blocked or word in seen:
            continue
        seen.add(word)
        words.append(word)
    if len(words) < 1000:
        raise RuntimeError(f"Heat vocabulary has only {len(words)} usable words; expected at least 1,000")
    return tuple(words)


def _fingerprint(words: tuple[str, ...], identity: dict) -> str:
    digest = hashlib.sha256()
    digest.update("\n".join(words).encode("utf-8"))
    digest.update(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    digest.update(sys.byteorder.encode("ascii"))
    return digest.hexdigest()


def _cache_path(fingerprint: str) -> Path:
    return runtime.DB_PATH.parent / f"heat-reference-{fingerprint[:24]}.bin"


def _read_cache(path: Path, fingerprint: str, count: int, dimensions: int) -> array.array | None:
    try:
        with path.open("rb") as source:
            if source.read(len(_CACHE_MAGIC)) != _CACHE_MAGIC:
                return None
            raw_length = source.read(4)
            if len(raw_length) != 4:
                return None
            header_length = struct.unpack(">I", raw_length)[0]
            header = json.loads(source.read(header_length))
            payload = source.read()
        if (header.get("fingerprint") != fingerprint or header.get("count") != count
                or header.get("dimensions") != dimensions or header.get("byteorder") != sys.byteorder
                or len(payload) != count * dimensions * 4):
            return None
        values = array.array("f")
        values.frombytes(payload)
        return values
    except (OSError, ValueError, struct.error, json.JSONDecodeError):
        return None


def _write_cache(path: Path, fingerprint: str, count: int, dimensions: int, values: array.array) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = json.dumps({"fingerprint": fingerprint, "count": count,
                         "dimensions": dimensions, "byteorder": sys.byteorder},
                        separators=(",", ":")).encode("utf-8")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=".heat-reference-", delete=False) as target:
            temporary = Path(target.name)
            target.write(_CACHE_MAGIC)
            target.write(struct.pack(">I", len(header)))
            target.write(header)
            target.write(values.tobytes())
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_partial_cache(path: Path, expected_values: int) -> array.array:
    """Read completed vectors from an interrupted warmup checkpoint."""
    try:
        raw = path.read_bytes()
    except OSError:
        return array.array("f")
    if len(raw) > expected_values * 4:
        path.unlink(missing_ok=True)
        return array.array("f")
    # A process can stop in the middle of a write. Keep only complete floats;
    # the caller additionally resumes only on complete embedding boundaries.
    usable = len(raw) - (len(raw) % 4)
    values = array.array("f")
    values.frombytes(raw[:usable])
    return values


def _set_warmup_progress(completed: int, total: int) -> None:
    global _WARMUP_COMPLETED, _WARMUP_TOTAL
    with _WARMUP_LOCK:
        _WARMUP_COMPLETED = max(0, int(completed))
        _WARMUP_TOTAL = max(0, int(total))


def reference_index() -> ReferenceIndex:
    """Load cached vectors or embed the bundled reference vocabulary once."""
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    with _INDEX_LOCK:
        if _INDEX is not None:
            return _INDEX
        words = _load_words()
        identity = embeddings.semantic_cache_identity()
        dimensions = int(identity["dimensions"])
        fingerprint = _fingerprint(words, identity)
        cache = _cache_path(fingerprint)
        vectors = _read_cache(cache, fingerprint, len(words), dimensions)
        if vectors is None:
            if identity["provider"] == "demo_lexicon":
                available = set(embeddings.offline_words())
                words = tuple(word for word in words if word in available)
                if not words:
                    raise RuntimeError("Offline practice vocabulary has no overlap with the heat reference list")
                fingerprint = _fingerprint(words, identity)
                cache = _cache_path(fingerprint)
                vectors = _read_cache(cache, fingerprint, len(words), dimensions)
            if vectors is None:
                partial = Path(str(cache) + ".partial")
                expected_values = len(words) * dimensions
                vectors = _read_partial_cache(partial, expected_values)
                complete_words = len(vectors) // dimensions
                # Resume from the last whole word. Discard any interrupted tail.
                del vectors[complete_words * dimensions:]
                complete_words = min(complete_words, len(words))
                with partial.open("wb") as target:
                    target.write(vectors.tobytes())
                print(f"Embedding heat reference words with {identity['provider']} ({identity.get('model', 'local')}): {complete_words:,}/{len(words):,} cached", flush=True)
                _set_warmup_progress(complete_words, len(words))
                for offset in range(complete_words, len(words), _BATCH_SIZE):
                    batch_words = list(words[offset:offset + _BATCH_SIZE])
                    embedded = embeddings.embed_semantic_many(batch_words, timeout_seconds=60, batch_size=_BATCH_SIZE)
                    if len(embedded) != len(batch_words):
                        raise RuntimeError(f"Embedding provider returned {len(embedded)} vectors for {len(batch_words)} reference words")
                    batch_vectors = array.array("f")
                    for vector in embedded:
                        if len(vector) != dimensions:
                            raise ValueError(f"Reference embedding dimension {len(vector)} does not match {dimensions}")
                        batch_vectors.extend(float(value) for value in vector)
                    with partial.open("ab") as target:
                        target.write(batch_vectors.tobytes())
                        target.flush()
                        os.fsync(target.fileno())
                    vectors.extend(batch_vectors)
                    complete_words += len(batch_words)
                    _set_warmup_progress(complete_words, len(words))
                    print(f"Heat reference embeddings: {complete_words:,}/{len(words):,}", flush=True)
                _write_cache(cache, fingerprint, len(words), dimensions, vectors)
                partial.unlink(missing_ok=True)
                print(f"Cached heat reference embeddings at {cache}", flush=True)
        _INDEX = ReferenceIndex(words=words, vectors=vectors, dimensions=dimensions, fingerprint=fingerprint)
        return _INDEX


def warm_reference_cache() -> ReferenceIndex:
    """Explicit startup hook; safe to call again after warmup."""
    return reference_index()


def reference_warmup_status() -> dict:
    with _WARMUP_LOCK:
        return {"ready": _INDEX is not None, "state": "ready" if _INDEX is not None else _WARMUP_STATE,
                "error": _WARMUP_ERROR, "completed": _WARMUP_COMPLETED, "total": _WARMUP_TOTAL}


def begin_reference_warmup() -> None:
    """Warm the reference vectors off the HTTP startup path; retry after failures."""
    global _WARMUP_STATE, _WARMUP_ERROR, _WARMUP_COMPLETED, _WARMUP_TOTAL
    with _WARMUP_LOCK:
        if _INDEX is not None or _WARMUP_STATE == "warming":
            return
        _WARMUP_STATE = "warming"
        _WARMUP_ERROR = ""
        _WARMUP_COMPLETED = 0
        _WARMUP_TOTAL = 0

    def warm():
        global _WARMUP_STATE, _WARMUP_ERROR
        try:
            index = warm_reference_cache()
            print(f"Semantic heat calibration ready: {len(index.words):,} reference words", flush=True)
            from .signal_game import backfill_heat_ranks
            rounds, guesses = backfill_heat_ranks()
            if rounds or guesses:
                print(f"Calibrated {rounds} existing rounds and {guesses} guesses", flush=True)
            with _WARMUP_LOCK:
                _WARMUP_STATE = "ready"
        except Exception as exc:
            with _WARMUP_LOCK:
                _WARMUP_STATE = "error"
                _WARMUP_ERROR = str(exc)[:240]
            print(f"Semantic heat calibration failed: {str(exc)[:240]}", flush=True)

    threading.Thread(target=warm, daemon=True, name="semantic-heat-warmup").start()


def cosine(left, right) -> float:
    if len(left) != len(right):
        raise ValueError(f"Embedding dimensions differ ({len(left)} and {len(right)})")
    value = sum(float(a) * float(b) for a, b in zip(left, right))
    return max(-1.0, min(1.0, value))


def reference_distribution(target_vector, index: ReferenceIndex | None = None) -> list[float]:
    """Return all target-to-vocabulary cosines in ascending order for bisect."""
    index = index or reference_index()
    if len(target_vector) != index.dimensions:
        raise ValueError(f"Target embedding dimension {len(target_vector)} does not match {index.dimensions}")
    packed = memoryview(index.vectors)
    dimensions = index.dimensions
    scores = []
    for offset in range(0, len(index.words) * dimensions, dimensions):
        reference_vector = packed[offset:offset + dimensions]
        similarity = sum(float(left) * float(right) for left, right in zip(target_vector, reference_vector))
        scores.append(max(-1.0, min(1.0, similarity)))
    scores.sort()
    return scores


def score_similarity(similarity: float, sorted_similarities, exact: bool = False) -> dict:
    """Map a cosine to rank, percentile, and the requested tiered heat scale."""
    scores = sorted_similarities
    if not scores:
        raise ValueError("A non-empty reference similarity distribution is required")
    similarity = max(-1.0, min(1.0, float(similarity)))
    insertion = bisect.bisect_right(scores, similarity)
    percentile = insertion * 100.0 / len(scores)
    rank = len(scores) - insertion + 1
    if exact:
        return {"heat": 100, "percentile": 100.0, "rank": 1}
    if percentile >= 99:
        heat = 90 + min(9, int((percentile - 99) * 9))
    elif percentile >= 95:
        heat = 70 + min(19, int((percentile - 95) * 19 / 4))
    elif percentile >= 85:
        heat = 40 + min(29, int((percentile - 85) * 29 / 10))
    elif percentile >= 60:
        heat = 15 + min(24, int((percentile - 60) * 24 / 25))
    else:
        heat = min(14, int(percentile * 14 / 60))
    return {"heat": heat, "percentile": round(percentile, 3), "rank": rank}


def score_guess(guess_vector, target_vector, sorted_similarities, exact: bool = False) -> dict:
    return score_similarity(cosine(guess_vector, target_vector), sorted_similarities, exact=exact)
