"""Persisted, deterministic live boss encounters driven by real Twitch chat."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone

from .runtime import db, lock, now, publish


def init_schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS game_rounds(
      id TEXT PRIMARY KEY, chapter_id TEXT NOT NULL, boss_name TEXT NOT NULL,
      boss_epithet TEXT NOT NULL, relic_name TEXT NOT NULL, hp INTEGER NOT NULL,
      max_hp INTEGER NOT NULL, charge INTEGER NOT NULL DEFAULT 0, guard INTEGER NOT NULL DEFAULT 0,
      boss_pressure INTEGER NOT NULL DEFAULT 0, hype REAL NOT NULL DEFAULT 1, status TEXT NOT NULL, mode TEXT NOT NULL,
      started_at TEXT NOT NULL, ends_at TEXT NOT NULL, closed_at TEXT, result TEXT)""")
    c.execute("SAVEPOINT game_pressure_migration")
    try:
        c.execute("ALTER TABLE game_rounds ADD COLUMN boss_pressure INTEGER NOT NULL DEFAULT 0")
        c.execute("RELEASE SAVEPOINT game_pressure_migration")
    except Exception:
        c.execute("ROLLBACK TO SAVEPOINT game_pressure_migration")
        c.execute("RELEASE SAVEPOINT game_pressure_migration")
    c.execute("""CREATE TABLE IF NOT EXISTS game_action_events(
      event_id TEXT PRIMARY KEY, round_id TEXT NOT NULL, viewer_id TEXT NOT NULL,
      viewer TEXT NOT NULL, action TEXT NOT NULL, action_second INTEGER NOT NULL,
      at TEXT NOT NULL, damage INTEGER NOT NULL DEFAULT 0, xp INTEGER NOT NULL DEFAULT 0,
      mode TEXT NOT NULL, UNIQUE(round_id,viewer_id,action_second))""")
    c.execute("""CREATE TABLE IF NOT EXISTS game_viewers(
      viewer_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, xp INTEGER NOT NULL DEFAULT 0,
      title TEXT NOT NULL DEFAULT 'Research Apprentice', streak INTEGER NOT NULL DEFAULT 0,
      last_stream TEXT NOT NULL DEFAULT '', last_seen TEXT NOT NULL)""")
    c.execute("UPDATE game_viewers SET title=CASE WHEN xp>=500 THEN 'Lead Investigator' WHEN xp>=200 THEN 'Field Researcher' WHEN xp>=50 THEN 'Signal Analyst' ELSE 'Research Apprentice' END")


def _parse(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0


def _profile_title(xp):
    return "Lead Investigator" if xp >= 500 else "Field Researcher" if xp >= 200 else "Signal Analyst" if xp >= 50 else "Research Apprentice"


def _close(c, row, result):
    c.execute("UPDATE game_rounds SET status='RESOLVED',closed_at=?,result=? WHERE id=?", (now(), result, row["id"]))
    link = c.execute("SELECT relic_id FROM chapter_relics WHERE chapter_id=?", (row["chapter_id"],)).fetchone() if row["mode"] == "live" else None
    if link:
        from .relics import add_resonance, get
        stored = c.execute("SELECT charge,max_charge,wards FROM relics WHERE id=?", (link["relic_id"],)).fetchone()
        remaining_charge=min(int(stored["max_charge"]),max(0,int(row["charge"])//10))
        earned_wards=min(9,max(int(stored["wards"]),int(row["guard"])//8))
        c.execute("UPDATE relics SET charge=?,wards=?,updated_at=? WHERE id=?", (remaining_charge,earned_wards,now(),link["relic_id"]))
        action_count = int(c.execute("SELECT count(*) n FROM game_action_events WHERE round_id=?", (row["id"],)).fetchone()["n"])
        gain = min(30, max(5, action_count // 3))
        evolved = add_resonance(c, link["relic_id"], gain)
        result += f" {evolved['name']} gains {gain} resonance and reaches {evolved['stage_name']}."
        c.execute("UPDATE game_rounds SET result=? WHERE id=?", (result, row["id"]))
    if row["mode"] == "live":
        participants = c.execute("SELECT viewer_id,display_name,sum(damage) damage FROM game_action_events WHERE round_id=? AND mode='live' GROUP BY viewer_id,display_name", (row["id"],)).fetchall()
        stream = c.execute("SELECT value FROM settings WHERE key='current_stream_id'").fetchone()
        stream_id = stream["value"] if stream else datetime.now(timezone.utc).date().isoformat()
        for viewer in participants:
            award = 15 + min(30, int(viewer["damage"]) // 3)
            existing = c.execute("SELECT xp,streak,last_stream FROM game_viewers WHERE viewer_id=?", (viewer["viewer_id"],)).fetchone()
            if existing:
                streak = int(existing["streak"]) + (1 if existing["last_stream"] != stream_id else 0)
                c.execute("UPDATE game_viewers SET display_name=?,xp=xp+?,streak=?,last_stream=?,last_seen=? WHERE viewer_id=?",
                          (viewer["display_name"],award,streak,stream_id,now(),viewer["viewer_id"]))
            else:
                c.execute("INSERT INTO game_viewers(viewer_id,display_name,xp,streak,last_stream,last_seen) VALUES(?,?,?,1,?,?)",
                          (viewer["viewer_id"],viewer["display_name"],award,stream_id,now()))
        c.execute("UPDATE game_viewers SET title=CASE WHEN xp>=500 THEN 'Lead Investigator' WHEN xp>=200 THEN 'Field Researcher' WHEN xp>=50 THEN 'Signal Analyst' ELSE 'Research Apprentice' END WHERE viewer_id IN (SELECT viewer_id FROM game_action_events WHERE round_id=? AND mode='live')", (row["id"],))


def start(chapter_id=None, mode="live", boss_override=None):
    if mode not in ("live", "simulation"):
        return None, "Unknown encounter mode."
    with lock, db() as c:
        state(c)  # Close expired rounds and award their persisted profile XP.
        active = c.execute("SELECT id FROM game_rounds WHERE status='ACTIVE'").fetchone()
        if active:
            return None, "A science encounter is already active."
        chapter = c.execute("SELECT * FROM chapters ORDER BY ordinal DESC LIMIT 1").fetchone()
        if not chapter or chapter["status"] != "RESOLVED":
            return None, "Resolve the chapter vote before starting its anomaly investigation."
        completed = c.execute("SELECT id FROM game_rounds WHERE chapter_id=? AND mode='live' LIMIT 1", (chapter["id"],)).fetchone()
        if completed:
            return None, "This field study already had its anomaly investigation. Open the next field study to continue."
        if chapter_id and chapter["id"] != chapter_id:
            return None, "That chapter is no longer the latest chapter."
        relic_link = c.execute("SELECT relic_id FROM chapter_relics WHERE chapter_id=?", (chapter["id"],)).fetchone()
        relic = c.execute("SELECT * FROM relics WHERE id=?", (relic_link["relic_id"],)).fetchone() if relic_link else None
        lore_id = chapter["seed_lore_id"]
        lore = c.execute("SELECT boss_name,boss_epithet FROM lore_enrichments WHERE lore_id=?", (lore_id,)).fetchone() if lore_id else None
        fallback_boss, fallback_epithet = "The Unresolved Hypothesis", "A fictional anomaly shaped by approved community lore"
        boss = boss_override or (lore["boss_name"] if lore else fallback_boss)
        epithet = lore["boss_epithet"] if lore else fallback_epithet
        rid, started = str(uuid.uuid4()), now()
        duration = 7.5 if mode == "simulation" else 75
        end_epoch = datetime.now(timezone.utc).timestamp() + duration
        ends = datetime.fromtimestamp(end_epoch, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        initial_charge=min(100,int(relic["charge"])*10) if relic else 0
        initial_guard=min(72,int(relic["wards"])*8) if relic else 0
        c.execute("INSERT INTO game_rounds(id,chapter_id,boss_name,boss_epithet,relic_name,hp,max_hp,charge,guard,status,mode,started_at,ends_at) VALUES(?,?,?,?,?,240,240,?,?,'ACTIVE',?,?,?)",
                  (rid, chapter["id"], boss, epithet, relic["name"] if relic else "Evidence Charm", initial_charge, initial_guard, mode, started, ends))
        result = state(c)
    publish({"kind": "game", "game": result})
    return result, ""


def state(c=None):
    own = c is None
    if own:
        with lock, db() as connection:
            return state(connection)
    row = c.execute("SELECT * FROM game_rounds ORDER BY started_at DESC LIMIT 1").fetchone()
    if not row:
        return {"status": "IDLE", "leaderboard": [], "feed": []}
    if row["status"] == "ACTIVE" and _parse(row["ends_at"]) <= datetime.now(timezone.utc).timestamp():
        outcome = "The anomaly remained unresolved when the observation window closed." if row["hp"] else "Chat resolved the anomaly!"
        _close(c, row, outcome)
        row = c.execute("SELECT * FROM game_rounds WHERE id=?", (row["id"],)).fetchone()
    elapsed = max(0, int(datetime.now(timezone.utc).timestamp() - _parse(row["started_at"])))
    seconds = max(0, int(_parse(row["ends_at"]) - datetime.now(timezone.utc).timestamp())) if row["status"] == "ACTIVE" else 0
    recent = c.execute("SELECT viewer,action,damage,at FROM game_action_events WHERE round_id=? ORDER BY at DESC LIMIT 8", (row["id"],)).fetchall()
    leaderboard = c.execute("SELECT display_name,xp,title,streak FROM game_viewers ORDER BY xp DESC,last_seen DESC LIMIT 8").fetchall()
    action_count = c.execute("SELECT count(*) n FROM game_action_events WHERE round_id=?", (row["id"],)).fetchone()["n"]
    return {"id": row["id"], "chapter_id": row["chapter_id"], "status": row["status"], "mode": row["mode"],
            "boss_name": row["boss_name"], "boss_epithet": row["boss_epithet"], "relic_name": row["relic_name"],
            "hp": int(row["hp"]), "max_hp": int(row["max_hp"]), "charge": int(row["charge"]), "guard": int(row["guard"]),
            "hype": float(row["hype"]), "pressure": int(row["boss_pressure"]), "seconds_left": seconds, "elapsed": elapsed, "actions": action_count,
            "result": row["result"], "feed": [dict(item) for item in recent], "leaderboard": [dict(item) for item in leaderboard]}


def apply_action(event):
    payload = event.get("payload", {})
    text = str(payload.get("text", "")).strip()
    match = re.fullmatch(r"!(scan|strike|attack|shield|guard|calibrate|charge)\s*", text, re.I)
    if not match or event.get("source") not in ("twitch_eventsub", "game_simulation"):
        return None
    action = match.group(1).lower()
    action = {"strike":"scan", "attack":"scan", "guard":"shield", "charge":"calibrate"}.get(action, action)
    viewer_id = str(event.get("viewer_id") or "")
    viewer = str(payload.get("display_name") or "Viewer")[:50]
    at = str(event.get("occurred_at") or now())
    with lock, db() as c:
        if c.execute("SELECT 1 FROM forgotten WHERE viewer_id=?", (viewer_id,)).fetchone():
            return None
        row = c.execute("SELECT * FROM game_rounds WHERE status='ACTIVE' ORDER BY started_at DESC LIMIT 1").fetchone()
        if row and ((row["mode"] == "live" and event.get("source") != "twitch_eventsub") or (row["mode"] == "simulation" and event.get("source") != "game_simulation")):
            return None
        if not row or _parse(row["ends_at"]) <= datetime.now(timezone.utc).timestamp():
            return None
        second = int(_parse(at))
        # One counted move per viewer per second. The unique key also makes Kafka retries safe.
        duplicate = c.execute("SELECT 1 FROM game_action_events WHERE event_id=?", (str(event.get("event_id") or ""),)).fetchone()
        if duplicate:
            return state(c)
        viewer_actions = c.execute("SELECT count(*) n FROM game_action_events WHERE round_id=? AND viewer_id=?", (row["id"],viewer_id)).fetchone()["n"]
        if int(viewer_actions) >= 15:
            return None
        prior = c.execute("SELECT 1 FROM game_action_events WHERE round_id=? AND viewer_id=? AND action_second=?", (row["id"], viewer_id, second)).fetchone()
        if prior:
            return None
        recent = c.execute("SELECT count(*) n FROM events WHERE type='chat.message' AND source='twitch_eventsub' AND at>=?", (datetime.fromtimestamp(second-8, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),)).fetchone()["n"]
        hype = min(2.5, 1.0 + recent / 18.0 + (0.5 if row["ends_at"] and _parse(row["ends_at"]) - second <= 10 else 0))
        damage = 0
        charge, guard = int(row["charge"]), int(row["guard"])
        pressure = int(row["boss_pressure"])
        if action == "scan":
            individual = c.execute("SELECT coalesce(sum(damage),0) n FROM game_action_events WHERE round_id=? AND viewer_id=?", (row["id"], viewer_id)).fetchone()["n"]
            strikes = c.execute("SELECT count(*) n FROM game_action_events WHERE round_id=? AND viewer_id=? AND action='scan'", (row["id"],viewer_id)).fetchone()["n"]
            base_damage = max(1,int(8 * hype) - int(strikes) // 3)
            charge_bonus = min(8,charge//10) if charge>=10 else 0
            damage = min(max(0, int(row["max_hp"] * .1) - int(individual)), base_damage + charge_bonus)
            if charge_bonus: charge=max(0,charge-10)
            pressure = min(100, pressure + 5)
        elif action == "shield":
            guard = min(100, guard + 8)
            pressure = max(0, pressure - 18)
        else:  # calibrate
            charge = min(100, charge + 10)
            pressure = min(100, pressure + 2)
        if pressure >= 100:
            charge = max(0, charge - 10)
            pressure = 25
        action_id = str(event.get("event_id") or uuid.uuid4())
        c.execute("INSERT INTO game_action_events(event_id,round_id,viewer_id,viewer,action,action_second,at,damage,mode) VALUES(?,?,?,?,?,?,?,?,?)",
                  (action_id, row["id"], viewer_id, viewer, action, second, at, damage, row["mode"]))
        hp = max(0, int(row["hp"]) - damage)
        c.execute("UPDATE game_rounds SET hp=?,charge=?,guard=?,boss_pressure=?,hype=? WHERE id=?", (hp, charge, guard, pressure, hype, row["id"]))
        updated = c.execute("SELECT * FROM game_rounds WHERE id=?", (row["id"],)).fetchone()
        if hp <= 0:
            _close(c, updated, f"Chat resolved {row['boss_name']} with the {row['relic_name']}!")
        result = state(c)
    publish({"kind": "game", "game": result})
    return result


def simulate():
    current, error = start(mode="simulation", boss_override="The Null Signal")
    if error: return False, error
    def crowd():
        names = ["ReplayViewer%03d" % index for index in range(1, 201)]
        moves = ("!scan", "!shield", "!calibrate", "!scan")
        for turn in range(40):
            for offset in range(10):
                index = (turn * 10 + offset) % len(names)
                event = {"event_id": "simulation:" + str(uuid.uuid4()), "source": "game_simulation",
                         "occurred_at": now(), "viewer_id": "simulation:" + names[index],
                         "payload": {"display_name": names[index], "text": moves[(turn + offset) % len(moves)]}}
                apply_action(event)
            time.sleep(.075)
    threading.Thread(target=crowd, daemon=True, name="lorekeeper-game-replay").start()
    return True, "A clearly labelled 200-viewer simulation is running."


def forget_viewer(viewer_id):
    with lock, db() as c:
        c.execute("DELETE FROM game_viewers WHERE viewer_id=?", (viewer_id,))
        c.execute("DELETE FROM game_action_events WHERE viewer_id=?", (viewer_id,))

