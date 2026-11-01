from __future__ import annotations
import json, os, re, uuid
from . import runtime
from . import story
from . import game
from .memory import summarize_lore
from datetime import datetime, timezone
from .runtime import (db, duplicates_total, events_total, lock, now, projection_errors, projection_latency,
                      publish, redis_rate_errors, stats, store_overlay_command,
                      time_cutoff)

RATE_LIMIT_SCRIPT="""
local clock=redis.call('TIME'); local now=clock[1]*1000+math.floor(clock[2]/1000)
local bucket=redis.call('HMGET',KEYS[1],'tokens','at')
local tokens=tonumber(bucket[1]) or tonumber(ARGV[2]); local last=tonumber(bucket[2]) or now
tokens=math.min(tonumber(ARGV[2]),tokens+(now-last)/tonumber(ARGV[1]))
if tonumber(ARGV[3])>0 and redis.call('EXISTS',KEYS[2])==1 then return 0 end
if tokens<1 then redis.call('HSET',KEYS[1],'tokens',tokens,'at',now); redis.call('EXPIRE',KEYS[1],60); return 0 end
redis.call('HSET',KEYS[1],'tokens',tokens-1,'at',now); redis.call('EXPIRE',KEYS[1],60)
if tonumber(ARGV[3])>0 then redis.call('SET',KEYS[2],'1','PX',ARGV[3]) end
return 1
"""

def emit_event(event_type, viewer, text, source="replay", mode="replay", viewer_id=None, payload=None, event_id=None, occurred_at=None):
    viewer_id = str(viewer_id or ("viewer-" + re.sub(r"\W+", "", viewer.lower())[:20]))
    event = {"schema_version": 1, "event_id": event_id or f"{source}:{uuid.uuid4()}", "event_type": event_type,
             "channel_id": os.environ.get("TWITCH_CHANNEL", "demo-channel"), "viewer_id": viewer_id,
             "occurred_at": occurred_at or now(), "received_at": now(), "trace_id": uuid.uuid4().hex[:12], "source": source,
             "mode": mode, "payload": payload or {"display_name": viewer, "text": text}}
    if runtime.event_bus:
        runtime.event_bus.publish(event)
        return event
    return process_event(event)

def process_event(event):
    with projection_latency.time():
        try: return _project_event(event)
        except Exception:
            projection_errors.inc()
            raise

def _project_event(event):
    event_type=event["event_type"]
    viewer=event.get("payload",{}).get("display_name","viewer")
    text=event.get("payload",{}).get("text","")
    viewer_id=str(event.get("viewer_id","unknown"))
    chapter_update=None
    whisper_request=None
    with lock, db() as c:
        if c.execute("SELECT 1 FROM events WHERE id=?", (event["event_id"],)).fetchone():
            duplicates_total.inc()
            command=c.execute("SELECT seq,payload,created_at FROM overlay_commands WHERE command_id=?",(event["event_id"],)).fetchone()
            replay_command=None
            if command:
                try:
                    if (datetime.now(timezone.utc)-datetime.fromisoformat(command["created_at"].replace("Z","+00:00"))).total_seconds()<10:
                        replay_command=json.loads(command["payload"]); replay_command["seq"]=command["seq"]
                except ValueError: pass
            result={"kind":"event","event":event,"reaction":replay_command}
            publish_after_duplicate=True
        else: publish_after_duplicate=False
        if publish_after_duplicate:
            reaction=None
        else:
            is_forgotten=c.execute("SELECT 1 FROM forgotten WHERE viewer_id=?", (viewer_id,)).fetchone() is not None
            if is_forgotten:
                viewer="Viewer"; text="[viewer memory disabled]"
                event={**event,"payload":{"display_name":"Viewer","text":text}}
            c.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)", (event["event_id"], event_type, viewer_id, viewer, text, event.get("source","unknown"), event.get("mode","live"), event["occurred_at"], json.dumps(event["payload"])))
            if event_type=="stream.online" and event.get("source")=="twitch_eventsub":
                stream_id=str(event.get("payload",{}).get("stream_id") or event["event_id"])
                c.execute("INSERT INTO settings(key,value) VALUES('current_stream_id',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(stream_id,))
        is_forgotten = not publish_after_duplicate and is_forgotten
        if not publish_after_duplicate and not is_forgotten:
            c.execute("INSERT INTO viewers VALUES(?,?,?,?,1) ON CONFLICT(viewer_id) DO UPDATE SET display_name=excluded.display_name,last_seen=excluded.last_seen",(viewer_id,viewer,event["occurred_at"],event["occurred_at"]))
            stats["received"] += 1
            events_total.labels(event.get("source","unknown"),event_type).inc()
            if event.get("source") != "load_test":
                # Replay and load profiles exercise projections without adding
                # synthetic chat to the channel's real approved-memory inbox.
                if event.get("source") == "twitch_eventsub":
                    learn_lore(c, viewer_id, viewer, text, event["event_id"])
                detect_moment(c, event_type)
            if event_type=="chat.message" and event.get("mode")=="live":
                chapter_update=story.cast_vote(c,viewer_id,text)
            if (event.get("source")=="twitch_eventsub" and event_type=="chat.message"
                    and text.strip().lower().startswith("!whatdoyouknow")):
                whisper_request=(viewer_id,viewer_status_message(c,viewer_id))
                reaction=None
            else:
                reaction = make_reaction(c, event_type, viewer, text, viewer_id, event["event_id"])
        elif not publish_after_duplicate: reaction = None
    if publish_after_duplicate:
        publish(result)
        if replay_command: publish({"kind":"reaction","reaction":replay_command,"seq":replay_command["seq"]})
        _dispatch_game_action(event)
        return event
    publish({"kind":"event", "event":event, "reaction":reaction})
    if chapter_update: publish({"kind":"chapter", "chapter":chapter_update})
    if reaction: publish({"kind":"reaction", "reaction":reaction})
    if whisper_request:
        from .twitch import enqueue_whisper
        enqueue_whisper(*whisper_request)
    _dispatch_game_action(event)
    return event

def _dispatch_game_action(event):
    if event.get("source") != "twitch_eventsub" or event.get("event_type") != "chat.message": return
    text=str(event.get("payload",{}).get("text","")).strip().lower()
    if not re.fullmatch(r"!guess\s+[a-z]{2,24}", text): return
    if runtime.event_bus:
        try: runtime.event_bus.publish_game_action(event)
        except Exception as exc:
            print(f"Could not append game action to game.actions; applying inline fallback: {exc}",flush=True)
            from .signal_game import apply_guess
            apply_guess(event)
    else:
        from .signal_game import apply_guess
        apply_guess(event)

def learn_lore(c, viewer_id, viewer, text, event_id):
    # Accept the common one-letter omission in "remember" so a mistyped
    # recurrence cue does not silently prevent an otherwise useful Memory.
    corrected = re.sub(r"\bremeber\b", "remember", str(text), flags=re.I)
    clean = corrected.strip().lower()
    if len(clean) < 14 or clean.startswith("!"): return
    if len(clean)>280 or re.search(r"(?:[\w.+-]+@[\w.-]+\.[a-z]{2,}|\b\d{3}[- .]?\d{3}[- .]?\d{4}\b|\b(?:my address|i live at|my real name|my phone|diagnosed|medical condition|social security|password is)\b)",clean,re.I): return
    # Generic recurrence cues work across stream categories and do not assume
    # any particular game, creator, meme, or community.
    signals = ("remember when", "remember that", "again", "every time", "always", "these days", "lately", "keeps coming up", "never forget", "back to")
    if not any(word in clean for word in signals): return
    summary = summarize_lore(corrected)
    old = c.execute("SELECT * FROM lore WHERE lower(summary)=lower(?) AND status!='REJECTED'", (summary,)).fetchone()
    if old:
        evidence = json.loads(old["evidence"]); evidence.append(event_id)
        subjects = list(dict.fromkeys(json.loads(old["subjects"])+[viewer_id]))
        c.execute("UPDATE lore SET mentions=mentions+1,evidence=?,subjects=? WHERE id=?", (json.dumps(evidence),json.dumps(subjects),old["id"]))
        return
    values=(str(uuid.uuid4()),"community_memory",summary,"PENDING",json.dumps([viewer_id]),json.dumps([event_id]),0.74,now(),1)
    if runtime.DATABASE_URL:
        from .embeddings import pgvector_literal
        c.execute("INSERT INTO lore(id,kind,summary,status,subjects,evidence,importance,created_at,mentions,embedding) VALUES(?,?,?,?,?,?,?,?,?,?::vector)",values+(pgvector_literal(summary),))
    else:
        c.execute("INSERT INTO lore(id,kind,summary,status,subjects,evidence,importance,created_at,mentions) VALUES(?,?,?,?,?,?,?,?,?)",values)

def backfill_lore_embeddings():
    if not runtime.DATABASE_URL: return 0
    from .embeddings import pgvector_literal
    with db() as c:
        rows=c.execute("SELECT id,summary FROM lore WHERE embedding IS NULL").fetchall()
        for row in rows:
            c.execute("UPDATE lore SET embedding=?::vector WHERE id=?",(pgvector_literal(row["summary"]),row["id"]))
    return len(rows)

def detect_moment(c, event_type):
    recent = c.execute("SELECT count(*) n FROM events WHERE at>=?",(time_cutoff(12),)).fetchone()["n"]
    last=c.execute("SELECT count(*) n FROM moments WHERE at>=?",(time_cutoff(20),)).fetchone()["n"]
    if not last and (event_type in ("raid.incoming", "subscription", "gift_sub") or recent >= 12):
        title = "Raid incoming" if event_type == "raid.incoming" else ("Community hype spike" if recent >= 12 else "A new community moment")
        c.execute("INSERT INTO moments VALUES(?,?,?,?,?)",(str(uuid.uuid4()),title,"A burst of activity brought chat together.",min(0.99,0.68+recent/100),now()))

def choose_lore(c, viewer=None, viewer_id=None, query_text=None):
    if viewer_id:
        personal=c.execute("SELECT * FROM lore WHERE status='ACTIVE' AND subjects LIKE ? ORDER BY importance DESC,created_at DESC LIMIT 1",(f'%"{viewer_id}"%',)).fetchone()
        if personal: return personal
    if runtime.DATABASE_URL and query_text:
        try:
            from .embeddings import pgvector_literal
            query_vector=pgvector_literal(query_text)
            match=c.execute("SELECT * FROM lore WHERE status='ACTIVE' AND embedding IS NOT NULL ORDER BY embedding <=> ?::vector LIMIT 1",(query_vector,)).fetchone()
            if match: return match
        except Exception as exc:
            print(f"pgvector lore retrieval unavailable; using recency fallback: {exc}",flush=True)
    rows = c.execute("SELECT * FROM lore WHERE status='ACTIVE' ORDER BY importance DESC,created_at DESC LIMIT 30").fetchall()
    return rows[0] if rows else None

def speech_allowed(c,event_type,viewer_id,text):
    cooldowns={"subscription":5000,"gift_sub":5000,"follow":10000,"redemption":5000,"moment.detected":20000,"stream.online":20000,"chat.message":60000,"raid.incoming":0}
    cooldown=cooldowns.get(event_type,0)
    if event_type=="chat.message" and text.lower().startswith("!lore"): cooldown=15000
    if runtime.redis_client:
        try:
            channel=os.environ.get("TWITCH_CHANNEL_ID",runtime.twitch_tokens.get("user_id","local") if runtime.twitch_tokens else "local")
            urgent=event_type=="raid.incoming"
            bucket=f"lorekeeper:rate:{'urgent' if urgent else 'normal'}:{channel}"
            allowed=runtime.redis_client.eval(RATE_LIMIT_SCRIPT,2,bucket,f"lorekeeper:cooldown:{channel}:{event_type}:{viewer_id}",5000 if urgent else 8000,1 if urgent else 2,cooldown)
            return bool(allowed)
        except Exception as exc:
            redis_rate_errors.inc()
            print(f"Redis rate limiter unavailable; using local limiter: {exc}",flush=True)
    if event_type=="raid.incoming":
        recent=c.execute("SELECT count(*) n FROM utterances WHERE trigger='raid.incoming' AND at>=?",(time_cutoff(5),)).fetchone()["n"]
        return recent==0
    recent=c.execute("SELECT count(*) n FROM utterances WHERE at>=?",(time_cutoff(4),)).fetchone()["n"]
    return recent<2

def make_reaction(c, event_type, viewer, text, viewer_id=None, source_event_id=None):
    muted = c.execute("SELECT value FROM settings WHERE key='muted'").fetchone()["value"] == "true"
    if muted: return None
    if text.strip().lower().startswith("!whatdoyouknow"): return None
    trigger = event_type
    line = None
    lore = choose_lore(c, viewer, viewer_id, text)
    if event_type == "raid.incoming": line = f"Welcome, raiders! The hearth has room for everyone."
    elif event_type == "redemption": line = f"A delightful choice, {viewer}. The archive has noticed."
    elif event_type == "cheer": line = f"{viewer} sent some stardust to the stream. Thank you!"
    elif event_type == "stream.online": line = "The archive is open. Chat's next chapter is ready!"
    elif event_type in ("subscription","gift_sub"): line = f"{viewer} just powered the legend. Thank you!"
    elif text.lower().startswith("!lore") and lore: line = f"The archives remember: {summarize_lore(lore['summary'])}"
    if not line and event_type == "follow": line = f"Welcome to the realm, {viewer}!"
    if not line or not speech_allowed(c,event_type,viewer_id or viewer,text): return None
    line = safe_line(line)
    result = {"id":str(uuid.uuid4()),"text":line,"trigger":trigger,"viewer":viewer,"at":now(),"animation":"celebrate" if event_type in ("raid.incoming","subscription","gift_sub","cheer") else "talk"}
    c.execute("INSERT INTO utterances VALUES(?,?,?,?,?)",(result["id"],line,trigger,viewer,result["at"]))
    store_overlay_command(c,result,source_event_id or result["id"])
    stats["reactions"] += 1
    return result

def viewer_status_message(c,viewer_id):
    """Build a private, bounded account summary for Twitch Whisper delivery."""
    personal=c.execute("SELECT summary FROM lore WHERE status='ACTIVE' AND subjects LIKE ? ORDER BY created_at DESC LIMIT 1",
                       (f'%\"{viewer_id}\"%',)).fetchone()
    profile=c.execute("SELECT xp,title,streak FROM game_viewers WHERE viewer_id=?",(str(viewer_id),)).fetchone()
    parts=[]
    if personal:
        parts.append(f"Your saved Memory: {summarize_lore(personal['summary'])}")
    else:
        parts.append("You don't have any saved Memories in Lorekeeper yet.")
    if profile:
        parts.append(f"Your title is {profile['title']}; you have {profile['xp']} XP across a {profile['streak']}-stream streak.")
    else:
        parts.append("You haven't earned a game title or XP yet.")
    return " ".join(parts)[:450]

def safe_line(line):
    line = re.sub(r"https?://\S+|@[\w]+", "", line)
    return " ".join(line.split())[:180]
