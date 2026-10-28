"""A Twitch-first, lore-linked semantic guessing encounter."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone

from . import embeddings, narrator
from .runtime import db, lock, now, publish

_start_lock = threading.Lock()
_WORD = re.compile(r"!guess\s+([a-zA-Z]{2,24})\s*$", re.I)


def init_schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS signal_rounds(
      id TEXT PRIMARY KEY, chapter_id TEXT NOT NULL, lore_id TEXT, boss_name TEXT NOT NULL,
      boss_epithet TEXT NOT NULL, relic_name TEXT NOT NULL, target_word TEXT NOT NULL,
      target_vector TEXT NOT NULL, clue TEXT NOT NULL, hints TEXT NOT NULL,
      best_similarity REAL NOT NULL DEFAULT 0, best_guess TEXT, best_viewer TEXT,
      status TEXT NOT NULL, mode TEXT NOT NULL, started_at TEXT NOT NULL, ends_at TEXT NOT NULL,
      closed_at TEXT, result TEXT, solved_by TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS signal_guesses(
      event_id TEXT PRIMARY KEY, round_id TEXT NOT NULL, viewer_id TEXT NOT NULL,
      viewer TEXT NOT NULL, guess TEXT NOT NULL, similarity REAL NOT NULL,
      heat INTEGER NOT NULL, at TEXT NOT NULL, mode TEXT NOT NULL,
      UNIQUE(round_id,viewer_id,guess))""")


def _epoch(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0


def _band(heat):
    return "BLAZING" if heat >= 88 else "HOT" if heat >= 70 else "WARM" if heat >= 48 else "COOL" if heat >= 28 else "COLD"


def _close(c, row, solved=False, viewer=None):
    result = (f"{viewer or 'Chat'} found the signal: {row['target_word'].upper()}!" if solved
              else f"The signal faded. The hidden word was {row['target_word'].upper()}.")
    c.execute("UPDATE signal_rounds SET status='RESOLVED',closed_at=?,result=?,solved_by=? WHERE id=?",
              (now(), result, viewer if solved else None, row["id"]))
    if row["mode"] == "live":
        participants = c.execute("SELECT viewer_id,viewer FROM signal_guesses WHERE round_id=? GROUP BY viewer_id,viewer", (row["id"],)).fetchall()
        stream = c.execute("SELECT value FROM settings WHERE key='current_stream_id'").fetchone()
        stream_id = stream["value"] if stream else datetime.now(timezone.utc).date().isoformat()
        for person in participants:
            award = 35 if solved and person["viewer"] == viewer else 10
            existing = c.execute("SELECT xp,streak,last_stream FROM game_viewers WHERE viewer_id=?", (person["viewer_id"],)).fetchone()
            if existing:
                streak = int(existing["streak"]) + (1 if existing["last_stream"] != stream_id else 0)
                c.execute("UPDATE game_viewers SET display_name=?,xp=xp+?,streak=?,last_stream=?,last_seen=? WHERE viewer_id=?",
                          (person["viewer"], award, streak, stream_id, now(), person["viewer_id"]))
            else:
                c.execute("INSERT INTO game_viewers(viewer_id,display_name,xp,streak,last_stream,last_seen) VALUES(?,?,?,1,?,?)",
                          (person["viewer_id"], person["viewer"], award, stream_id, now()))
        c.execute("UPDATE game_viewers SET title=CASE WHEN xp>=500 THEN 'Lead Investigator' WHEN xp>=200 THEN 'Field Researcher' WHEN xp>=50 THEN 'Signal Analyst' ELSE 'Research Apprentice' END WHERE viewer_id IN (SELECT viewer_id FROM signal_guesses WHERE round_id=?)", (row["id"],))
        if solved:
            from .relics import add_resonance
            link = c.execute("SELECT relic_id FROM chapter_relics WHERE chapter_id=?", (row["chapter_id"],)).fetchone()
            if link:
                add_resonance(c, link["relic_id"], 15)
    return result


def start(chapter_id=None, mode="live"):
    if mode not in ("live", "simulation"):
        return None, "Unknown signal-hunt mode."
    if not _start_lock.acquire(blocking=False):
        return None, "The Keeper is preparing this signal hunt. Try again in a moment."
    try:
        with lock, db() as c:
            from .game import state as legacy_game_state
            legacy_game_state(c)
            active = c.execute("SELECT id FROM signal_rounds WHERE status='ACTIVE' LIMIT 1").fetchone()
            if active:
                return None, "A lore boss signal hunt is already active."
            chapter = c.execute("SELECT * FROM chapters ORDER BY ordinal DESC LIMIT 1").fetchone()
            if not chapter or chapter["status"] != "RESOLVED":
                return None, "Resolve the field-study vote before opening its lore boss encounter."
            if chapter_id and chapter["id"] != chapter_id:
                return None, "That chapter is no longer the latest chapter."
            existing = c.execute("SELECT id FROM signal_rounds WHERE chapter_id=? AND mode=? LIMIT 1", (chapter["id"], mode)).fetchone()
            if existing:
                return None, "This chapter already had its signal hunt. Open the next field study to continue."
            lore_id = chapter["seed_lore_id"]
            lore = c.execute("SELECT summary,status FROM lore WHERE id=?", (lore_id,)).fetchone() if lore_id else None
            if not lore or lore["status"] != "ACTIVE":
                return None, "The source observation must be approved before a lore boss can be created."
            enrichment = c.execute("SELECT boss_name,boss_epithet FROM lore_enrichments WHERE lore_id=?", (lore_id,)).fetchone()
            relic_link = c.execute("SELECT relic_id FROM chapter_relics WHERE chapter_id=?", (chapter["id"],)).fetchone()
            relic = c.execute("SELECT name FROM relics WHERE id=?", (relic_link["relic_id"],)).fetchone() if relic_link else None
            context = {"chapter_title": chapter["title"], "stream_context": {}}
            boss = enrichment["boss_name"] if enrichment else "The Unresolved Hypothesis"
            epithet = enrichment["boss_epithet"] if enrichment else "A fictional anomaly shaped by approved community lore"
            summary = lore["summary"]
            relic_name = relic["name"] if relic else "Community Signal Charm"
        puzzle = narrator.signal_puzzle(summary, relic_name, boss, context)
        try:
            vector = embeddings.embed_semantic(puzzle["target_word"], timeout_seconds=4)
        except Exception as exc:
            return None, f"Semantic embeddings are unavailable: {str(exc)[:180]}. Check LM Studio is running, its embedding model is loaded, and EMBEDDING_DIMENSIONS matches (768)."
        started = now()
        duration = 7 if mode == "simulation" else max(45, min(180, int(__import__('os').environ.get("LOREKEEPER_SIGNAL_SECONDS", "90"))))
        if mode == "live" and chapter["winning_choice"] == "B":
            duration = min(180, duration + 20)
        ends = datetime.fromtimestamp(time.time() + duration, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        with lock, db() as c:
            current = c.execute("SELECT id,status FROM chapters ORDER BY ordinal DESC LIMIT 1").fetchone()
            if not current or current["id"] != chapter["id"] or current["status"] != "RESOLVED":
                return None, "The field study changed while the Keeper prepared the puzzle. Start again."
            rid = str(uuid.uuid4())
            c.execute("INSERT INTO signal_rounds(id,chapter_id,lore_id,boss_name,boss_epithet,relic_name,target_word,target_vector,clue,hints,status,mode,started_at,ends_at) VALUES(?,?,?,?,?,?,?,?,?,?,'ACTIVE',?,?,?)",
                      (rid, chapter["id"], lore_id, boss, epithet, relic_name, puzzle["target_word"], json.dumps(vector), puzzle["clue"], json.dumps(puzzle["hints"], ensure_ascii=False), mode, started, ends))
            result = state(c)
        publish({"kind": "game", "game": result})
        return result, ""
    finally:
        _start_lock.release()


def state(c=None):
    if c is None:
        with lock, db() as connection:
            return state(connection)
    row = c.execute("SELECT r.*,ch.winning_choice FROM signal_rounds r LEFT JOIN chapters ch ON ch.id=r.chapter_id ORDER BY r.started_at DESC LIMIT 1").fetchone()
    if not row:
        return {"status": "IDLE", "feed": [], "leaderboard": []}
    if row["status"] == "ACTIVE" and _epoch(row["ends_at"]) <= time.time():
        _close(c, row)
        row = c.execute("SELECT r.*,ch.winning_choice FROM signal_rounds r LEFT JOIN chapters ch ON ch.id=r.chapter_id WHERE r.id=?", (row["id"],)).fetchone()
    elapsed = max(0, int(time.time() - _epoch(row["started_at"])))
    seconds = max(0, int(_epoch(row["ends_at"]) - time.time())) if row["status"] == "ACTIVE" else 0
    hints = json.loads(row["hints"] or "[]")
    stage = min(3, max(0, int(elapsed / max(1, (_epoch(row["ends_at"]) - _epoch(row["started_at"])) / 4))))
    if row["winning_choice"] == "A":
        stage = max(1, stage)
    count = c.execute("SELECT count(*) n FROM signal_guesses WHERE round_id=?", (row["id"],)).fetchone()["n"]
    live_completed = c.execute("SELECT 1 FROM signal_rounds WHERE chapter_id=? AND mode='live' AND status='RESOLVED' LIMIT 1", (row["chapter_id"],)).fetchone()
    feed = c.execute("SELECT viewer,guess,heat,at FROM signal_guesses WHERE round_id=? ORDER BY at DESC LIMIT 8", (row["id"],)).fetchall()
    leaderboard = c.execute("SELECT display_name,xp,title,streak FROM game_viewers ORDER BY xp DESC,last_seen DESC LIMIT 8").fetchall()
    similarity = float(row["best_similarity"] or 0)
    # Heat is a friendly, bounded presentation score; this is not a global word rank.
    heat = int(round(max(0, min(99, (similarity - 0.10) / 0.62 * 100)))) if row["best_guess"] else 0
    solved = row["status"] == "RESOLVED" and bool(row["solved_by"])
    if solved:
        heat = 100
    result = {"id": row["id"], "chapter_id": row["chapter_id"], "status": row["status"], "mode": row["mode"],
              "boss_name": row["boss_name"], "boss_epithet": row["boss_epithet"], "relic_name": row["relic_name"],
              "hp": 100 - heat, "max_hp": 100, "seconds_left": seconds, "actions": int(count), "unique_guesses": int(count),
              "clue": row["clue"], "hint": hints[max(0, stage - 1)] if stage and hints else None,
              "hint_stage": stage, "best_guess": row["best_guess"], "best_viewer": row["best_viewer"], "best_heat": heat,
              "best_band": _band(heat), "result": row["result"], "live_completed_for_chapter": bool(live_completed), "feed": [dict(x) for x in feed],
              "leaderboard": [dict(x) for x in leaderboard]}
    if row["status"] == "RESOLVED":
        result["target_word"] = row["target_word"]
        result["solved"] = solved
    return result


def apply_guess(event):
    if event.get("source") not in ("twitch_eventsub", "game_simulation") or event.get("event_type") != "chat.message":
        return None
    payload = event.get("payload") or {}
    match = _WORD.fullmatch(str(payload.get("text", "")).strip())
    if not match:
        return None
    guess = match.group(1).casefold()
    viewer_id = str(event.get("viewer_id") or "")
    if not viewer_id:
        return None
    viewer = str(payload.get("display_name") or "Viewer")[:50]
    event_id = str(event.get("event_id") or uuid.uuid4())
    with lock, db() as c:
        if c.execute("SELECT 1 FROM forgotten WHERE viewer_id=?", (viewer_id,)).fetchone():
            return None
        row = c.execute("SELECT * FROM signal_rounds WHERE status='ACTIVE' ORDER BY started_at DESC LIMIT 1").fetchone()
        if not row or ((row["mode"] == "live") != (event.get("source") == "twitch_eventsub")) or _epoch(row["ends_at"]) <= time.time():
            return None
        if c.execute("SELECT 1 FROM signal_guesses WHERE event_id=?", (event_id,)).fetchone():
            return state(c)
        if c.execute("SELECT 1 FROM signal_guesses WHERE round_id=? AND viewer_id=? AND guess=?", (row["id"], viewer_id, guess)).fetchone():
            return None
        prior = c.execute("SELECT at FROM signal_guesses WHERE round_id=? AND viewer_id=? ORDER BY at DESC LIMIT 1", (row["id"], viewer_id)).fetchone()
        if prior and _epoch(now()) - _epoch(prior["at"]) < 2:
            return None
        if int(c.execute("SELECT count(*) n FROM signal_guesses WHERE round_id=? AND viewer_id=?", (row["id"], viewer_id)).fetchone()["n"]) >= 30:
            return None
        target = json.loads(row["target_vector"])
    try:
        vector = embeddings.embed_semantic(guess, timeout_seconds=2)
    except Exception as exc:
        print(f"Could not score semantic guess: {str(exc)[:160]}", flush=True)
        return None
    similarity = sum(float(a) * float(b) for a, b in zip(vector, target))
    similarity = max(-1.0, min(1.0, similarity))
    if guess == row["target_word"].casefold():
        similarity = 1.0
    # Calibrated display band, not presented as an exact semantic rank.
    heat = 100 if similarity >= 0.999 else max(0, min(99, int(round((similarity - 0.10) / 0.62 * 100))))
    with lock, db() as c:
        current = c.execute("SELECT * FROM signal_rounds WHERE id=? AND status='ACTIVE'", (row["id"],)).fetchone()
        if not current or _epoch(current["ends_at"]) <= time.time():
            return state(c)
        at = now()
        prior = c.execute("SELECT at FROM signal_guesses WHERE round_id=? AND viewer_id=? ORDER BY at DESC LIMIT 1", (row["id"], viewer_id)).fetchone()
        count = int(c.execute("SELECT count(*) n FROM signal_guesses WHERE round_id=? AND viewer_id=?", (row["id"], viewer_id)).fetchone()["n"])
        if (prior and _epoch(at) - _epoch(prior["at"]) < 2) or count >= 30:
            return None
        try:
            c.execute("INSERT INTO signal_guesses(event_id,round_id,viewer_id,viewer,guess,similarity,heat,at,mode) VALUES(?,?,?,?,?,?,?,?,?)",
                      (event_id, row["id"], viewer_id, viewer, guess, similarity, heat, at, row["mode"]))
        except Exception:
            return None
        if not current["best_guess"] or similarity > float(current["best_similarity"] or 0):
            c.execute("UPDATE signal_rounds SET best_similarity=?,best_guess=?,best_viewer=? WHERE id=?", (similarity, guess, viewer, row["id"]))
        if guess == row["target_word"].casefold():
            updated = c.execute("SELECT * FROM signal_rounds WHERE id=?", (row["id"],)).fetchone()
            _close(c, updated, True, viewer)
        result = state(c)
    publish({"kind": "game", "game": result})
    return result


def simulate():
    current, error = start(mode="simulation")
    if error:
        return False, error
    # Clearly marked synthetic activity; the puzzle's real embedding decides the heat.
    guesses = ["memory", "signal", "science", "charm", "stream", "story", "research", "idea"]
    def crowd():
        for index in range(200):
            name = f"ReplayResearcher{index + 1:03d}"
            event = {"event_id": "signal-sim:" + str(uuid.uuid4()), "event_type": "chat.message",
                     "source": "game_simulation", "occurred_at": now(), "viewer_id": "simulation:" + name,
                     "payload": {"display_name": name, "text": "!guess " + guesses[index % len(guesses)]}}
            apply_guess(event)
            time.sleep(.02)
    threading.Thread(target=crowd, daemon=True, name="lorekeeper-signal-replay").start()
    return True, "A clearly labelled synthetic signal-hunt preview is running."


def forget_viewer(viewer_id):
    with lock, db() as c:
        forgotten_names = {row["viewer"] for row in c.execute("SELECT DISTINCT viewer FROM signal_guesses WHERE viewer_id=?", (str(viewer_id),)).fetchall()}
        c.execute("DELETE FROM signal_guesses WHERE viewer_id=?", (str(viewer_id),))
        active = c.execute("SELECT * FROM signal_rounds WHERE status='ACTIVE' ORDER BY started_at DESC LIMIT 1").fetchone()
        if active:
            best = c.execute("SELECT guess,viewer,similarity FROM signal_guesses WHERE round_id=? ORDER BY similarity DESC LIMIT 1", (active["id"],)).fetchone()
            c.execute("UPDATE signal_rounds SET best_guess=?,best_viewer=?,best_similarity=? WHERE id=?",
                      (best["guess"], best["viewer"], best["similarity"], active["id"]) if best else (None, None, 0, active["id"]))
        for round_row in c.execute("SELECT id,solved_by FROM signal_rounds WHERE solved_by IS NOT NULL").fetchall():
            if round_row["solved_by"] in forgotten_names:
                c.execute("UPDATE signal_rounds SET solved_by='A community investigator',result=replace(result,?,'A community investigator') WHERE id=?", (round_row["solved_by"], round_row["id"]))


def forget_lore(c, lore_id):
    rounds = c.execute("SELECT id FROM signal_rounds WHERE lore_id=?", (str(lore_id),)).fetchall()
    for row in rounds:
        c.execute("DELETE FROM signal_guesses WHERE round_id=?", (row["id"],))
        c.execute("DELETE FROM signal_rounds WHERE id=?", (row["id"],))
