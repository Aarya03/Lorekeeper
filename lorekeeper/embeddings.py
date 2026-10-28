"""Local Ollama or deterministic feature-hash embeddings for pgvector."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import functools
import urllib.request

from . import runtime


def feature_hash(text: str, dimensions: int | None = None) -> list[float]:
    dimensions = dimensions or runtime.embedding_dimensions()
    words = re.findall(r"[a-z0-9']+", text.lower())
    features = words + [words[i] + "_" + words[i + 1] for i in range(len(words) - 1)]
    vector = [0.0] * dimensions
    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=16).digest()
        index = int.from_bytes(digest[:8], "big") % dimensions
        vector[index] += 1.0 if digest[8] & 1 else -1.0
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else vector


def _embedding_config(provider):
    if provider == "ollama":
        base = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434").rstrip("/")
    else:
        base = (os.environ.get("LM_STUDIO_BASE_URL") or
                os.environ.get("LOREKEEPER_LLM_BASE_URL", "http://host.docker.internal:1234/v1")).rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
    return base, os.environ.get("EMBEDDING_MODEL", "nomic-embed-text").strip(), runtime.embedding_dimensions()


@functools.lru_cache(maxsize=4096)
def _remote_embedding(text, provider, base, model, dimensions, timeout, api_key):
    """Cached, strict model inference shared by lore retrieval and signal hunts."""
    content = text[:2000]
    # Nomic's clustering task is intended for comparing related text vectors.
    if "nomic" in model.casefold():
        content = "clustering: " + content
    headers = {"Content-Type": "application/json"}
    if provider == "ollama":
        body = json.dumps({"model": model, "input": content, "truncate": True}).encode()
        url = base + "/api/embed"
    else:
        body = json.dumps({"model": model, "input": content}).encode()
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        url = base + "/embeddings"
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read())
    vector = result["embeddings"][0] if provider == "ollama" else result["data"][0]["embedding"]
    if len(vector) != dimensions:
        raise ValueError(f"Embedding model returned {len(vector)} dimensions; configured dimensions are {dimensions}")
    values = tuple(float(value) for value in vector)
    norm = math.sqrt(sum(value * value for value in values))
    if not norm:
        raise ValueError("Embedding model returned an empty vector")
    return tuple(value / norm for value in values)


def embed_semantic(text: str, timeout_seconds: float | None = None) -> list[float]:
    """Return a real local semantic vector or raise; never substitute feature hashes."""
    provider = os.environ.get("EMBEDDING_PROVIDER", "feature_hash").strip().lower()
    if provider not in ("ollama", "lm_studio"):
        raise RuntimeError("Signal Hunt requires EMBEDDING_PROVIDER=lm_studio or ollama")
    base, model, dimensions = _embedding_config(provider)
    if not model:
        raise RuntimeError("Set EMBEDDING_MODEL to the exact embedding model ID loaded by the local server")
    timeout = timeout_seconds if timeout_seconds is not None else float(os.environ.get("EMBEDDING_TIMEOUT_SECONDS", "8"))
    timeout = max(0.2, min(20.0, float(timeout)))
    runtime.embedding_requests.labels(provider).inc()
    return list(_remote_embedding(" ".join(str(text).casefold().split()), provider, base, model,
                                  dimensions, timeout, os.environ.get("LM_STUDIO_API_KEY", "").strip()))


def embed(text: str) -> list[float]:
    provider = os.environ.get("EMBEDDING_PROVIDER", "feature_hash").strip().lower()
    if provider in ("ollama", "lm_studio"):
        try:
            return embed_semantic(text)
        except Exception as exc:
            from .runtime import embedding_fallbacks
            embedding_fallbacks.inc()
            print(f"{provider} embedding unavailable; using local feature-hash vector: {exc}", flush=True)
    elif provider not in ("feature_hash", "none", ""):
        raise RuntimeError("EMBEDDING_PROVIDER must be ollama, lm_studio, feature_hash, or none")
    else:
        runtime.embedding_requests.labels("feature_hash").inc()
    return feature_hash(text)


def pgvector_literal(text: str) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in embed(text)) + "]"
