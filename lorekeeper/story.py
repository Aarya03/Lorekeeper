"""Persistent, deterministic audience story chapters driven by live Twitch chat."""
from __future__ import annotations

import os
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from . import runtime
from .runtime import db, lock, now, publish
from . import relics
from .memory import summarize_lore


def _encode_choice(choice):
    return json.dumps({"label": choice[0], "detail": choice[1]}, ensure_ascii=False, separators=(",", ":"))


def _decode_choice(value):
    try:
        choice = json.loads(value)
        if isinstance(choice, dict) and isinstance(choice.get("label"), str):
            return choice["label"], str(choice.get("detail", ""))
    except (TypeError, ValueError):
        pass
    # Backward compatibility with chapter rows created before choices had detail text.
    return str(value or ""), ""


def init_schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS chapters (
        id TEXT PRIMARY KEY, ordinal INTEGER NOT NULL, title TEXT NOT NULL,
        seed_lore_id TEXT, seed_lore TEXT NOT NULL, intro TEXT NOT NULL,
        choice_a TEXT NOT NULL, choice_b TEXT NOT NULL, status TEXT NOT NULL,
        started_at TEXT NOT NULL, closes_at TEXT NOT NULL, resolved_at TEXT,
        winning_choice TEXT, votes_a INTEGER NOT NULL DEFAULT 0,
        votes_b INTEGER NOT NULL DEFAULT 0, result TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS chapter_votes (
        chapter_id TEXT NOT NULL, viewer_id TEXT NOT NULL, choice TEXT NOT NULL,
        updated_at TEXT NOT NULL, PRIMARY KEY(chapter_id, viewer_id))""")
def _iso_after(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")


def _counts(c, row):
    if row["status"] == "VOTING":
        totals = c.execute("SELECT choice,count(*) n FROM chapter_votes WHERE chapter_id=? GROUP BY choice", (row["id"],)).fetchall()
        return {item["choice"]: item["n"] for item in totals}
    return {"A": row["votes_a"], "B": row["votes_b"]}


def _outcome_text(c, row):
    counts = _counts(c, row)
    if not counts.get("A", 0) and not counts.get("B", 0):
        return "No one voted this round, so the charm was unchanged. The community memory is still available for another hunt."
    winner = row["winning_choice"]
    if winner == "A":
        if "no charge left" in str(row["result"] or "").casefold():
            return "Chat revealed an early hint. The charm had no charge to spend, but still gained resonance."
        return "Chat revealed an early hint. The charm spent one charge and gained resonance."
    if winner == "B":
        if "already carrying the maximum wards" in str(row["result"] or "").casefold():
            return "Chat kept the first hint hidden and extended the hunt by 20 seconds. The charm is at its ward limit and gained resonance."
        return "Chat kept the first hint hidden and extended the hunt by 20 seconds. The charm gained one ward to protect a future encounter."
    return "The vote tied, so neither path won. The charm kept its charge and gained resonance."


def _chapter_intro():
    return ("This hunt is based on an approved community memory. "
            "Chat can reveal an early hint now or keep it hidden to gain 20 extra seconds.")


def _fallback_title(ordinal, seed):
    clue = re.sub(r"^(?:chat keeps returning to|the community remembers|viewers remember|the stream recalls)\s+", "", summarize_lore(seed), flags=re.I)
    words = [word for word in re.findall(r"[\w'-]+", clue, flags=re.UNICODE)
             if word.casefold().strip("'") not in {"chat", "community", "keeps", "returning", "remembers", "remember", "the", "and", "from", "with", "about"}]
    subject = " ".join(words[:4]).strip().title()
    return f"Field Study {ordinal}: {subject}" if subject else f"Field Study {ordinal}: New Clue"


def _public(c, row):
    if not row:
        return None
    counts = _counts(c, row)
    relic = relics.for_chapter(c, row["id"])
    choice_a, detail_a = _decode_choice(row["choice_a"])
    choice_b, detail_b = _decode_choice(row["choice_b"])
    # Older builds saved INVOKE/GUARD strings into the chapter row. Re-render
    # untouched reveals from the current relic identity so zero-vote chapters
    # don't keep presenting obsolete labels after a relic naming migration.
    if (relic and row["status"] == "RESOLVED" and not counts.get("A", 0)
            and not counts.get("B", 0) and int(relic["resonance"]) == 0):
        use, protect = relics.vote_choices(relic)
        choice_a, detail_a = use
        choice_b, detail_b = protect
    safe_intro = str(row["intro"] or "").replace(str(row["seed_lore"] or ""), summarize_lore(row["seed_lore"]))
    # Older chapter rows stored the entire previous encounter result in front
    # of the new prologue, repeating the same approved observation twice.
    if safe_intro.casefold().startswith("the previous investigation carries into the research notes:"):
        safe_intro = _chapter_intro()
    return {
        "id": row["id"], "ordinal": row["ordinal"], "title": row["title"],
        "seed_lore": summarize_lore(row["seed_lore"]), "intro": safe_intro,
        "choices": {"A": choice_a, "B": choice_b},
        "choice_details": {"A": detail_a, "B": detail_b},
        "status": row["status"], "started_at": row["started_at"],
        "closes_at": row["closes_at"], "resolved_at": row["resolved_at"],
        "winning_choice": row["winning_choice"], "votes": counts,
        "result": _outcome_text(c, row) if row["status"] == "RESOLVED" else row["result"], "relic": relic,
    }


def _resolve(c, row):
    counts = _counts(c, row)
    winner = "A" if counts.get("A", 0) > counts.get("B", 0) else "B" if counts.get("B", 0) > counts.get("A", 0) else "TIE"
    relic_link = c.execute("SELECT relic_id FROM chapter_relics WHERE chapter_id=?", (row["id"],)).fetchone()
    relic_result = None
    if relic_link:
        _, relic_result = relics.resolve_encounter(c, relic_link["relic_id"], winner, sum(counts.values()))
    if relic_result:
        result = relic_result + f" Community clue: {summarize_lore(row['seed_lore'])}"
    elif winner == "A":
        result = f"Chat used the relic and pushed the story forward: {summarize_lore(row['seed_lore'])}"
    elif winner == "B":
        result = f"Chat protected the relic and kept the memory ready for later: {summarize_lore(row['seed_lore'])}"
    else:
        result = f"Chat split the vote, so the Keeper preserves both paths: {summarize_lore(row['seed_lore'])}"
    resolved = now()
    c.execute("UPDATE chapters SET status='RESOLVED',resolved_at=?,winning_choice=?,votes_a=?,votes_b=?,result=? WHERE id=?",
              (resolved, winner, counts.get("A", 0), counts.get("B", 0), result, row["id"]))
    return c.execute("SELECT * FROM chapters WHERE id=?", (row["id"],)).fetchone()


def cancel_missing_memory_chapters(c):
    """Scrub and release chapters whose source Memory has been deleted."""
    orphaned=c.execute("SELECT ch.id,ch.seed_lore_id FROM chapters ch LEFT JOIN lore l ON l.id=ch.seed_lore_id WHERE ch.seed_lore_id IS NOT NULL AND l.id IS NULL AND ch.status!='CANCELLED'").fetchall()
    for chapter in orphaned:
        chapter_id,lore_id=chapter["id"],chapter["seed_lore_id"]
        from .signal_game import forget_lore
        forget_lore(c,lore_id)
        c.execute("DELETE FROM chapter_votes WHERE chapter_id=?",(chapter_id,))
        c.execute("DELETE FROM chapter_relics WHERE chapter_id=?",(chapter_id,))
        c.execute("DELETE FROM game_action_events WHERE round_id IN (SELECT id FROM game_rounds WHERE chapter_id=?)",(chapter_id,))
        c.execute("DELETE FROM game_rounds WHERE chapter_id=?",(chapter_id,))
        c.execute("DELETE FROM relics WHERE lore_id=?",(lore_id,))
        c.execute("UPDATE chapters SET title='Memory withdrawn',seed_lore_id=NULL,seed_lore='Memory removed by viewer request',intro='This Round ended because its source Memory was removed.',choice_a='{}',choice_b='{}',status='CANCELLED',resolved_at=NULL,winning_choice=NULL,votes_a=0,votes_b=0,result='Round cancelled after its source Memory was deleted.' WHERE id=?",(chapter_id,))
    return len(orphaned)


def current_state():
    changed = False
    with lock, db() as c:
        changed=bool(cancel_missing_memory_chapters(c))
        row = c.execute("SELECT * FROM chapters ORDER BY ordinal DESC LIMIT 1").fetchone()
        # Remove an unvoted placeholder chapter created by older builds when
        # there was no approved lore to seed it. A real observation is required
        # before opening the story, so this empty round should not linger.
        if row and row["status"] == "VOTING" and not row["seed_lore_id"]:
            counts = _counts(c, row)
            if not counts.get("A", 0) and not counts.get("B", 0):
                link = c.execute("SELECT relic_id FROM chapter_relics WHERE chapter_id=?", (row["id"],)).fetchone()
                c.execute("DELETE FROM chapter_votes WHERE chapter_id=?", (row["id"],))
                c.execute("DELETE FROM chapter_relics WHERE chapter_id=?", (row["id"],))
                c.execute("DELETE FROM chapters WHERE id=?", (row["id"],))
                if link:
                    relic = c.execute("SELECT lore_id FROM relics WHERE id=?", (link["relic_id"],)).fetchone()
                    if relic and str(relic["lore_id"] or "").startswith("seed:"):
                        c.execute("DELETE FROM relics WHERE id=? AND NOT EXISTS (SELECT 1 FROM chapter_relics WHERE relic_id=?)",
                                  (link["relic_id"], link["relic_id"]))
                row = None
                changed = True
        if row and row["status"] == "VOTING":
            link = c.execute("SELECT relic_id FROM chapter_relics WHERE chapter_id=?", (row["id"],)).fetchone()
            if link:
                relic = relics.get(c, link["relic_id"])
            else:
                relic = relics.ensure_for_lore(c, row["seed_lore_id"], row["seed_lore"]) if row["seed_lore_id"] else relics.ensure_seed(c, row["seed_lore"])
                c.execute("INSERT INTO chapter_relics(chapter_id,relic_id) VALUES(?,?)", (row["id"], relic["id"]))
            use, protect = relics.vote_choices(relic)
            encoded_use, encoded_protect = _encode_choice(use), _encode_choice(protect)
            if row["choice_a"] != encoded_use or row["choice_b"] != encoded_protect:
                c.execute("UPDATE chapters SET choice_a=?,choice_b=? WHERE id=?", (encoded_use, encoded_protect, row["id"]))
                row = c.execute("SELECT * FROM chapters WHERE id=?", (row["id"],)).fetchone()
        if row and row["status"] == "VOTING" and row["closes_at"] <= now():
            row = _resolve(c, row)
            changed = True
        result = _public(c, row)
    if changed:
        publish({"kind": "chapter", "chapter": result})
    return result


def recent_history(limit=5):
    with lock, db() as c:
        rows = c.execute("SELECT * FROM chapters ORDER BY ordinal DESC LIMIT ?", (max(1, min(10, int(limit))),)).fetchall()
        return [_public(c, row) for row in rows]


def start_chapter():
    with lock, db() as c:
        cancel_missing_memory_chapters(c)
        from .game import state as legacy_game_state
        legacy_game_state(c)  # Close any encounter left by an earlier app version.
        boss = c.execute("SELECT id FROM game_rounds WHERE status='ACTIVE' LIMIT 1").fetchone()
        signal_boss = c.execute("SELECT id FROM signal_rounds WHERE status='ACTIVE' LIMIT 1").fetchone()
        if boss or signal_boss:
            return None, "Finish the current anomaly investigation before opening the next field study."
        previous_round = c.execute("SELECT * FROM chapters ORDER BY ordinal DESC LIMIT 1").fetchone()
        if previous_round and previous_round["status"] == "RESOLVED":
            fought = c.execute("SELECT id FROM game_rounds WHERE chapter_id=? AND mode='live' LIMIT 1", (previous_round["id"],)).fetchone()
            signal_fought = c.execute("SELECT id FROM signal_rounds WHERE chapter_id=? AND mode='live' LIMIT 1", (previous_round["id"],)).fetchone()
            if not fought and not signal_fought:
                return None, "Finish this field study's anomaly investigation before opening the next vote."
        active = c.execute("SELECT * FROM chapters WHERE status='VOTING' ORDER BY ordinal DESC LIMIT 1").fetchone()
        if active and active["closes_at"] > now():
            return None, "A chapter vote is already open."
        if active:
            _resolve(c, active)
        memory = c.execute("""SELECT l.id,l.summary FROM lore l WHERE l.status='ACTIVE'
            ORDER BY (SELECT count(*) FROM chapters ch WHERE ch.seed_lore_id=l.id) ASC,
                     COALESCE((SELECT max(ch.ordinal) FROM chapters ch WHERE ch.seed_lore_id=l.id),0) ASC,
                     l.importance DESC,l.created_at ASC LIMIT 1""").fetchone()
        if not memory:
            return None, "Approve a community observation before starting a field study."
        previous = c.execute("SELECT * FROM chapters WHERE status='RESOLVED' ORDER BY ordinal DESC LIMIT 1").fetchone()
        lore_id = memory["id"] if memory else None
        seed = summarize_lore(memory["summary"]).rstrip(".!?")
        relic = relics.ensure_for_lore(c, lore_id, seed) if lore_id else relics.ensure_seed(c, seed)
        relic = relics.retune_for_current_stream(c, relic["id"], seed)
        seconds = max(15, min(180, int(os.environ.get("LOREKEEPER_CHAPTER_VOTE_SECONDS", "45"))))
        chapter_id = str(uuid.uuid4())
        ordinal = c.execute("SELECT COALESCE(MAX(ordinal),0)+1 n FROM chapters").fetchone()["n"]
        intro = _chapter_intro()
        previous_result = ""
        if previous:
            previous_result = (f"Previous chapter: {previous['title']}. Community choice: {previous['winning_choice'] or 'not recorded'}. ")
            previous_signal = c.execute("SELECT target_word,solved FROM signal_rounds WHERE chapter_id=? AND mode='live' ORDER BY started_at DESC LIMIT 1", (previous["id"],)).fetchone()
            if previous_signal:
                outcome = "chat solved it" if previous_signal["solved"] else "time ran out"
                previous_result += f"Previous signal answer: {previous_signal['target_word']}; {outcome}."
        use, protect = relics.vote_choices(relic)
        stream_context = relics.stream_context(c)
        expected_previous_ordinal = ordinal - 1
    # Local inference is deliberately outside the database lock so Twitch event
    # projection and chat actions remain responsive while LM Studio is working.
    from .narrator import chapter_copy
    title, intro, narrator_provider = chapter_copy(seed, relic["name"], previous_result,
                                                   _fallback_title(ordinal, seed), intro, stream_context)
    with lock, db() as c:
        from .game import state as legacy_game_state
        legacy_game_state(c)
        active = c.execute("SELECT * FROM chapters WHERE status='VOTING' ORDER BY ordinal DESC LIMIT 1").fetchone()
        if active and active["closes_at"] > now():
            return None, "A chapter vote was opened while the narrator was preparing this chapter."
        current_ordinal = c.execute("SELECT COALESCE(MAX(ordinal),0) n FROM chapters").fetchone()["n"]
        if int(current_ordinal) != expected_previous_ordinal:
            return None, "The story changed while the narrator was preparing this chapter. Please start again."
        boss = c.execute("SELECT id FROM game_rounds WHERE status='ACTIVE' LIMIT 1").fetchone()
        signal_boss = c.execute("SELECT id FROM signal_rounds WHERE status='ACTIVE' LIMIT 1").fetchone()
        if boss or signal_boss:
            return None, "An anomaly investigation started while the narrator was preparing this field study. Finish it first."
        started = now()
        use, protect = relics.vote_choices(relic)
        c.execute("INSERT INTO chapters(id,ordinal,title,seed_lore_id,seed_lore,intro,choice_a,choice_b,status,started_at,closes_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (chapter_id, ordinal, title, lore_id, seed, intro, _encode_choice(use), _encode_choice(protect), "VOTING", started, _iso_after(seconds)))
        c.execute("INSERT INTO chapter_relics(chapter_id,relic_id) VALUES(?,?)", (chapter_id, relic["id"]))
        row = c.execute("SELECT * FROM chapters WHERE id=?", (chapter_id,)).fetchone()
        result = _public(c, row)
    publish({"kind": "chapter", "chapter": result})
    return result, ""


def cast_vote(c, viewer_id, text):
    match = re.fullmatch(r"\s*!vote\s+([ab])\s*", text or "", re.IGNORECASE)
    if not match or not viewer_id:
        return None
    row = c.execute("SELECT * FROM chapters WHERE status='VOTING' ORDER BY ordinal DESC LIMIT 1").fetchone()
    if not row:
        return None
    if row["closes_at"] <= now():
        return _public(c, _resolve(c, row))
    choice = match.group(1).upper()
    prior = c.execute("SELECT choice FROM chapter_votes WHERE chapter_id=? AND viewer_id=?", (row["id"], viewer_id)).fetchone()
    c.execute("INSERT INTO chapter_votes(chapter_id,viewer_id,choice,updated_at) VALUES(?,?,?,?) ON CONFLICT(chapter_id,viewer_id) DO UPDATE SET choice=excluded.choice,updated_at=excluded.updated_at",
              (row["id"], viewer_id, choice, now()))
    if not prior:
        link = c.execute("SELECT relic_id FROM chapter_relics WHERE chapter_id=?", (row["id"],)).fetchone()
        if link:
            relics.add_resonance(c, link["relic_id"], 5)
    row = c.execute("SELECT * FROM chapters WHERE id=?", (row["id"],)).fetchone()
    return _public(c, row)


def close_chapter():
    with lock, db() as c:
        row = c.execute("SELECT * FROM chapters WHERE status='VOTING' ORDER BY ordinal DESC LIMIT 1").fetchone()
        if not row:
            return None
        result = _public(c, _resolve(c, row))
    publish({"kind": "chapter", "chapter": result})
    return result
