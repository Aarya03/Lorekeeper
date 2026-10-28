"""Curated, deterministic community relic generation and campaign progression.

Relics turn streamer-approved community memories into collectible objects. The
generator composes reviewed stream archetypes, artifact forms, names, abilities,
and visual palettes; it never executes or trusts viewer supplied instructions.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid

from .runtime import db, now
from .memory import summarize_lore


STREAM_STYLES = {
    "gaming": "Battle & Games", "tabletop": "Tabletop & RPG", "art": "Art & Making",
    "music": "Music & Performance", "coding": "Coding & Making", "cooking": "Cooking & Food",
    "fitness": "Fitness & Movement", "science": "Science & Learning", "travel": "IRL & Travel",
    "sports": "Sports & Competition", "horror": "Horror & Mystery", "cozy": "Cozy & Community",
    "retro": "Retro & Collecting", "craft": "Craft & Collecting", "variety": "Community Variety",
}

PALETTE_VARIANTS = {
    "gaming": [["#a98cff", "#ff9b72"], ["#5fa8ff", "#ff6487"], ["#5ed6c2", "#b78cff"], ["#ff6d78", "#ffd36f"]],
    "tabletop": [["#c58a63", "#e8c66d"], ["#6179bd", "#d6b5ff"], ["#4f9c7d", "#e8d39a"]],
    "art": [["#f07fa5", "#ffca78"], ["#6bbfe0", "#b68cff"], ["#e77c62", "#e7e3a0"]],
    "music": [["#a078f0", "#56d6cc"], ["#ff6ca8", "#7e9bff"], ["#f3aa56", "#e96e8b"]],
    "coding": [["#59c7d8", "#a98cff"], ["#63dfb0", "#618bff"], ["#ff9b72", "#e676d0"]],
    "cooking": [["#ff8d61", "#ffd36f"], ["#d95655", "#ffb26b"], ["#8cbe73", "#f2d887"]],
    "fitness": [["#ff6c78", "#ffbd6d"], ["#4cb9c9", "#ffc765"], ["#a980eb", "#fa829b"]],
    "science": [["#55cbb0", "#77a8ff"], ["#8b7cea", "#61d3da"], ["#f2b55f", "#76b9a1"]],
    "travel": [["#55cbb0", "#e7ca71"], ["#5d9ee8", "#ff9b72"], ["#77ad76", "#e8d6a2"]],
    "sports": [["#65b9ff", "#ff9b72"], ["#e96572", "#f5c65f"], ["#61c5a5", "#6c8fe0"]],
    "horror": [["#bb83ee", "#ff6d88"], ["#7183d8", "#a7eddb"], ["#d9699c", "#f1ba70"]],
    "cozy": [["#f19ac0", "#f2c879"], ["#91bd89", "#edb6b1"], ["#83b8d0", "#f3d6a2"]],
    "retro": [["#ff7eb6", "#78e0c4"], ["#ffb348", "#e56b78"], ["#81a3ff", "#e58eda"]],
    "craft": [["#e0a56e", "#a98cff"], ["#62b5a4", "#ec8e9d"], ["#d27752", "#eac976"]],
    "variety": [["#a98cff", "#65d6bd"], ["#f28db3", "#86a7ed"], ["#f1b35f", "#70c6b7"], ["#7e99e8", "#de83ba"]],
}

SIGIL_VARIANTS = {
    "gaming": ["crystal", "medal", "gear", "star", "eye"], "tabletop": ["die", "compass", "eye", "crystal"],
    "art": ["palette", "star", "crystal", "gear"], "music": ["soundwave", "cassette", "star", "orbit"],
    "coding": ["circuit", "gear", "crystal", "orbit"], "cooking": ["flame", "lantern", "star", "palette"],
    "fitness": ["medal", "star", "orbit", "crystal"], "science": ["microscope", "atom", "flask", "molecule", "telescope"],
    "travel": ["compass", "lantern", "star", "orbit"], "sports": ["medal", "star", "die", "orbit"],
    "horror": ["eye", "lantern", "crystal", "orbit"], "cozy": ["lantern", "star", "palette", "orbit"],
    "retro": ["cassette", "crystal", "gear", "star"], "craft": ["gear", "palette", "crystal", "die"],
    "variety": ["star", "orbit", "crystal", "lantern", "compass"],
}

KEYWORDS = {
    "tabletop": ("dnd", "d&d", "dice", "campaign", "dungeon", "critical hit", "wizard", "bard", "rpg"),
    "art": ("draw", "drawing", "paint", "painting", "sketch", "illustrat", "canvas", "commission", "sculpt"),
    "music": ("song", "music", "sing", "guitar", "piano", "drum", "concert", "album", "beat", "karaoke"),
    "coding": ("code", "coding", "debug", "program", "compile", "software", "keyboard", "bug", "build"),
    "cooking": ("cook", "recipe", "bake", "kitchen", "food", "cake", "bread", "coffee", "meal"),
    "fitness": ("workout", "gym", "run", "running", "lift", "fitness", "yoga", "race", "exercise"),
    "science": ("science", "experiment", "space", "planet", "chemistry", "physics", "microscope", "research", "learn"),
    "travel": ("travel", "trip", "hike", "hiking", "camp", "road trip", "airport", "mountain", "city tour"),
    "sports": ("football", "soccer", "basketball", "baseball", "tournament", "goal", "team", "champion", "overtime", "touchdown"),
    "horror": ("horror", "haunted", "ghost", "scary", "scream", "monster", "nightmare", "spooky", "survival"),
    "cozy": ("cozy", "cosy", "relax", "tea", "rain", "farm", "garden", "cute", "wholesome", "comfort"),
    "retro": ("retro", "pixel", "arcade", "cassette", "vinyl", "old school", "classic game", "nostalgia"),
    "craft": ("craft", "sew", "knit", "crochet", "lego", "miniature", "model", "collect", "woodwork"),
    "gaming": ("game", "gaming", "gameplay", "match", "boss", "clutch", "rank", "win", "lose", "stream", "raid", "speedrun", "level", "quest"),
}

FORMS = {
    "gaming": ["Charm", "Boss Sigil", "Loot Key", "Respawn Token", "Victory Medal", "Glitch Crystal", "Pocket Portal", "Lucky Loadout"],
    "tabletop": ["Fate Die", "Dragon Scale", "Quest Compass", "Spellbound Mini", "Dungeon Key", "Critical Coin"],
    "art": ["Muse Palette", "Golden Brush", "Color Prism", "Sketchbook Sprite", "Inkstone", "Idea Lantern"],
    "music": ["Echo Pick", "Encore Bell", "Harmony Orb", "Bassline Cassette", "Tempo Talisman", "Stage Light"],
    "coding": ["Debug Key", "Patch Crystal", "Compiler Coin", "Pixel Circuit", "Rollback Dial", "Bugcatcher Lens"],
    "cooking": ["Recipe Locket", "Golden Ladle", "Sourdough Sprite", "Spice Comet", "Midnight Kettle", "Comfort Cauldron"],
    "fitness": ["Second-Wind Medal", "Stamina Stone", "Victory Ribbon", "Momentum Band", "Summit Compass", "Endurance Flame"],
    "science": ["Evidence Charm", "Field-Note Locket", "Signal Prism", "Specimen Seal", "Lab Pass", "Data Crystal"],
    "travel": ["Wayfinder Compass", "Trail Lantern", "Postcard Portal", "Summit Bell", "Lucky Map", "Roadside Star"],
    "sports": ["Overtime Medal", "Momentum Ball", "Rally Pennant", "Home-Run Comet", "Victory Whistle", "Clutch Crest"],
    "horror": ["Bravery Lantern", "Ghostglass Eye", "Night Key", "Fear-Eater Charm", "Midnight Mirror", "Safehouse Sigil"],
    "cozy": ["Hearthstone", "Rainy-Day Teacup", "Comfort Moth", "Warmth Lantern", "Friendship Quilt", "Little Lucky Star"],
    "retro": ["Rewind Cassette", "Arcade Token", "Pixel Crown", "Save-Point Crystal", "8-Bit Heart", "Memory Cartridge"],
    "craft": ["Maker's Gear", "Threadkeeper Bobbin", "Tiny Masterpiece", "Collector's Cabinet Key", "Copper Thimble", "Builder's Prism"],
    "variety": ["Community Star", "Storyglass Locket", "Lucky Comet", "Archive Key", "Friendship Charm", "Wonder Capsule"],
}

TIER_NAMES = [(0, "Observation"), (30, "Hypothesis"), (100, "Experiment"), (250, "Replication"), (600, "Breakthrough")]
TIER_EFFECTS = ["a faint signal", "a visible trace", "a layered spectrum", "a stable pattern", "a mapped constellation"]


def init_schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS relics (
        id TEXT PRIMARY KEY, lore_id TEXT UNIQUE, name TEXT NOT NULL,
        stream_type TEXT NOT NULL, stream_label TEXT NOT NULL, form TEXT NOT NULL,
        power_name TEXT NOT NULL, power_text TEXT NOT NULL, palette TEXT NOT NULL,
        visual TEXT NOT NULL, resonance INTEGER NOT NULL DEFAULT 0,
        charge INTEGER NOT NULL DEFAULT 3, max_charge INTEGER NOT NULL DEFAULT 3,
        wards INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS chapter_relics (
        chapter_id TEXT PRIMARY KEY, relic_id TEXT NOT NULL)""")
    # Upgrade existing approved lore into relics without requiring a fresh event.
    for memory in c.execute("SELECT id,summary FROM lore WHERE status='ACTIVE' ORDER BY created_at").fetchall():
        ensure_for_lore(c, memory["id"], memory["summary"])
    # Refresh only unplayed relics once so the clearer memory-based naming also
    # applies to collections created by earlier versions. Played relics retain identity.
    version = c.execute("SELECT value FROM settings WHERE key='relic_personalization_version'").fetchone()
    if not version or version["value"] != "6":
        # Refresh artifacts into the science-first collection without resetting
        # progress; include seed relics and preserve local narrator text where available.
        existing = c.execute("SELECT r.id,r.lore_id,l.summary,e.relic_title,e.power_name AS enriched_power_name,e.power_text AS enriched_power_text FROM relics r LEFT JOIN lore l ON l.id=r.lore_id LEFT JOIN lore_enrichments e ON e.lore_id=r.lore_id").fetchall()
        for relic in existing:
            clean_summary = summarize_lore(relic["summary"] or "Community field observation")
            item = generate(clean_summary, relic["lore_id"], configured_stream_type(c, clean_summary))
            if relic["relic_title"]:
                item["name"] = relic["relic_title"]
                item["power_name"] = relic["enriched_power_name"]
                item["power_text"] = relic["enriched_power_text"]
            c.execute("UPDATE relics SET name=?,stream_type=?,stream_label=?,form=?,power_name=?,power_text=?,palette=?,visual=?,updated_at=? WHERE id=?",
                      (item["name"], item["stream_type"], item["stream_label"], item["form"], item["power_name"], item["power_text"], json.dumps(item["palette"]), json.dumps(item["visual"]), now(), relic["id"]))
        c.execute("INSERT INTO settings(key,value) VALUES('relic_personalization_version','6') ON CONFLICT(key) DO UPDATE SET value=excluded.value")


def _stable_index(text: str, size: int, salt: str = "") -> int:
    digest = hashlib.sha256((salt + "|" + text.casefold()).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % size


def _classify(summary: str) -> str:
    text = summary.casefold()
    for kind, words in KEYWORDS.items():
        if any(word in text for word in words):
            return kind
    return "variety"


def _memory_anchor(summary: str) -> str:
    """Extract a neutral fallback label; the narrator supplies contextual names."""
    summary = summarize_lore(summary)
    words = re.findall(r"[\w'-]+", summary, flags=re.UNICODE)
    stop = {"chat", "keeps", "returning", "remembers", "community", "shared", "moment", "story", "about", "the", "a", "an", "and", "for", "from", "with", "that", "this"}
    useful = [word for word in words if len(word.strip("'-")) > 2 and word.casefold() not in stop]
    if not useful:
        return "Community Moment"
    # Keep the recognizable subject/event, not the conversational filler.
    return " ".join(word.capitalize() for word in useful[:3])


def _power(kind: str, summary: str):
    options = {
        "gaming": [("CLUTCH CHARGE", "Turn one close call into a bold new lead."), ("LOOT LUCK", "Reveal a hidden cache on the next expedition."), ("RESPAWN SPARK", "Recover one lost ward after a daring choice.")],
        "tabletop": [("FATE REROLL", "Give the party one more chance at a risky roll."), ("QUEST SENSE", "Reveal a hidden route in the next encounter."), ("CRITICAL SPARK", "Turn a close vote into a legendary success.")],
        "art": [("MUSE'S SPARK", "Transform the next scene with a burst of color."), ("IDEA BLOOM", "Reveal a bonus detail for the community creation."), ("GOLDEN STROKE", "Turn the next chapter's clue into a vivid scene.")],
        "music": [("ENCORE PULSE", "Bring a quiet moment back for one more refrain."), ("HARMONY WAVE", "Join the community's voices into a shared boost."), ("TEMPO SHIFT", "Change the mood of the next encounter.")],
        "coding": [("DEBUG PULSE", "Expose a hidden flaw before it becomes a problem."), ("ROLLBACK", "Undo one setback in the shared adventure."), ("PATCH BURST", "Upgrade the next tool the Keeper discovers.")],
        "cooking": [("COMFORT FEAST", "Restore warmth after a difficult encounter."), ("SPICE BURST", "Add a daring twist to the next scene."), ("SECRET RECIPE", "Reveal a bonus ingredient in the archive.")],
        "fitness": [("SECOND WIND", "Recover momentum when the expedition slows."), ("SUMMIT PUSH", "Turn a challenge into forward progress."), ("STAMINA SURGE", "Protect the party through one tough encounter.")],
        "science": [("HYPOTHESIS LENS", "Deploy this charm to reveal the first puzzle hint as the hunt opens."), ("OBSERVATION TOKEN", "Preserve this sample to add 20 seconds to the community signal hunt."), ("SIGNAL KEEPER", "A shared semantic hunt turns approved field notes into a solvable clue.")],
        "travel": [("SAFE PASSAGE", "Find the safer route through the next scene."), ("WAYFINDER", "Reveal a shortcut on the shared map."), ("TRAILBLAZE", "Turn a detour into a new destination.")],
        "sports": [("MOMENTUM SWING", "Shift the next close call in the community's favor."), ("OVERTIME", "Keep the encounter alive for one more beat."), ("HOME ADVANTAGE", "Protect the archive from the next setback.")],
        "horror": [("FEAR WARD", "Hold the darkness back for one encounter."), ("GHOSTLIGHT", "Reveal what is hiding in the next scene."), ("NIGHT KEY", "Open a forbidden door without losing a ward.")],
        "cozy": [("HEARTH GLOW", "Restore one ward and warm the next chapter."), ("FRIENDSHIP QUILT", "Turn a setback into a shared comfort."), ("TEA TIME", "Pause the peril and recover the Keeper's resolve.")],
        "retro": [("REWIND", "Replay a close call and choose a new direction."), ("SAVE POINT", "Protect the next chapter from one setback."), ("PIXEL POWER", "Upgrade a familiar memory into a new lead.")],
        "craft": [("MAKER'S TOUCH", "Repair one broken ward in the archive."), ("MASTERWORK", "Transform a small clue into a lasting artifact."), ("COLLECTOR'S EYE", "Reveal a rare detail in the next chapter.")],
        "variety": [("LUCKY BREAK", "Turn the next unexpected moment into an opportunity."), ("COMMUNITY SPARK", "Combine chat's energy into a shared boost."), ("ARCHIVE KEY", "Unlock a new path in the living story.")],
    }
    return options[kind][_stable_index(summary, len(options[kind]), "power")]


def generate(summary: str, lore_id: str | None = None, stream_type: str | None = None) -> dict:
    """Build a reproducible, stream-aware relic from approved lore.

    15 archetypes x curated form/name/power variations and palette/visual
    combinations create thousands of stable, reviewable collectible variants.
    """
    summary = summarize_lore(summary or "A story the community made together")
    requested = (stream_type or "auto").strip().lower()
    kind = requested if requested in STREAM_STYLES else _classify(summary)
    label = STREAM_STYLES[kind]
    palette = PALETTE_VARIANTS[kind][_stable_index(summary, len(PALETTE_VARIANTS[kind]), "palette")]
    sigil = SIGIL_VARIANTS[kind][_stable_index(summary, len(SIGIL_VARIANTS[kind]), "sigil")]
    anchor = _memory_anchor(summary)
    form = FORMS[kind][_stable_index(summary, len(FORMS[kind]), "form")]
    suffixes = {"gaming": ["Charm", "Trophy", "Checkpoint Token", "Memory Badge"], "tabletop": ["Quest Token", "Party Keepsake", "Fate Charm"], "art": ["Muse Charm", "Color Token", "Artist's Keepsake"], "music": ["Encore Token", "Harmony Charm", "Stage Keepsake"], "coding": ["Patch Token", "Debug Charm", "Builder's Keepsake"], "cooking": ["Recipe Charm", "Spice Token", "Kitchen Keepsake"], "fitness": ["Momentum Charm", "Summit Token", "Victory Keepsake"], "science": ["Evidence Charm", "Signal Charm", "Specimen Token"], "travel": ["Wayfinder Token", "Journey Charm", "Postcard Keepsake"], "sports": ["Victory Token", "Rally Charm", "Team Keepsake"], "horror": ["Bravery Charm", "Ghostlight Token", "Night Keepsake"], "cozy": ["Hearth Charm", "Comfort Token", "Friendship Keepsake"], "retro": ["Rewind Token", "Arcade Charm", "Pixel Keepsake"], "craft": ["Maker's Charm", "Masterwork Token", "Collector's Keepsake"], "variety": ["Community Charm", "Memory Token", "Story Keepsake"]}
    suffix = suffixes[kind][_stable_index(summary, len(suffixes[kind]), "title")]
    name = f"{anchor} {suffix}"
    power_name, power_text = _power(kind, summary)
    visual = {
        "sigil": sigil,
        "shape": ["reliquary", "medallion", "crystal", "compass", "keepsake"][_stable_index(summary, 5, "shape")],
        "pattern": ["orbit", "stardust", "engraving", "runes", "ribbons"][_stable_index(summary, 5, "pattern")],
        "aura": TIER_EFFECTS[0],
        "rarity": "COMMUNITY MADE",
    }
    return {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"lorekeeper:relic:{lore_id or summary}")),
            "lore_id": lore_id, "name": name, "stream_type": kind,
            "stream_label": label, "form": form, "power_name": power_name,
            "power_text": power_text, "palette": palette, "visual": visual,
            "resonance": 0, "stage": 0, "stage_name": "Spark", "charge": 3,
            "max_charge": 3, "wards": 0, "next_stage_at": 30}


def update_stream_context(category: str, title: str = "", tags=None):
    """Persist Twitch's latest broadcaster category and descriptive metadata."""
    values = {
        "stream_category": " ".join(str(category or "").split())[:120],
        "stream_title": " ".join(str(title or "").split())[:300],
        "stream_context_at": now(),
    }
    if tags is not None:
        values["stream_tags"] = json.dumps([str(tag)[:80] for tag in tags[:20]])
    with db() as c:
        for key, value in values.items():
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        result = stream_context(c)
    return result


def stream_context(c):
    rows = c.execute("SELECT key,value FROM settings WHERE key IN ('stream_category','stream_title','stream_tags')").fetchall()
    values = {row["key"]: row["value"] for row in rows}
    try: tags = json.loads(values.get("stream_tags", "[]"))
    except (TypeError, ValueError): tags = []
    category = values.get("stream_category", "")
    title = values.get("stream_title", "")
    kind = classify_stream_context(category, title, tags)
    source = "category" if category else "title and tags" if title or tags else "approved memory"
    return {"category": category, "title": title, "tags": tags, "source": source,
            "stream_type": kind, "stream_label": STREAM_STYLES[kind] if kind else "Auto-detecting from memory"}


def classify_stream_context(category: str = "", title: str = "", tags=None):
    category_key = re.sub(r"\s+", " ", str(category or "").casefold()).strip()
    aliases = {
        "just chatting": "variety", "talk shows & podcasts": "variety", "special events": "variety",
        "art": "art", "makers & crafting": "craft", "music": "music", "software and game development": "coding",
        "food & drink": "cooking", "science & technology": "science", "travel & outdoors": "travel",
        "sports": "sports", "asmr": "cozy", "pools, hot tubs, and beaches": "travel",
    }
    if category_key in aliases:
        if aliases[category_key] == "variety":
            detail = _classify(" ".join([title or "", *(tags or [])]))
            return detail if detail != "variety" else "variety"
        return aliases[category_key]
    text = " ".join([category or "", title or "", *(tags or [])])
    detected = _classify(text)
    if category_key:
        # Twitch's other category names are overwhelmingly game categories.
        return detected if detected != "variety" else "gaming"
    return detected if text.strip() else ""


def configured_stream_type(c, summary: str = ""):
    # This iteration is intentionally science-first. Twitch metadata still
    # informs the narrator, while the artifact/research fantasy stays cohesive.
    configured = os.environ.get("LOREKEEPER_EXPERIENCE", "science").strip().lower()
    if configured in STREAM_STYLES:
        return configured
    context = stream_context(c)
    if context["category"] or context["title"] or context["tags"]:
        return context["stream_type"] or "gaming"
    return _classify(summary) if summary else "variety"


def ensure_for_lore(c, lore_id: str, summary: str, stream_type: str | None = None):
    existing = c.execute("SELECT id FROM relics WHERE lore_id=?", (lore_id,)).fetchone()
    if existing:
        return get(c, existing["id"])
    item = generate(summary, lore_id, stream_type or configured_stream_type(c, summary))
    stamp = now()
    c.execute("INSERT INTO relics(id,lore_id,name,stream_type,stream_label,form,power_name,power_text,palette,visual,resonance,charge,max_charge,wards,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,0,3,3,0,?,?)",
              (item["id"], lore_id, item["name"], item["stream_type"], item["stream_label"], item["form"], item["power_name"], item["power_text"], json.dumps(item["palette"]), json.dumps(item["visual"]), stamp, stamp))
    return get(c, item["id"])


def retune_for_current_stream(c, relic_id: str, summary: str = ""):
    """Apply the configured experience style to a dormant relic before first use."""
    row = c.execute("SELECT * FROM relics WHERE id=?", (relic_id,)).fetchone()
    if not row or int(row["resonance"]) > 0:
        return get(c, relic_id)
    if c.execute("SELECT 1 FROM chapter_relics WHERE relic_id=? LIMIT 1", (relic_id,)).fetchone():
        return get(c, relic_id)
    source = c.execute("SELECT summary FROM lore WHERE id=?", (row["lore_id"],)).fetchone() if row["lore_id"] and not row["lore_id"].startswith("seed:") else None
    source_summary = (source["summary"] if source else summary) or "A story this community made together"
    stream_type = configured_stream_type(c, source_summary)
    if not stream_type or stream_type == row["stream_type"]:
        return get(c, relic_id)
    item = generate(source_summary, row["lore_id"], stream_type)
    enriched = c.execute("SELECT relic_title,power_name,power_text FROM lore_enrichments WHERE lore_id=?", (row["lore_id"],)).fetchone() if row["lore_id"] and not row["lore_id"].startswith("seed:") else None
    if enriched:
        item["name"], item["power_name"], item["power_text"] = enriched["relic_title"], enriched["power_name"], enriched["power_text"]
    c.execute("UPDATE relics SET name=?,stream_type=?,stream_label=?,form=?,power_name=?,power_text=?,palette=?,visual=?,updated_at=? WHERE id=?",
              (item["name"], item["stream_type"], item["stream_label"], item["form"], item["power_name"], item["power_text"], json.dumps(item["palette"]), json.dumps(item["visual"]), now(), relic_id))
    return get(c, relic_id)


def ensure_seed(c, summary: str):
    return ensure_for_lore(c, "seed:" + hashlib.sha256(summary.encode("utf-8")).hexdigest()[:24], summary)


def _tier(resonance: int):
    current = (0, "Spark")
    for threshold, name in TIER_NAMES:
        if resonance >= threshold:
            current = (threshold, name)
    higher = [threshold for threshold, _ in TIER_NAMES if threshold > resonance]
    return current[1], higher[0] if higher else None, TIER_EFFECTS[TIER_NAMES.index(current)]


def _stage_index(resonance: int):
    return max(index for index, (threshold, _) in enumerate(TIER_NAMES) if resonance >= threshold)


def get(c, relic_id: str):
    row = c.execute("SELECT r.*,l.summary AS source_memory FROM relics r LEFT JOIN lore l ON l.id=r.lore_id WHERE r.id=?", (relic_id,)).fetchone()
    return _public(row) if row else None


def _public(row):
    if not row:
        return None
    resonance = int(row["resonance"])
    stage, next_stage, aura = _tier(resonance)
    visual = json.loads(row["visual"])
    visual["aura"] = aura
    visual["rarity"] = stage.upper()
    stage_index = _stage_index(resonance)
    return {"id": row["id"], "lore_id": row["lore_id"], "source_memory": summarize_lore(row["source_memory"] or ""), "name": row["name"],
            "stream_type": row["stream_type"], "stream_label": row["stream_label"],
            "form": row["form"], "power_name": row["power_name"],
            "power_text": row["power_text"], "palette": json.loads(row["palette"]),
            "visual": visual, "resonance": resonance, "stage": stage_index,
            "stage_name": stage, "next_stage_at": next_stage,
            "charge": int(row["charge"]), "max_charge": int(row["max_charge"]),
            "wards": int(row["wards"]), "updated_at": row["updated_at"]}


def list_relics(c, limit=12):
    rows = c.execute("SELECT r.*,l.summary AS source_memory FROM relics r LEFT JOIN lore l ON l.id=r.lore_id ORDER BY r.resonance DESC,r.updated_at DESC LIMIT ?", (max(1, min(40, int(limit))),)).fetchall()
    return [_public(row) for row in rows]


def for_chapter(c, chapter_id: str):
    link = c.execute("SELECT relic_id FROM chapter_relics WHERE chapter_id=?", (chapter_id,)).fetchone()
    return get(c, link["relic_id"]) if link else None


def vote_choices(relic):
    """Return clue-hunt vote actions with visible, distinct encounter consequences."""
    if not relic:
        return (("DEPLOY THE CHARM", "Reveal the first hint as the hunt opens; the charm gains extra resonance."),
                ("PRESERVE THE SAMPLE", "Keep the first hint sealed, add 20 seconds to the hunt, and gain 1 ward."))
    if int(relic["charge"]) > 0:
        use = ("DEPLOY THE CHARM", "Reveal the first hint immediately; spend 1 stored charge for extra charm resonance.")
    else:
        use = ("FOCUS THE CLUE", "Reveal the first hint immediately; the charm still gains resonance.")
    if int(relic["wards"]) < 9:
        detail = "Keep the first hint sealed at the start, add 20 seconds to the hunt, and gain 1 ward."
    else:
        detail = "Keep the first hint sealed at the start and add 20 seconds; this charm already has 9 wards."
    protect = ("PRESERVE THE SAMPLE", detail)
    return use, protect


def add_resonance(c, relic_id: str, amount: int):
    if amount <= 0:
        return get(c, relic_id)
    row = c.execute("SELECT * FROM relics WHERE id=?", (relic_id,)).fetchone()
    if not row: return None
    resonance = int(row["resonance"]) + int(amount)
    evolved = max(0, _stage_index(resonance) - _stage_index(int(row["resonance"])))
    charge = min(int(row["max_charge"]), int(row["charge"]) + evolved)
    c.execute("UPDATE relics SET resonance=?,charge=?,updated_at=? WHERE id=?", (resonance, charge, now(), relic_id))
    return get(c, relic_id)


def resolve_encounter(c, relic_id: str, winning_choice: str, vote_count: int):
    row = c.execute("SELECT * FROM relics WHERE id=?", (relic_id,)).fetchone()
    if not row:
        return None, "The archive carried the clue forward, but no relic was bound to it."
    vote_count = max(0, int(vote_count))
    if vote_count == 0:
        return get(c, relic_id), f"No one voted this chapter, so the Keeper leaves {row['name']} untouched. Chat can shape its fate in the next encounter."
    resonance_gain = min(30, vote_count * 3)
    old_stage = _stage_index(int(row["resonance"]))
    new_resonance = int(row["resonance"]) + resonance_gain + (8 if winning_choice == "A" and int(row["charge"]) > 0 else 0)
    new_stage = _stage_index(new_resonance)
    charge = int(row["charge"]) - (1 if winning_choice == "A" and int(row["charge"]) > 0 else 0)
    charge = min(int(row["max_charge"]), charge + new_stage - old_stage)
    wards = min(9, int(row["wards"]) + (1 if winning_choice == "B" else 0))
    c.execute("UPDATE relics SET resonance=?,charge=?,wards=?,updated_at=? WHERE id=?", (new_resonance, charge, wards, now(), relic_id))
    evolved = new_stage > old_stage
    tier_name = TIER_NAMES[new_stage][1]
    if winning_choice == "A":
        if int(row["charge"]) > 0:
            result = f"Chat used {row['name']} and spent one charge. {row['power_name']} flares: {row['power_text']} The relic gains {resonance_gain + 8} resonance."
        else:
            result = f"The relic has no charge left, but chat's invocation still feeds its legend. It gains {resonance_gain} resonance."
    elif winning_choice == "B":
        ward_text = "gains a ward for a later encounter" if int(row["wards"]) < 9 else "is already carrying the maximum wards"
        result = f"Chat protected {row['name']}. The relic keeps its charges and {ward_text}, plus {resonance_gain} resonance."
    else:
        result = f"The vote split. The Keeper preserves the relic's charge and gains {resonance_gain} resonance from both paths."
    if evolved:
        result += f" It evolves into {tier_name} and restores {new_stage-old_stage} charge(s)."
    return get(c, relic_id), result
