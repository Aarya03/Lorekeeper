"""Inspect rank-calibrated semantic heat using the configured local embedder."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lorekeeper import embeddings, heat


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Compare target and guess heat against the bundled reference vocabulary.")
    parser.add_argument("target", help="The hidden target word")
    parser.add_argument("guesses", nargs="+", help="One or more candidate guesses")
    args = parser.parse_args(argv)
    words = [args.target, *args.guesses]
    if any(not re.fullmatch(r"[A-Za-z]{2,24}", word) for word in words):
        parser.error("target and guesses must each be one 2-24 letter English word")
    try:
        index = heat.reference_index()
        target = embeddings.embed_semantic(args.target, timeout_seconds=20)
        distribution = heat.reference_distribution(target, index)
        print(f"target={args.target.casefold()}  reference_words={len(index.words)}  model={embeddings.semantic_cache_identity().get('model', 'demo_lexicon')}")
        print(f"{'GUESS':<20} {'COSINE':>8} {'PERCENTILE':>12} {'HEAT':>6} {'RANK':>14}")
        for guess in args.guesses:
            vector = embeddings.embed_semantic(guess, timeout_seconds=20)
            cosine = heat.cosine(vector, target)
            score = heat.score_similarity(cosine, distribution, exact=guess.casefold() == args.target.casefold())
            print(f"{guess.casefold():<20} {cosine:>8.4f} {score['percentile']:>11.2f}% {score['heat']:>6} #{score['rank']:<13,}")
    except Exception as exc:
        print(f"Heat check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
