"""Small, configurable safety gate for public chat guesses."""
from __future__ import annotations

import os
import re
import unicodedata

WORD = re.compile(r"^[a-zA-Z]{2,24}$")
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
_DEFAULT_BLOCKED = {
    "arse", "asshole", "bastard", "bitch", "bollocks", "bullshit", "cock", "cocksucker",
    "crap", "cunt", "damn", "dick", "dumbass", "fag", "faggot", "fuck", "fucker",
    "fucking", "motherfucker", "nazi", "nigger", "nigga", "piss", "porn", "rape",
    "retard", "shit", "slut", "twat", "whore",
}


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii").casefold()
    text = re.sub(r"[^a-z0-9@$]", "", text).translate(_LEET)
    # Make character repetition variants comparable (e.g. shiiiit).
    return re.sub(r"(.)\1+", r"\1", text)


def blocked_terms() -> set[str]:
    configured = os.environ.get("LOREKEEPER_GUESS_BLOCKLIST", "")
    return {normalize(item) for item in _DEFAULT_BLOCKED} | {normalize(item) for item in configured.split(",") if normalize(item)}


def is_allowed_guess(value: str) -> tuple[bool, str]:
    """Accept only one ASCII word and reject normalized configured/default terms."""
    if not WORD.fullmatch(str(value)):
        return False, "Use one English word containing 2-24 letters."
    normalized = normalize(value)
    terms = blocked_terms()
    suffixes = ("s", "es", "ed", "er", "ers", "ing", "y")
    if normalized in terms or any(normalized == term + suffix for term in terms for suffix in suffixes):
        return False, "That word is blocked by the channel's chat-safety filter."
    return True, ""


def contains_blocked_text(value: str) -> bool:
    """Filter generated public clue text with the same normalized word rules."""
    words = re.findall(r"[\w@$]+", unicodedata.normalize("NFKD", str(value)).casefold())
    return any(not is_allowed_guess(word)[0] and WORD.fullmatch(word) for word in words)
