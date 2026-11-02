"""A Twitch-first, lore-linked semantic guessing encounter."""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone

from . import embeddings, heat, narrator
from .guess_moderation import is_allowed_guess
from .runtime import db, embedding_dimensions, guess_embedding_latency, guess_moderation_rejections, lock, now, publish

_start_lock = threading.Lock()
_WORD = re.compile(r"!guess\s+([a-zA-Z]{2,24})\s*$", re.I)


def init_schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS signal_rounds(
      id TEXT PRIMARY KEY, chapter_id TEXT NOT NULL, lore_id TEXT, boss_name TEXT NOT NULL,
      boss_epithet TEXT NOT NULL, relic_name TEXT NOT NULL, target_word TEXT NOT NULL,
      target_vector TEXT NOT NULL, reference_similarities TEXT NOT NULL DEFAULT '[]',
      reference_count INTEGER NOT NULL DEFAULT 0, vocabulary_fingerprint TEXT NOT NULL DEFAULT '',
      clue TEXT NOT NULL, hints TEXT NOT NULL,
      best_similarity REAL NOT NULL DEFAULT 0, best_guess TEXT, best_viewer TEXT,
      status TEXT NOT NULL, mode TEXT NOT NULL, started_at TEXT NOT NULL, ends_at TEXT NOT NULL,
      closed_at TEXT, result TEXT, solved_by TEXT, solved_by_id TEXT,
      solved INTEGER NOT NULL DEFAULT 0, reveal_text TEXT NOT NULL DEFAULT '',
      scoring_error TEXT, scoring_error_at TEXT, moderation_rejections INTEGER NOT NULL DEFAULT 0,
      input_notice TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS signal_guesses(
      event_id TEXT PRIMARY KEY, round_id TEXT NOT NULL, viewer_id TEXT NOT NULL,
      viewer TEXT NOT NULL, guess TEXT NOT NULL, similarity REAL NOT NULL,
      heat INTEGER NOT NULL, rank INTEGER NOT NULL DEFAULT 0, percentile REAL NOT NULL DEFAULT 0,
      at TEXT NOT NULL, mode TEXT NOT NULL,
      latency_ms REAL NOT NULL DEFAULT 0, improved_best INTEGER NOT NULL DEFAULT 0,
      UNIQUE(round_id,viewer_id,guess))""")
    for index, (table, column) in enumerate((
        ("signal_rounds", "solved_by_id TEXT"), ("signal_rounds", "solved INTEGER NOT NULL DEFAULT 0"),
        ("signal_rounds", "reveal_text TEXT NOT NULL DEFAULT ''"), ("signal_rounds", "scoring_error TEXT"),
        ("signal_rounds", "scoring_error_at TEXT"), ("signal_rounds", "moderation_rejections INTEGER NOT NULL DEFAULT 0"),
        ("signal_rounds", "input_notice TEXT"),
        ("signal_rounds", "reference_similarities TEXT NOT NULL DEFAULT '[]'"),
        ("signal_rounds", "reference_count INTEGER NOT NULL DEFAULT 0"),
        ("signal_rounds", "vocabulary_fingerprint TEXT NOT NULL DEFAULT ''"),
        ("signal_guesses", "rank INTEGER NOT NULL DEFAULT 0"),
        ("signal_guesses", "percentile REAL NOT NULL DEFAULT 0"),
        ("signal_guesses", "latency_ms REAL NOT NULL DEFAULT 0"), ("signal_guesses", "improved_best INTEGER NOT NULL DEFAULT 0"),
    )):
        savepoint = f"signal_column_{index}"
        c.execute(f"SAVEPOINT {savepoint}")
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
            c.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            c.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            c.execute(f"RELEASE SAVEPOINT {savepoint}")
    c.execute("UPDATE signal_rounds SET solved=1 WHERE solved_by IS NOT NULL AND solved=0")


def _epoch(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0


def _band(heat):
    return "BLAZING" if heat >= 88 else "HOT" if heat >= 70 else "WARM" if heat >= 48 else "COOL" if heat >= 28 else "COLD"


def _close(c, row, solved=False, viewer=None, viewer_id=None):
    reveal = str(row["reveal_text"] or f"{row['target_word'].title()} connects to this approved field note.")
    result = (f"{viewer or 'Chat'} solved the signal! {reveal}" if solved
              else f"Time is up. {reveal}")
    c.execute("UPDATE signal_rounds SET status='RESOLVED',closed_at=?,result=?,solved_by=?,solved_by_id=?,solved=? WHERE id=?",
              (now(), result, viewer if solved else None, viewer_id if solved else None, 1 if solved else 0, row["id"]))
    if row["mode"] == "live":
        best_score = c.execute("SELECT MAX(similarity) score FROM signal_guesses WHERE round_id=?", (row["id"],)).fetchone()["score"]
        participants = c.execute("SELECT viewer_id,viewer,MAX(similarity) best,SUM(improved_best) assists FROM signal_guesses WHERE round_id=? GROUP BY viewer_id,viewer", (row["id"],)).fetchall()
        stream = c.execute("SELECT value FROM settings WHERE key='current_stream_id'").fetchone()
        stream_id = stream["value"] if stream else datetime.now(timezone.utc).date().isoformat()
        for person in participants:
            award = 8 + min(12, int(person["assists"] or 0) * 3)
            if best_score is not None and float(person["best"]) >= float(best_score) - 1e-9:
                award += 20
            if solved and person["viewer_id"] == viewer_id:
                award += 25
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
    calibration = heat.reference_warmup_status()
    if not calibration["ready"]:
        if calibration["state"] in ("idle", "error"):
            heat.begin_reference_warmup()
        if calibration["state"] == "error":
            return None, f"Semantic heat calibration failed: {calibration['error']}. A retry has started; check the Lorekeeper logs if it fails again."
        if calibration["state"] == "warming":
            completed, total = calibration.get("completed", 0), calibration.get("total", 0)
            progress = f"{completed:,}/{total:,} reference words" if total else "preparing the reference vocabulary"
            return None, f"Semantic heat calibration is still running ({progress}). Wait for it to finish, then start the Hunt again."
        return None, "Semantic heat calibration is starting. Refresh shortly for progress, then start the Hunt again."
    if not _start_lock.acquire(blocking=False):
        return None, "The Keeper is preparing this signal hunt. Try again in a moment."
    try:
        with lock, db() as c:
            from .game import state as legacy_game_state
            legacy_game_state(c)
            from .story import cancel_missing_memory_chapters
            cancel_missing_memory_chapters(c)
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
            lore = c.execute("SELECT summary,status,evidence FROM lore WHERE id=?", (lore_id,)).fetchone() if lore_id else None
            if not lore or lore["status"] != "ACTIVE":
                return None, "The source observation must be approved before a lore boss can be created."
            enrichment = c.execute("SELECT boss_name,boss_epithet FROM lore_enrichments WHERE lore_id=?", (lore_id,)).fetchone()
            relic_link = c.execute("SELECT relic_id FROM chapter_relics WHERE chapter_id=?", (chapter["id"],)).fetchone()
            relic = c.execute("SELECT name FROM relics WHERE id=?", (relic_link["relic_id"],)).fetchone() if relic_link else None
            from .relics import stream_context as current_stream_context
            context = {"chapter_title": chapter["title"], "stream_context": current_stream_context(c)}
            boss = enrichment["boss_name"] if enrichment else "The Unresolved Hypothesis"
            epithet = enrichment["boss_epithet"] if enrichment else "A fictional anomaly shaped by approved community lore"
            summary = lore["summary"]
            previous_answers=[item["target_word"] for item in c.execute(
                "SELECT target_word FROM signal_rounds WHERE lore_id=? ORDER BY started_at",
                (str(lore_id),)).fetchall()]
            source_texts = []
            try:
                evidence_value = lore["evidence"] or []
                evidence_ids = evidence_value if isinstance(evidence_value, list) else json.loads(evidence_value)
                if evidence_ids:
                    source_rows = c.execute(
                        "SELECT payload FROM events WHERE id IN (%s)" % ",".join("?" for _ in evidence_ids[:8]),
                        tuple(evidence_ids[:8])).fetchall()
                    for source_row in source_rows:
                        payload_value = source_row["payload"] or "{}"
                        payload = payload_value if isinstance(payload_value, dict) else json.loads(payload_value)
                        message = payload.get("text", "")
                        if message:
                            source_texts.append(message)
            except (TypeError, ValueError, KeyError):
                source_texts = []
            relic_name = relic["name"] if relic else "Community Signal Charm"
        try:
            puzzle = narrator.signal_puzzle(summary, relic_name, boss, context, source_texts, previous_answers)
        except Exception as exc:
            return None, f"The Keeper could not prepare a safe puzzle answer: {str(exc)[:160]}"
        try:
            vector = embeddings.embed_semantic(puzzle["target_word"], timeout_seconds=4)
        except Exception as exc:
            return None, f"Semantic embeddings are unavailable: {str(exc)[:180]}. Check the local embedding server, loaded model, and EMBEDDING_DIMENSIONS={embedding_dimensions()}."
        try:
            reference_index = heat.reference_index()
            reference_similarities = heat.reference_distribution(vector, reference_index)
        except Exception as exc:
            return None, f"The Keeper could not calibrate Hunt heat: {str(exc)[:180]}"
        started = now()
        duration = 30 if mode == "simulation" else max(45, min(180, int(__import__('os').environ.get("LOREKEEPER_SIGNAL_SECONDS", "90"))))
        if mode == "live" and chapter["winning_choice"] == "B":
            duration = min(180, duration + 20)
        ends = datetime.fromtimestamp(time.time() + duration, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        with lock, db() as c:
            current = c.execute("SELECT id,status FROM chapters ORDER BY ordinal DESC LIMIT 1").fetchone()
            if not current or current["id"] != chapter["id"] or current["status"] != "RESOLVED":
                return None, "The field study changed while the Keeper prepared the puzzle. Start again."
            rid = str(uuid.uuid4())
            c.execute("INSERT INTO signal_rounds(id,chapter_id,lore_id,boss_name,boss_epithet,relic_name,target_word,target_vector,reference_similarities,reference_count,vocabulary_fingerprint,clue,hints,status,mode,started_at,ends_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',?,?,?)",
                      (rid, chapter["id"], lore_id, boss, epithet, relic_name, puzzle["target_word"], json.dumps(vector),
                       json.dumps(reference_similarities, separators=(",", ":")), len(reference_similarities), reference_index.fingerprint,
                       puzzle["clue"], json.dumps(puzzle["hints"], ensure_ascii=False), mode, started, ends))
            c.execute("UPDATE signal_rounds SET reveal_text=? WHERE id=?", (puzzle.get("reveal", ""), rid))
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
        return {"status": "IDLE", "feed": [], "closest_guesses": [], "leaderboard": []}
    if row["status"] == "ACTIVE" and _epoch(row["ends_at"]) <= time.time():
        _close(c, row)
        row = c.execute("SELECT r.*,ch.winning_choice FROM signal_rounds r LEFT JOIN chapters ch ON ch.id=r.chapter_id WHERE r.id=?", (row["id"],)).fetchone()
    elapsed = max(0, int(time.time() - _epoch(row["started_at"])))
    seconds = max(0, int(_epoch(row["ends_at"]) - time.time())) if row["status"] == "ACTIVE" else 0
    hints = json.loads(row["hints"] or "[]")
    try:
        reference_similarities = json.loads(row["reference_similarities"] or "[]")
    except (TypeError, ValueError):
        reference_similarities = []
    reference_count = int(row["reference_count"] or len(reference_similarities))

    def scored(similarity, exact=False):
        if reference_similarities:
            return heat.score_similarity(float(similarity), reference_similarities, exact=exact)
        return {"heat": 0, "percentile": 0.0, "rank": reference_count + 1}

    stage = min(3, max(0, int(elapsed / max(1, (_epoch(row["ends_at"]) - _epoch(row["started_at"])) / 4))))
    if row["winning_choice"] == "A":
        stage = max(1, stage)
    count = c.execute("SELECT count(*) n FROM signal_guesses WHERE round_id=?", (row["id"],)).fetchone()["n"]
    live_completed = c.execute("SELECT 1 FROM signal_rounds WHERE chapter_id=? AND mode='live' AND status='RESOLVED' LIMIT 1", (row["chapter_id"],)).fetchone()
    feed_rows = c.execute("SELECT viewer,guess,similarity,heat,rank,percentile,at FROM signal_guesses WHERE round_id=? ORDER BY at DESC LIMIT 8", (row["id"],)).fetchall()
    feed = []
    for guess_row in feed_rows:
        item = dict(guess_row)
        if reference_similarities:
            item.update(scored(item["similarity"], item["guess"].casefold() == row["target_word"].casefold()))
        item.pop("similarity", None)
        feed.append(item)
    closest_rows = c.execute("SELECT viewer,guess,similarity,heat,rank,percentile,at FROM signal_guesses WHERE round_id=? ORDER BY similarity DESC,at ASC LIMIT 5", (row["id"],)).fetchall()
    closest_guesses = []
    for guess_row in closest_rows:
        item = dict(guess_row)
        if reference_similarities:
            item.update(scored(item["similarity"], item["guess"].casefold() == row["target_word"].casefold()))
        item.pop("similarity", None)
        closest_guesses.append(item)
    leaderboard = c.execute("SELECT display_name,xp,title,streak FROM game_viewers ORDER BY xp DESC,last_seen DESC LIMIT 8").fetchall()
    round_leaderboard = c.execute("SELECT viewer_id,viewer,MAX(similarity) best_similarity,MAX(CASE WHEN lower(guess)=lower(?) THEN 1 ELSE 0 END) exact_guess,SUM(improved_best) assists FROM signal_guesses WHERE round_id=? GROUP BY viewer_id,viewer ORDER BY best_similarity DESC,assists DESC,viewer ASC LIMIT 8", (row["target_word"], row["id"])).fetchall()
    round_board = [dict(item) for item in round_leaderboard]
    top_similarity = max((float(item["best_similarity"]) for item in round_board), default=-1)
    for item in round_board:
        item["best_heat"] = scored(item["best_similarity"], bool(item["exact_guess"])).get("heat", 0)
        item["is_closest"] = float(item["best_similarity"]) >= top_similarity - 1e-9
        item["xp_projected"] = 8 + min(12, int(item["assists"] or 0) * 3) + (20 if item["is_closest"] else 0) + (25 if row["solved"] and item["viewer_id"] == row["solved_by_id"] else 0)
        item["xp_awarded"] = item["xp_projected"] if row["status"] == "RESOLVED" and row["mode"] == "live" else 0
        item.pop("viewer_id", None)
        item.pop("best_similarity", None)
        item.pop("exact_guess", None)
    latency_rows = c.execute("SELECT latency_ms FROM signal_guesses WHERE round_id=? AND latency_ms>0 ORDER BY latency_ms", (row["id"],)).fetchall()
    latency_p95 = float(latency_rows[max(0, int(len(latency_rows) * .95 + .999999) - 1)]["latency_ms"]) if latency_rows else None
    similarity = float(row["best_similarity"] or 0)
    best_score = scored(similarity, bool(row["best_guess"]) and row["best_guess"].casefold() == row["target_word"].casefold()) if row["best_guess"] else {"heat": 0, "percentile": 0.0, "rank": reference_count + 1}
    heat_value = best_score["heat"]
    solved = row["status"] == "RESOLVED" and bool(row["solved"])
    if solved:
        heat_value = 100
    result = {"id": row["id"], "chapter_id": row["chapter_id"], "status": row["status"], "mode": row["mode"],
              "boss_name": row["boss_name"], "boss_epithet": row["boss_epithet"], "relic_name": row["relic_name"],
              "hp": 100 - heat_value, "max_hp": 100, "seconds_left": seconds, "actions": int(count), "unique_guesses": int(count),
              "clue": row["clue"], "hint": hints[max(0, stage - 1)] if stage and hints else None,
              "hint_stage": stage, "best_guess": row["best_guess"], "best_viewer": row["best_viewer"], "best_heat": heat_value,
              "best_percentile": best_score["percentile"], "best_rank": best_score["rank"], "reference_size": reference_count,
              "best_band": _band(heat_value), "result": row["result"], "live_completed_for_chapter": bool(live_completed), "feed": feed,
              "closest_guesses": closest_guesses,
              "leaderboard": [dict(x) for x in leaderboard], "round_leaderboard": round_board,
              "scoring_error": row["scoring_error"], "moderation_rejections": int(row["moderation_rejections"] or 0),
              "input_notice": row["input_notice"],
              "guess_latency_p95_ms": round(latency_p95, 1) if latency_p95 is not None else None,
              "guess_latency_samples": len(latency_rows), "embedding_mode": embeddings.semantic_mode(),
              "offline_words": embeddings.offline_words() if embeddings.semantic_mode() == "offline_curated" else []}
    if row["status"] == "RESOLVED":
        result["target_word"] = row["target_word"]
        result["solved"] = solved
    return result


def backfill_heat_ranks():
    """Calibrate rounds created before rank heat and refresh stored guess scores."""
    index = heat.reference_index()
    updated_rounds = 0
    updated_guesses = 0
    with lock, db() as c:
        rounds = c.execute("SELECT id,target_word,target_vector,reference_similarities,reference_count,vocabulary_fingerprint FROM signal_rounds").fetchall()
        for row in rounds:
            try:
                distribution = json.loads(row["reference_similarities"] or "[]")
            except (TypeError, ValueError):
                distribution = []
            fingerprint = row["vocabulary_fingerprint"] or index.fingerprint
            if not distribution:
                target = json.loads(row["target_vector"])
                distribution = heat.reference_distribution(target, index)
                fingerprint = index.fingerprint
                c.execute("UPDATE signal_rounds SET reference_similarities=?,reference_count=?,vocabulary_fingerprint=? WHERE id=?",
                          (json.dumps(distribution, separators=(",", ":")), len(distribution), fingerprint, row["id"]))
                updated_rounds += 1
            elif not row["vocabulary_fingerprint"]:
                c.execute("UPDATE signal_rounds SET reference_count=?,vocabulary_fingerprint=? WHERE id=?",
                          (len(distribution), fingerprint, row["id"]))
            guesses = c.execute("SELECT event_id,guess,similarity FROM signal_guesses WHERE round_id=?", (row["id"],)).fetchall()
            for guess in guesses:
                score = heat.score_similarity(float(guess["similarity"]), distribution,
                                              exact=str(guess["guess"]).casefold() == str(row["target_word"]).casefold())
                c.execute("UPDATE signal_guesses SET heat=?,rank=?,percentile=? WHERE event_id=?",
                          (score["heat"], score["rank"], score["percentile"], guess["event_id"]))
                updated_guesses += 1
    return updated_rounds, updated_guesses


def apply_guess(event):
    if event.get("source") not in ("twitch_eventsub", "game_simulation") or event.get("event_type") != "chat.message":
        return None
    payload = event.get("payload") or {}
    match = _WORD.fullmatch(str(payload.get("text", "")).strip())
    if not match:
        return None
    guess = match.group(1).casefold()
    allowed, reason = is_allowed_guess(guess)
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
        if not allowed:
            guess_moderation_rejections.inc()
            c.execute("UPDATE signal_rounds SET moderation_rejections=moderation_rejections+1,input_notice=? WHERE id=?",
                      ("A guess was filtered by the channel safety rules.", row["id"]))
            result = state(c)
            publish({"kind": "game", "game": result})
            return result
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
    score_started = time.perf_counter()
    try:
        vector = embeddings.embed_semantic(guess, timeout_seconds=2)
    except Exception as exc:
        latency = time.perf_counter() - score_started
        guess_embedding_latency.observe(latency)
        if embeddings.semantic_mode() == "offline_curated" and "offline practice vocabulary" in str(exc).casefold():
            notice = "Offline practice accepts only words in the dashboard's curated vocabulary list."
            with lock, db() as c:
                c.execute("UPDATE signal_rounds SET input_notice=? WHERE id=? AND status='ACTIVE'", (notice, row["id"]))
                result = state(c)
            publish({"kind": "game", "game": result})
            return result
        print(f"Could not score semantic guess: {str(exc)[:160]}", flush=True)
        with lock, db() as c:
            c.execute("UPDATE signal_rounds SET scoring_error=?,scoring_error_at=? WHERE id=? AND status='ACTIVE'",
                      ("Signal lost: the local embedding service did not score this guess. Check that it is running and reachable.", now(), row["id"]))
            result = state(c)
        publish({"kind": "game", "game": result})
        return result
    latency = time.perf_counter() - score_started
    try:
        reference_similarities = json.loads(row["reference_similarities"] or "[]")
        if not reference_similarities:
            index = heat.reference_index()
            reference_similarities = heat.reference_distribution(target, index)
            with lock, db() as c:
                c.execute("UPDATE signal_rounds SET reference_similarities=?,reference_count=?,vocabulary_fingerprint=? WHERE id=? AND reference_similarities='[]'",
                          (json.dumps(reference_similarities, separators=(",", ":")), len(reference_similarities), index.fingerprint, row["id"]))
                row = c.execute("SELECT * FROM signal_rounds WHERE id=?", (row["id"],)).fetchone()
        index = heat.reference_index()
        if row["vocabulary_fingerprint"] and row["vocabulary_fingerprint"] != index.fingerprint:
            raise RuntimeError("The embedding model changed after this Hunt started. Finish it and start a new Round to recalibrate.")
        score = heat.score_guess(vector, target, reference_similarities, exact=guess == row["target_word"].casefold())
        similarity = heat.cosine(vector, target)
        if guess == row["target_word"].casefold():
            similarity = 1.0
    except Exception as exc:
        guess_embedding_latency.observe(latency)
        print(f"Could not calibrate semantic guess: {str(exc)[:160]}", flush=True)
        with lock, db() as c:
            c.execute("UPDATE signal_rounds SET scoring_error=?,scoring_error_at=? WHERE id=? AND status='ACTIVE'",
                      ("Signal lost: semantic heat calibration is unavailable. Check the embedding model and reference cache.", now(), row["id"]))
            result = state(c)
        publish({"kind": "game", "game": result})
        return result
    guess_embedding_latency.observe(latency)
    heat_value, rank, percentile = score["heat"], score["rank"], score["percentile"]
    with lock, db() as c:
        current = c.execute("SELECT * FROM signal_rounds WHERE id=? AND status='ACTIVE'", (row["id"],)).fetchone()
        if not current or _epoch(current["ends_at"]) <= time.time():
            return state(c)
        at = now()
        prior = c.execute("SELECT at FROM signal_guesses WHERE round_id=? AND viewer_id=? ORDER BY at DESC LIMIT 1", (row["id"], viewer_id)).fetchone()
        count = int(c.execute("SELECT count(*) n FROM signal_guesses WHERE round_id=? AND viewer_id=?", (row["id"], viewer_id)).fetchone()["n"])
        if (prior and _epoch(at) - _epoch(prior["at"]) < 2) or count >= 30:
            return None
        improved = not current["best_guess"] or similarity > float(current["best_similarity"] or 0)
        try:
            c.execute("INSERT INTO signal_guesses(event_id,round_id,viewer_id,viewer,guess,similarity,heat,rank,percentile,at,mode,latency_ms,improved_best) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (event_id, row["id"], viewer_id, viewer, guess, similarity, heat_value, rank, percentile, at, row["mode"], latency * 1000, int(improved)))
        except Exception:
            return None
        c.execute("UPDATE signal_rounds SET scoring_error=NULL,scoring_error_at=NULL,input_notice=NULL WHERE id=?", (row["id"],))
        if improved:
            c.execute("UPDATE signal_rounds SET best_similarity=?,best_guess=?,best_viewer=? WHERE id=?", (similarity, guess, viewer, row["id"]))
        if guess == row["target_word"].casefold():
            updated = c.execute("SELECT * FROM signal_rounds WHERE id=?", (row["id"],)).fetchone()
            _close(c, updated, True, viewer, viewer_id)
        result = state(c)
    publish({"kind": "game", "game": result})
    return result


def simulate():
    current, error = start(mode="simulation")
    if error:
        return False, error
    # Clearly marked synthetic activity; use only supported words in constrained mode.
    guesses = ["memory", "signal", "science", "charm", "stream", "story", "research", "idea"]
    with lock, db() as c:
        target = c.execute("SELECT target_word FROM signal_rounds WHERE id=?", (current["id"],)).fetchone()["target_word"]
    if embeddings.semantic_mode() == "offline_curated":
        guesses = [word for word in embeddings.offline_words() if word != target]
    def crowd():
        for index in range(200):
            name = f"ReplayResearcher{index + 1:03d}"
            event = {"event_id": "signal-sim:" + str(uuid.uuid4()), "event_type": "chat.message",
                     "source": "game_simulation", "occurred_at": now(), "viewer_id": "simulation:" + name,
                     "payload": {"display_name": name, "text": "!guess " + (target if index == 199 else guesses[index % len(guesses)])}}
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
                c.execute("UPDATE signal_rounds SET solved_by='A community investigator',solved_by_id=NULL,best_viewer=CASE WHEN best_viewer=? THEN 'A community investigator' ELSE best_viewer END,result=replace(result,?,'A community investigator') WHERE id=?", (round_row["solved_by"], round_row["solved_by"], round_row["id"]))
        for display_name in forgotten_names:
            c.execute("UPDATE signal_rounds SET best_viewer='A community investigator' WHERE best_viewer=?", (display_name,))


def forget_lore(c, lore_id):
    rounds = c.execute("SELECT id FROM signal_rounds WHERE lore_id=?", (str(lore_id),)).fetchall()
    for row in rounds:
        c.execute("DELETE FROM signal_guesses WHERE round_id=?", (row["id"],))
        c.execute("DELETE FROM signal_rounds WHERE id=?", (row["id"],))
