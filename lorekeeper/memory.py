"""Generic, deterministic fallback formatting for streamer-approved lore.

The local narrator provides the contextual summary. This module deliberately
contains no streamer-, game-, person-, or topic-specific rules; it is only the
safe fallback used when model output is unavailable or invalid.
"""
from __future__ import annotations

import re


_FILLER = {
    "a", "an", "and", "are", "as", "at", "back", "be", "because", "been",
    "but", "by", "did", "do", "does", "for", "from", "had", "has", "have",
    "he", "her", "here", "hers", "him", "his", "i", "in", "into", "is",
    "it", "its", "just", "last", "me", "my", "of", "on", "once", "our",
    "ours", "she", "that", "the", "their", "them", "these", "they", "this",
    "those", "through", "to", "today", "twice", "was", "we", "were", "what",
    "when", "where", "which", "who", "with", "won", "would", "you", "your",
}
_SUMMARY_PREFIXES = ("chat ", "the community ", "viewers ", "the stream ", "a community memory ")


def _words(text: str, limit: int = 18) -> str:
    words = str(text or "").split()
    result = " ".join(words[:limit]).rstrip(" ,:;-.")
    return result + ("…" if len(words) > limit else "")


def summarize_lore(message: str) -> str:
    """Create a generic, short fallback clue without guessing its topic."""
    text = " ".join(str(message or "").split()).strip().rstrip(".!?…")
    if not text:
        return "A shared moment from the community."
    if text.casefold().startswith(_SUMMARY_PREFIXES):
        return _words(text, 18) + "."

    # Keep the fallback readable and avoid echoing handles, links, or common
    # conversational framing. Topic interpretation belongs to the narrator.
    text = re.sub(r"https?://\S+|@[\w]+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", " ", text)
    tokens = re.findall(r"[\w'-]+", text, flags=re.UNICODE)
    topic = [token for token in tokens if len(token.strip("'-")) > 2 and token.casefold() not in _FILLER]
    if not topic:
        return "A shared moment from the community."
    phrase = _words(" ".join(topic), 8)
    return f"Chat keeps returning to {phrase}."
